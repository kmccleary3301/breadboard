import http from "node:http"
import { randomUUID } from "node:crypto"
import { promises as fs } from "node:fs"
import path from "node:path"

export interface ReplayScriptStep {
  readonly delayMs?: number
  readonly event?: Record<string, any>
  readonly awaitCommand?: boolean
  readonly command?: string
  readonly timeoutMs?: number
}

export interface ReplayScriptFileInfo {
  readonly path: string
  readonly type: "file" | "directory"
  readonly size?: number
  readonly updated_at?: string
}

export interface ReplayScriptDocument {
  readonly steps: ReplayScriptStep[]
  readonly files?: Record<string, ReplayScriptFileInfo[]>
  readonly fileContents?: Record<string, string>
  readonly skills?: Record<string, unknown>
  readonly ctreeSnapshot?: Record<string, unknown> | null
}

export interface ReplayServerOptions {
  readonly scriptPath: string
  readonly host?: string
  readonly port?: number
  readonly loop?: boolean
  readonly delayMultiplier?: number
  readonly latencyMs?: number
  readonly jitterMs?: number
  readonly dropRate?: number
  readonly log?: (line: string) => void
}

export interface ReplayServerHandle {
  readonly url: string
  readonly scriptPath: string
  close: () => Promise<void>
}

interface SessionState {
  readonly id: string
  readonly steps: ReplayScriptStep[]
  readonly files?: Record<string, ReplayScriptFileInfo[]>
  readonly fileContents?: Record<string, string>
  readonly skills?: Record<string, unknown>
  readonly ctreeSnapshot?: Record<string, unknown> | null
  playRequested: boolean
  finished: boolean
  playing: boolean
  seqCounter: number
  eventLog: Record<string, any>[]
  clients: Set<http.ServerResponse>
  commandHistory: Array<{ receivedAt: number; body: Record<string, unknown> }>
  commandQueue: Array<{ receivedAt: number; body: Record<string, unknown> }>
  commandWaiters: Array<{
    resolve: (command: { receivedAt: number; body: Record<string, unknown> }) => void
    reject: (error: Error) => void
    command?: string
    timeoutHandle?: NodeJS.Timeout
  }>
}

const DEFAULT_PROTOCOL_VERSION = process.env.BREADBOARD_PROTOCOL_VERSION ?? "1.0"
const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}
const AUTOPLAY_ON_TASK = parseBoolEnv(process.env.BREADBOARD_MOCK_AUTOPLAY_ON_TASK, true)
const sleep = (ms: number) => (ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve())

export const resolveScriptPath = (scriptPath: string) =>
  path.isAbsolute(scriptPath) ? scriptPath : path.join(process.cwd(), scriptPath)

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

const parseFileInfo = (value: unknown): ReplayScriptFileInfo | null => {
  if (!isRecord(value)) return null
  const pathValue = typeof value.path === "string" ? value.path : null
  const typeValue = value.type === "file" || value.type === "directory" ? value.type : null
  if (!pathValue || !typeValue) return null
  return {
    path: pathValue,
    type: typeValue,
    size: typeof value.size === "number" ? value.size : undefined,
    updated_at: typeof value.updated_at === "string" ? value.updated_at : undefined,
  }
}

