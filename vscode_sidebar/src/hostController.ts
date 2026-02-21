import type * as vscode from "vscode"

export type ConnectionState =
  | { status: "connecting" }
  | {
      status: "connected"
      sessionId?: string
      protocolVersion?: string | null
      engineVersion?: string | null
      lastEventId?: string
      retryCount?: number
    }
  | { status: "error"; sessionId?: string; message: string; retryCount?: number; gapDetected?: boolean }

export type StreamEventEnvelope = {
  id: string
  type: string
  session_id: string
  turn?: number | null
  timestamp?: number
  payload: Record<string, unknown>
}

export type HostSink = {
  onConnection?: (state: ConnectionState) => void | Promise<void>
  onEvents?: (payload: { sessionId: string; events: StreamEventEnvelope[] }) => void | Promise<void>
  onState?: (payload: {
    sessions: Array<{ sessionId: string; status?: string; updatedAt?: number }>
    activeSessionId?: string
  }) => void | Promise<void>
}

const toMessage = (error: unknown): string => {
  if (error instanceof Error) return error.message
  return String(error)
}

const LAST_EVENT_ID_KEY_PREFIX = "breadboardSidebar.lastEventId."
const RETRY_BACKOFF_MS = [250, 1000, 2000, 5000]
const RECENT_EVENT_WINDOW = 512
const ENGINE_TOKEN_SECRET_KEY = "breadboardSidebar.engineToken"

type SidebarConfigLike = {
  engineBaseUrl: string
  defaultConfigPath: string
}

type ExtensionContextLike = Pick<vscode.ExtensionContext, "secrets" | "globalState">

type VSCodeRuntimeLike = {
  workspace: {
    workspaceFolders?: Array<{ uri: unknown }>
    openTextDocument: (arg: { content: string; language?: string }) => Promise<{ uri: unknown }>
  }
  Uri: {
    joinPath: (base: unknown, path: string) => unknown
    file: (path: string) => unknown
  }
  commands: {
    executeCommand: (
      command: string,
      ...args: unknown[]
    ) => Promise<unknown>
  }
}

type HostControllerDeps = {
  fetchFn?: typeof fetch
  setTimeoutFn?: typeof setTimeout
  clearTimeoutFn?: typeof clearTimeout
  retryBackoffMs?: number[]
  batchDelayMs?: number
  configProvider?: () => SidebarConfigLike
  runtimeProvider?: () => VSCodeRuntimeLike
}

type ParsedSseEvent = {
  id?: string
  event?: string
  data?: string
}

const parseSse = (chunkText: string, carry: string): { events: ParsedSseEvent[]; carry: string } => {
  const text = carry + chunkText
  const lines = text.split(/\r?\n/)
  const nextCarry = text.endsWith("\n") ? "" : (lines.pop() ?? "")
  const events: ParsedSseEvent[] = []
  let current: ParsedSseEvent = {}

  const pushCurrent = () => {
    if (!current.data && !current.id && !current.event) return
    events.push(current)
    current = {}
  }

  for (const line of lines) {
    if (line.length === 0) {
      pushCurrent()
      continue
    }
    if (line.startsWith(":")) continue
    const idx = line.indexOf(":")
    const field = idx >= 0 ? line.slice(0, idx) : line
    const rawValue = idx >= 0 ? line.slice(idx + 1) : ""
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue
    if (field === "id") {
      current.id = value
    } else if (field === "event") {
      current.event = value
    } else if (field === "data") {
      current.data = current.data ? `${current.data}\n${value}` : value
    }
  }
  return { events, carry: nextCarry }
}

const toRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.length > 0 ? value : null

const readNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