export const loadReplayScript = async (scriptPath: string): Promise<ReplayScriptDocument> => {
  const contents = await fs.readFile(scriptPath, "utf8")
  const parsed = JSON.parse(contents)
  const hasStepsArray = Array.isArray(parsed) || (isRecord(parsed) && Array.isArray(parsed.steps))
  if (!hasStepsArray) {
    throw new Error("Mock SSE script must be an array of steps, or an object with a \"steps\" array.")
  }
  const rawSteps: unknown[] = Array.isArray(parsed) ? parsed : (parsed.steps as unknown[])

  const files = (() => {
    if (!isRecord(parsed) || !isRecord(parsed.files)) return undefined
    const entries: Record<string, ReplayScriptFileInfo[]> = {}
    for (const [key, value] of Object.entries(parsed.files)) {
      if (!Array.isArray(value)) continue
      const normalized = value.map(parseFileInfo).filter((item): item is ReplayScriptFileInfo => item !== null)
      entries[key] = normalized
    }
    return entries
  })()

  const fileContents = (() => {
    if (!isRecord(parsed) || !isRecord(parsed.fileContents)) return undefined
    const entries: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed.fileContents)) {
      if (typeof value === "string") entries[key] = value
    }
    return entries
  })()

  const skills = (() => {
    if (!isRecord(parsed) || !isRecord(parsed.skills)) return undefined
    return parsed.skills as Record<string, unknown>
  })()

  const ctreeSnapshot = (() => {
    if (!isRecord(parsed)) return undefined
    if (parsed.ctreeSnapshot === null) return null
    if (!isRecord(parsed.ctreeSnapshot)) return undefined
    return parsed.ctreeSnapshot as Record<string, unknown>
  })()

  const steps = rawSteps.map((step) => {
    if (!step || typeof step !== "object") {
      throw new Error("Every mock SSE step must be an object.")
    }
    if (step.awaitCommand === true) {
      const command = typeof step.command === "string" && step.command.trim().length > 0 ? step.command.trim() : undefined
      const timeoutMs = typeof step.timeoutMs === "number" && Number.isFinite(step.timeoutMs) ? step.timeoutMs : undefined
      return {
        delayMs: typeof step.delayMs === "number" ? step.delayMs : 0,
        awaitCommand: true,
        command,
        timeoutMs,
      }
    }
    if (!step.event) {
      throw new Error("Every mock SSE step must include an event payload (or awaitCommand: true).")
    }
    return { delayMs: typeof step.delayMs === "number" ? step.delayMs : 0, event: step.event }
  })
  return { steps, files, fileContents, skills, ctreeSnapshot }
}

const finalizeEvent = (state: SessionState, rawEvent: Record<string, any>) => {
  const rawSeq =
    typeof rawEvent.seq === "number" && Number.isFinite(rawEvent.seq)
      ? rawEvent.seq
      : typeof rawEvent.sequence === "number" && Number.isFinite(rawEvent.sequence)
      ? rawEvent.sequence
      : null
  const rawTimestamp =
    typeof rawEvent.timestamp === "number" && Number.isFinite(rawEvent.timestamp) ? rawEvent.timestamp : null
  const rawTimestampMs =
    typeof rawEvent.timestamp_ms === "number" && Number.isFinite(rawEvent.timestamp_ms) ? rawEvent.timestamp_ms : null
  const timestampMs =
    rawTimestampMs ??
    (rawTimestamp != null
      ? rawTimestamp > 10_000_000_000
        ? rawTimestamp
        : Math.round(rawTimestamp * 1000)
      : Date.now())
  const seq = rawSeq ?? state.seqCounter++
  return {
    id: rawEvent.id ?? randomUUID(),
    type: rawEvent.type ?? "assistant_message",
    session_id: state.id,
    turn: rawEvent.turn ?? 1,
    seq,
    timestamp: timestampMs,
    timestamp_ms: timestampMs,
    protocol_version: rawEvent.protocol_version ?? DEFAULT_PROTOCOL_VERSION,
    data: rawEvent.data ?? rawEvent.payload ?? {},
    payload: rawEvent.payload,
  }
}

const writeSseEvent = (response: http.ServerResponse, event: Record<string, any>) => {
  response.write(`data: ${JSON.stringify(event)}\n\n`)
}

const readJsonBody = async (req: http.IncomingMessage): Promise<Record<string, unknown>> => {
  const chunks: Buffer[] = []
  for await (const chunk of req) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk)
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim()
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {}
    }
    return parsed as Record<string, unknown>
  } catch {
    return {}
  }
}

const enqueueCommand = (state: SessionState, body: Record<string, unknown>) => {
  const entry = { receivedAt: Date.now(), body }
  state.commandHistory.push(entry)
  for (let i = 0; i < state.commandWaiters.length; i += 1) {
    const waiter = state.commandWaiters[i]
    const expected = waiter.command
    const commandValue = typeof body.command === "string" ? body.command : undefined
    if (!expected || (commandValue && commandValue === expected)) {
      if (waiter.timeoutHandle) clearTimeout(waiter.timeoutHandle)
      state.commandWaiters.splice(i, 1)
      waiter.resolve(entry)
      return
    }
  }
  state.commandQueue.push(entry)
}

const waitForCommand = (
  state: SessionState,
  options: { command?: string; timeoutMs?: number },
): Promise<{ receivedAt: number; body: Record<string, unknown> }> => {
  const expected = options.command
  if (state.commandQueue.length > 0) {
    const index = expected
      ? state.commandQueue.findIndex((entry) => entry.body.command === expected)
      : 0
    if (index >= 0) {
      const [match] = state.commandQueue.splice(index, 1)
      return Promise.resolve(match)
    }
  }
  return new Promise((resolve, reject) => {
    const waiter = {
      resolve,
      reject,
      command: expected,
      timeoutHandle: undefined as NodeJS.Timeout | undefined,
    }
    if (typeof options.timeoutMs === "number" && options.timeoutMs > 0) {
      waiter.timeoutHandle = setTimeout(() => {
        const idx = state.commandWaiters.indexOf(waiter)
        if (idx >= 0) state.commandWaiters.splice(idx, 1)
        reject(new Error(`Timed out waiting for command${expected ? ` "${expected}"` : ""}.`))
      }, options.timeoutMs)
    }
    state.commandWaiters.push(waiter)
  })
}

const startPlaybackLoop = async (
  state: SessionState,
  options: Required<Omit<ReplayServerOptions, "scriptPath" | "log">>,
  log?: (line: string) => void,
) => {
  if (state.playing) return
  state.playing = true
  const runOnce = async () => {
    for (const step of state.steps) {
      const baseDelay = Math.max(0, (step.delayMs ?? 0) * options.delayMultiplier)
      if (baseDelay) await sleep(baseDelay)
      const chaosDelay = options.latencyMs + options.jitterMs * Math.random()
      if (chaosDelay) await sleep(chaosDelay)
      if (step.awaitCommand) {
        try {
          log?.(
            `[mock-sse] waiting for command${step.command ? ` "${step.command}"` : ""} (session ${state.id})`,
          )
          const received = await waitForCommand(state, { command: step.command, timeoutMs: step.timeoutMs })
          const commandValue = typeof received.body.command === "string" ? received.body.command : "(unknown)"
          log?.(`[mock-sse] received command "${commandValue}" (session ${state.id})`)
        } catch (error) {
          log?.(`[mock-sse] awaitCommand failed (session ${state.id}): ${(error as Error).message}`)
        }
        continue
      }
      if (options.dropRate > 0 && Math.random() < options.dropRate) {
        log?.(`[mock-sse] dropped event for session ${state.id}`)
        continue
      }
      if (!step.event) continue
      const event = finalizeEvent(state, step.event)
      state.eventLog.push(event)
      if (state.clients.size === 0) continue
      for (const client of state.clients) {
        writeSseEvent(client, event)
      }
    }
  }
  do {
    await runOnce()
  } while (options.loop && state.playRequested)
  state.playing = false
  state.finished = true
  for (const client of state.clients) {
    client.end()
  }
  state.clients.clear()
  log?.(`[mock-sse] finished playback for session ${state.id}`)
}

const tryStartPlayback = (
  state: SessionState,
  options: Required<Omit<ReplayServerOptions, "scriptPath" | "log">>,
  log?: (line: string) => void,
) => {
  if (!state.playRequested || state.playing || state.finished || state.clients.size === 0) {
    return
  }
  void startPlaybackLoop(state, options, log)
}