export class HostController {
  private readonly fetchFn: typeof fetch
  private readonly setTimeoutFn: typeof setTimeout
  private readonly clearTimeoutFn: typeof clearTimeout
  private readonly retryBackoffMs: number[]
  private readonly batchDelayMs: number
  private readonly configProvider: () => SidebarConfigLike
  private readonly runtimeProvider: () => VSCodeRuntimeLike
  private connectionState: ConnectionState = { status: "connecting" }
  private sink: HostSink | null = null
  private activeSessionId: string | null = null
  private streamAbortBySession = new Map<string, AbortController>()
  private eventBatchBySession = new Map<string, StreamEventEnvelope[]>()
  private flushTimerBySession = new Map<string, ReturnType<typeof setTimeout>>()
  private recentEventIdsBySession = new Map<string, string[]>()

  constructor(deps: HostControllerDeps = {}) {
    this.fetchFn = deps.fetchFn ?? fetch
    this.setTimeoutFn = deps.setTimeoutFn ?? setTimeout
    this.clearTimeoutFn = deps.clearTimeoutFn ?? clearTimeout
    this.retryBackoffMs = deps.retryBackoffMs ?? RETRY_BACKOFF_MS
    this.batchDelayMs = deps.batchDelayMs ?? 40
    this.configProvider =
      deps.configProvider ??
      (() => ({
        engineBaseUrl: "http://127.0.0.1:9099",
        defaultConfigPath: "agent_configs/base_v2.yaml",
      }))
    this.runtimeProvider =
      deps.runtimeProvider ??
      (() => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        return require("vscode") as VSCodeRuntimeLike
      })
  }

  private async authHeaders(context: ExtensionContextLike): Promise<Record<string, string> | undefined> {
    const token = await context.secrets.get(ENGINE_TOKEN_SECRET_KEY)
    if (!token) return undefined
    return { Authorization: `Bearer ${token}` }
  }

  private async requestJson<T>(
    context: ExtensionContextLike,
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const cfg = this.configProvider()
    const url = new URL(path, cfg.engineBaseUrl.endsWith("/") ? cfg.engineBaseUrl : `${cfg.engineBaseUrl}/`)
    const headers = {
      ...(await this.authHeaders(context)),
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    }
    const res = await this.fetchFn(url, { ...init, headers })
    if (!res.ok) {
      const text = await res.text().catch(() => "")
      throw new Error(`Request failed: ${res.status} ${text}`.trim())
    }
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  public setSink(sink: HostSink): void {
    this.sink = sink
  }

  public getConnectionState(): ConnectionState {
    return this.connectionState
  }

  public getActiveSessionId(): string | null {
    return this.activeSessionId
  }

  public async checkConnection(context: ExtensionContextLike): Promise<ConnectionState> {
    this.connectionState = { status: "connecting" }
    const cfg = this.configProvider()
    try {
      const url = new URL("/health", cfg.engineBaseUrl.endsWith("/") ? cfg.engineBaseUrl : `${cfg.engineBaseUrl}/`)
      const res = await this.fetchFn(url, {
        headers: await this.authHeaders(context),
      })
      if (!res.ok) {
        this.connectionState = {
          status: "error",
          message: `Engine health check failed: HTTP ${res.status}`,
        }
        return this.connectionState
      }
      const body = (await res.json()) as Record<string, unknown>
      this.connectionState = {
        status: "connected",
        protocolVersion: typeof body.protocol_version === "string" ? body.protocol_version : null,
        engineVersion: typeof body.engine_version === "string" ? body.engine_version : null,
      }
      return this.connectionState
    } catch (error) {
      this.connectionState = {
        status: "error",
        message: `Connection failed: ${toMessage(error)}`,
      }
      return this.connectionState
    }
  }

  private async emitConnection(state: ConnectionState): Promise<void> {
    this.connectionState = state
    await this.sink?.onConnection?.(state)
  }

  private async emitState(context: ExtensionContextLike): Promise<void> {
    const sessions = await this.listSessions(context)
    await this.sink?.onState?.({
      sessions: sessions.map((session) => ({
        sessionId: session.sessionId,
        status: session.status,
        updatedAt: session.updatedAt,
      })),
      activeSessionId: this.activeSessionId ?? undefined,
    })
  }

  private queueEvent(sessionId: string, event: StreamEventEnvelope): void {
    if (this.isDuplicateEvent(sessionId, event.id)) return
    const batch = this.eventBatchBySession.get(sessionId) ?? []
    batch.push(event)
    this.eventBatchBySession.set(sessionId, batch)
    if (this.flushTimerBySession.has(sessionId)) return
    const timer = this.setTimeoutFn(() => {
      void this.flushEvents(sessionId)
    }, this.batchDelayMs)
    this.flushTimerBySession.set(sessionId, timer)
  }

  private isDuplicateEvent(sessionId: string, eventId: string): boolean {
    if (!eventId) return false
    const recent = this.recentEventIdsBySession.get(sessionId) ?? []
    if (recent.includes(eventId)) return true
    recent.push(eventId)
    if (recent.length > RECENT_EVENT_WINDOW) {
      recent.splice(0, recent.length - RECENT_EVENT_WINDOW)
    }
    this.recentEventIdsBySession.set(sessionId, recent)
    return false
  }

  private async flushEvents(sessionId: string): Promise<void> {
    const timer = this.flushTimerBySession.get(sessionId)
    if (timer) {
      this.clearTimeoutFn(timer)
      this.flushTimerBySession.delete(sessionId)
    }
    const batch = this.eventBatchBySession.get(sessionId)
    if (!batch || batch.length === 0) return
    this.eventBatchBySession.set(sessionId, [])
    await this.sink?.onEvents?.({ sessionId, events: batch })
  }

  private normalizeEvent(
    raw: unknown,
    sessionIdFallback: string,
    sseId: string | null,
    syntheticId: string,
  ): StreamEventEnvelope | null {
    const record = toRecord(raw)
    if (!record) return null
    const type = readString(record.type) ?? readString(record.event) ?? readString(record.kind)
    if (!type) return null
    const sessionId = readString(record.session_id) ?? readString(record.sessionId) ?? sessionIdFallback
    const payload = toRecord(record.payload ?? record.data ?? record.body ?? {}) ?? {}
    const id =
      readString(record.id) ??
      readString(record.event_id) ??
      readString(record.eventId) ??
      sseId ??
      syntheticId
    const turn = readNumber(record.turn) ?? readNumber(record.turn_id) ?? readNumber(record.turnId)
    const timestamp =
      readNumber(record.timestamp_ms) ??
      readNumber(record.timestamp) ??
      readNumber(record.ts) ??
      Date.now()
    return {
      id,
      type,
      session_id: sessionId,
      turn: turn ?? null,
      timestamp,
      payload,
    }
  }

  private async readLastEventId(context: ExtensionContextLike, sessionId: string): Promise<string | null> {
    const key = `${LAST_EVENT_ID_KEY_PREFIX}${sessionId}`
    const value = context.globalState.get<string>(key)
    return value ?? null
  }

  private async writeLastEventId(context: ExtensionContextLike, sessionId: string, eventId: string): Promise<void> {
    const key = `${LAST_EVENT_ID_KEY_PREFIX}${sessionId}`
    await context.globalState.update(key, eventId)
  }

  public async listSessions(
    context: ExtensionContextLike,
  ): Promise<Array<{ sessionId: string; status?: string; updatedAt?: number }>> {
    const rows = await this.requestJson<unknown[]>(context, "/sessions")
    const out: Array<{ sessionId: string; status?: string; updatedAt?: number }> = []
    for (const row of rows) {
      const record = toRecord(row)
      if (!record) continue
      const sessionId = readString(record.session_id) ?? readString(record.sessionId)
      if (!sessionId) continue
      const status = readString(record.status) ?? undefined
      const updatedAtRaw =
        readString(record.last_activity_at) ??
        readString(record.updated_at) ??
        readString(record.created_at)
      const updatedAt = updatedAtRaw ? Date.parse(updatedAtRaw) : Date.now()
      out.push({ sessionId, status, updatedAt })
    }
    return out
  }

  public async createSession(context: ExtensionContextLike, task: string): Promise<Record<string, unknown>> {
    const cfg = this.configProvider()
    return await this.requestJson<Record<string, unknown>>(context, "/sessions", {
      method: "POST",
      body: JSON.stringify({
        config_path: cfg.defaultConfigPath,
        task,
      }),
    })
  }

  public async deleteSession(context: ExtensionContextLike, sessionId: string): Promise<void> {
    await this.requestJson<void>(context, `/sessions/${sessionId}`, {
      method: "DELETE",
    })
    if (this.activeSessionId === sessionId) {
      this.stopAllStreams()
      this.activeSessionId = null
    }
    await this.emitState(context)
  }

  public async sendInput(
    context: ExtensionContextLike,
    sessionId: string,
    text: string,
  ): Promise<void> {
    await this.requestJson<void>(context, `/sessions/${sessionId}/input`, {
      method: "POST",
      body: JSON.stringify({ content: text }),
    })
  }

  public async sendCommand(
    context: ExtensionContextLike,
    sessionId: string,
    command: string,
    args: Record<string, unknown> = {},
  ): Promise<void> {
    await this.requestJson<void>(context, `/sessions/${sessionId}/command`, {
      method: "POST",
      body: JSON.stringify({ command, payload: args }),
    })
  }

  public async listFiles(
    context: ExtensionContextLike,
    sessionId: string,
    path: string = ".",
  ): Promise<Array<{ path: string; type: string; size?: number }>> {
    const encoded = encodeURIComponent(path || ".")
    const rows = await this.requestJson<unknown[]>(
      context,
      `/sessions/${sessionId}/files?path=${encoded}`,
    )
    const out: Array<{ path: string; type: string; size?: number }> = []
    for (const row of rows) {
      const rec = toRecord(row)
      if (!rec) continue
      const itemPath = readString(rec.path)
      const itemType = readString(rec.type)
      const itemSize = readNumber(rec.size) ?? undefined
      if (!itemPath || !itemType) continue
      out.push({ path: itemPath, type: itemType, ...(itemSize !== undefined ? { size: itemSize } : {}) })
    }
    return out
  }

  public async readFileSnippet(
    context: ExtensionContextLike,
    sessionId: string,
    path: string,
    options: { headLines?: number; tailLines?: number; maxBytes?: number } = {},
  ): Promise<{ path: string; content: string; truncated: boolean; totalBytes?: number }> {
    const q = new URLSearchParams()
    q.set("path", path)
    q.set("mode", "snippet")
    if (typeof options.headLines === "number") q.set("head_lines", String(options.headLines))
    if (typeof options.tailLines === "number") q.set("tail_lines", String(options.tailLines))
    if (typeof options.maxBytes === "number") q.set("max_bytes", String(options.maxBytes))
    const rec = await this.requestJson<unknown>(context, `/sessions/${sessionId}/files?${q.toString()}`)
    const row = toRecord(rec) ?? {}
    return {
      path: readString(row.path) ?? path,
      content: readString(row.content) ?? "",
      truncated: Boolean(row.truncated),
      ...(readNumber(row.total_bytes) !== null ? { totalBytes: readNumber(row.total_bytes) ?? undefined } : {}),
    }
  }

  public async openDiff(
    context: ExtensionContextLike,
    sessionId: string,
    filePath: string,
    artifactPath?: string,
  ): Promise<void> {
    const runtime = this.runtimeProvider()
    const ws = runtime.workspace.workspaceFolders?.[0]?.uri
    const rightUri = ws ? runtime.Uri.joinPath(ws, filePath) : runtime.Uri.file(filePath)
    let leftUri: unknown

    if (artifactPath && artifactPath.trim().length > 0) {
      const cfg = this.configProvider()
      const base = cfg.engineBaseUrl.endsWith("/") ? cfg.engineBaseUrl : `${cfg.engineBaseUrl}/`
      const url = new URL(`/sessions/${sessionId}/download`, base)
      url.searchParams.set("artifact", artifactPath)
      const response = await this.fetchFn(url, {
        method: "GET",
        headers: await this.authHeaders(context),
      })
      if (!response.ok) {
        const text = await response.text().catch(() => "")
        throw new Error(`Failed to download artifact: HTTP ${response.status} ${text}`.trim())
      }
      const content = await response.text()
      const doc = await runtime.workspace.openTextDocument({
        content,
        language: undefined,
      })
      leftUri = doc.uri
    } else {
      const emptyDoc = await runtime.workspace.openTextDocument({ content: "" })
      leftUri = emptyDoc.uri
    }

    await runtime.commands.executeCommand(
      "vscode.diff",
      leftUri,
      rightUri,
      `BreadBoard Diff: ${filePath}`,
      { preview: true },
    )
  }

  public stopAllStreams(): void {
    for (const abort of this.streamAbortBySession.values()) {
      abort.abort()
    }
    this.streamAbortBySession.clear()
    this.recentEventIdsBySession.clear()
  }

  public async attachToSession(context: ExtensionContextLike, sessionId: string): Promise<void> {
    this.activeSessionId = sessionId
    this.stopAllStreams()
    await this.emitState(context)
    void this.startSessionStream(context, sessionId)
  }

  private async startSessionStream(context: ExtensionContextLike, sessionId: string): Promise<void> {
    const streamAbort = new AbortController()
    this.streamAbortBySession.set(sessionId, streamAbort)
    let retryCount = 0
    let syntheticIdCounter = 0
    while (!streamAbort.signal.aborted) {
      try {
        const cfg = this.configProvider()
        const url = new URL(`/sessions/${sessionId}/events`, cfg.engineBaseUrl.endsWith("/") ? cfg.engineBaseUrl : `${cfg.engineBaseUrl}/`)
        const lastEventId = await this.readLastEventId(context, sessionId)
        await this.emitConnection({
          status: "connecting",
        })
        const headers: Record<string, string> = {
          ...(await this.authHeaders(context)),
          ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
        }
        const response = await this.fetchFn(url, {
          method: "GET",
          headers,
          signal: streamAbort.signal,
        })
        if (response.status === 409) {
          await this.emitConnection({
            status: "error",
            sessionId,
            message: "Resume window exceeded; continuity gap detected.",
            retryCount,
            gapDetected: true,
          })
          return
        }
        if (!response.ok || !response.body) {
          const text = await response.text().catch(() => "")
          throw new Error(`SSE request failed: HTTP ${response.status} ${text}`.trim())
        }
        await this.emitConnection({
          status: "connected",
          sessionId,
          lastEventId: lastEventId ?? undefined,
          retryCount,
        })
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let carry = ""
        try {
          while (!streamAbort.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            if (!value) continue
            const decoded = decoder.decode(value, { stream: true })
            const parsed = parseSse(decoded, carry)
            carry = parsed.carry
            for (const evt of parsed.events) {
              if (!evt.data) continue
              let raw: unknown
              try {
                raw = JSON.parse(evt.data)
              } catch {
                continue
              }
              const normalized = this.normalizeEvent(
                raw,
                sessionId,
                evt.id ?? null,
                `synthetic-${sessionId}-${syntheticIdCounter++}`,
              )
              if (!normalized) continue
              this.queueEvent(sessionId, normalized)
              await this.writeLastEventId(context, sessionId, normalized.id)
            }
          }
        } finally {
          reader.releaseLock()
          await response.body.cancel().catch(() => undefined)
          await this.flushEvents(sessionId)
        }
        retryCount = 0
      } catch (error) {
        if (streamAbort.signal.aborted) return
        await this.emitConnection({
          status: "error",
          sessionId,
          message: `SSE stream error: ${toMessage(error)}`,
          retryCount,
        })
        const waitMs = this.retryBackoffMs[Math.min(retryCount, this.retryBackoffMs.length - 1)]
        retryCount += 1
        await new Promise<void>((resolve) => this.setTimeoutFn(resolve, waitMs))
      }
    }
  }
}