export const startReplayServer = async (options: ReplayServerOptions): Promise<ReplayServerHandle> => {
  const resolvedScript = resolveScriptPath(options.scriptPath)
  const script = await loadReplayScript(resolvedScript)
  const steps = script.steps
  const host = options.host ?? "127.0.0.1"
  const port = options.port ?? 9191
  const normalized: Required<Omit<ReplayServerOptions, "scriptPath" | "log">> = {
    host,
    port,
    loop: options.loop ?? false,
    delayMultiplier: options.delayMultiplier ?? 1,
    latencyMs: options.latencyMs ?? 0,
    jitterMs: options.jitterMs ?? 0,
    dropRate: options.dropRate ?? 0,
  }
  const sessions = new Map<string, SessionState>()
  const log = options.log

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "", `http://${req.headers.host}`)
    if (req.method === "GET" && url.pathname === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ status: "ok", script: resolvedScript }))
      return
    }
    if (req.method === "POST" && url.pathname === "/sessions") {
      const body = await readJsonBody(req)
      const task = typeof body.task === "string" ? body.task.trim() : ""
      const sessionId = randomUUID()
      const sessionState: SessionState = {
        id: sessionId,
        steps: steps.map((step) => ({ ...step })),
        files: script.files,
        fileContents: script.fileContents,
        skills: script.skills,
        ctreeSnapshot: script.ctreeSnapshot,
        playRequested: false,
        finished: false,
        playing: false,
        seqCounter: 0,
        eventLog: [],
        clients: new Set(),
        commandHistory: [],
        commandQueue: [],
        commandWaiters: [],
      }
      if (task && AUTOPLAY_ON_TASK) {
        sessionState.playRequested = true
        log?.(`[mock-sse] task provided on create (session ${sessionId}) autoplay=${AUTOPLAY_ON_TASK}`)
      }
      sessions.set(sessionId, sessionState)
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ session_id: sessionId, status: "ready", created_at: new Date().toISOString() }))
      log?.(`[mock-sse] session ${sessionId} created`)
      return
    }
    if (req.method === "GET" && url.pathname === "/sessions") {
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(
        JSON.stringify(
          Array.from(sessions.values()).map((session) => ({
            session_id: session.id,
            status: session.finished ? "finished" : session.playing ? "playing" : "ready",
          })),
        ),
      )
      return
    }
    const sessionMatch = url.pathname.match(/^\/sessions\/(.+)$/)
    if (!sessionMatch) {
      res.writeHead(404, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ message: "not found" }))
      return
    }
    const [sessionId, suffix] = sessionMatch[1].split("/")
    const state = sessionId ? sessions.get(sessionId) : null
    if (!state) {
      res.writeHead(404, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ message: "session not found" }))
      return
    }
    if (!suffix) {
      if (req.method === "GET") {
        res.writeHead(200, { "Content-Type": "application/json" })
        res.end(JSON.stringify({ session_id: state.id, status: state.finished ? "finished" : "ready" }))
        return
      }
      if (req.method === "DELETE") {
        sessions.delete(sessionId)
        res.writeHead(204)
        res.end()
        return
      }
      res.writeHead(400, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ message: "invalid session path" }))
      return
    }
    if (suffix === "events" && req.method === "GET") {
      const replay = url.searchParams.get("replay")
      if (replay === "1" || replay === "true") {
        const limitParam = url.searchParams.get("limit")
        const limit = limitParam ? Number(limitParam) : null
        const events = Number.isFinite(limit) && limit != null ? state.eventLog.slice(0, Math.max(0, limit)) : state.eventLog
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          Connection: "keep-alive",
          "Cache-Control": "no-cache",
        })
        res.write("\n")
        for (const event of events) {
          writeSseEvent(res, event)
        }
        state.clients.add(res)
        if (!state.playRequested && !state.finished) {
          state.playRequested = true
        }
        req.on("close", () => {
          state.clients.delete(res)
        })
        tryStartPlayback(state, normalized, log)
        return
      }
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        Connection: "keep-alive",
        "Cache-Control": "no-cache",
      })
      res.write("\n")
      state.clients.add(res)
      if (!state.playRequested && !state.finished) {
        state.playRequested = true
      }
      req.on("close", () => {
        state.clients.delete(res)
      })
      tryStartPlayback(state, normalized, log)
      return
    }
    if (suffix === "files" && req.method === "GET") {
      const requestedPath = url.searchParams.get("path") || "."
      const normalizedPath = requestedPath.trim().length > 0 ? requestedPath.trim() : "."
      const mode = url.searchParams.get("mode")
      if (mode) {
        const content = state.fileContents?.[normalizedPath] ?? ""
        res.writeHead(200, { "Content-Type": "application/json" })
        res.end(JSON.stringify({ path: normalizedPath, content, truncated: false, total_bytes: content.length }))
        return
      }
      const listingKey = normalizedPath === "." ? "." : normalizedPath
      const listing = state.files?.[listingKey] ?? []
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(JSON.stringify(listing))
      return
    }
    if (suffix === "skills" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(
        JSON.stringify(
          state.skills ?? {
            catalog: {},
            selection: null,
            sources: null,
          },
        ),
      )
      return
    }
    if (suffix === "ctrees" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(JSON.stringify(state.ctreeSnapshot ?? {}))
      return
    }
    if (suffix === "input" && req.method === "POST") {
      const body = await readJsonBody(req)
      const content = typeof body.content === "string" ? body.content : null
      if (content && state.clients.size > 0) {
        const event = finalizeEvent(state, { type: "user_message", payload: { text: content } })
        for (const client of state.clients) {
          writeSseEvent(client, event)
        }
      }
      state.playRequested = true
      tryStartPlayback(state, normalized, log)
      res.writeHead(202, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ status: "accepted" }))
      return
    }
    if (suffix === "command" && req.method === "POST") {
      const body = await readJsonBody(req)
      enqueueCommand(state, body)
      log?.(`[mock-sse] command received (session ${state.id}): ${JSON.stringify(body)}`)
      res.writeHead(202, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ status: "accepted" }))
      return
    }
    if (suffix === "commands" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" })
      res.end(
        JSON.stringify({
          session_id: state.id,
          history: state.commandHistory,
          pending: state.commandQueue,
        }),
      )
      return
    }
    if (suffix === "" && req.method === "DELETE") {
      sessions.delete(sessionId)
      res.writeHead(204)
      res.end()
      return
    }
    res.writeHead(404, { "Content-Type": "application/json" })
    res.end(JSON.stringify({ message: "unsupported path" }))
  })

  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error) => {
      server.off("listening", onListening)
      reject(error)
    }
    const onListening = () => {
      server.off("error", onError)
      resolve()
    }
    server.once("error", onError)
    server.once("listening", onListening)
    server.listen(port, host, () => {
      log?.(`[mock-sse] listening at http://${host}:${port}`)
    })
  })

  return {
    url: `http://${host}:${port}`,
    scriptPath: resolvedScript,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error)
          } else {
            resolve()
          }
        })
      }),
  }
}

export const runReplayCli = async (argv: string[]) => {
  const args = [...argv]
  let scriptPath: string | undefined
  let host = "127.0.0.1"
  let port = 9191
  let loop = false
  let delayMultiplier = 1
  let latencyMs = 0
  let jitterMs = 0
  let dropRate = 0
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--script":
        scriptPath = args[++i]
        break
      case "--host":
        host = args[++i]
        break
      case "--port":
        port = Number(args[++i])
        break
      case "--loop":
        loop = true
        break
      case "--delay-multiplier":
        delayMultiplier = Number(args[++i])
        break
      case "--latency-ms":
        latencyMs = Number(args[++i])
        break
      case "--jitter-ms":
        jitterMs = Number(args[++i])
        break
      case "--drop-rate":
        dropRate = Number(args[++i])
        break
      default:
        break
    }
  }
  if (!scriptPath) {
    console.error("Usage: tsx tools/mock/mockSseServer.ts --script scripts/mock_sse_sample.json [--port 9191]")
    process.exit(1)
  }
  const handle = await startReplayServer({
    scriptPath,
    host,
    port,
    loop,
    delayMultiplier,
    latencyMs,
    jitterMs,
    dropRate,
    log: (line) => console.log(line),
  })
  const shutdown = async () => {
    await handle.close().catch((error) => console.error("[mock-sse] shutdown error:", error))
    process.exit(0)
  }
  process.on("SIGINT", shutdown)
  process.on("SIGTERM", shutdown)
  console.log(`[mock-sse] ready on ${handle.url}`)
  console.log(`[mock-sse] script ${handle.scriptPath}`)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  void runReplayCli(process.argv.slice(2))
}
