import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react"
import {
  ApiError,
  createBreadboardClient,
  streamSessionEvents,
  type SessionEvent,
  type SessionFileInfo,
  type SessionSummary,
} from "@breadboard/sdk"
import {
  applyEventToProjection,
  dismissPermissionRequest,
  initialProjectionState,
  PROJECTION_LIMITS,
  type PermissionLedgerRow,
  type PermissionRequestRow,
  type PermissionScope,
  type ProjectionState,
} from "./projection"
import {
  appendSessionEvent,
  EVENT_STORE_LIMITS,
  loadSessionEvents,
  loadSessionSnapshot,
  loadSessionTailEvents,
  saveSessionSnapshot,
} from "./eventStore"
import { runSessionStreamLoop, StaleStreamTimeoutError } from "./sessionStream"
import { buildPermissionDecisionPayload, type PermissionDecision, type PermissionDraft } from "./permissions"
import { computeProjectionHash } from "./projectionHash"
import { buildReplayPackage, parseReplayPackage, serializeReplayPackage } from "./replayPackage"
import { buildSessionDownloadPath } from "./bridgeContracts"
import {
  DEFAULT_BASE_URLS,
  resolveClientAuthToken,
  sanitizeTokenForStorage,
  shouldSendAuthorizationHeader,
  type ConnectionMode,
  type ConnectionSettingsByMode,
  type TokenStoragePolicy,
} from "./connectionPolicy"
import { redactSensitiveValue } from "./redaction"
import { requestCheckpointList, requestCheckpointRestore, requestPermissionRevoke } from "./sessionCommands"
import DiffViewer from "./DiffViewer"
import TaskTreePanel from "./TaskTreePanel"
import { buildSearchEntries, searchEntries, type SearchEntryType } from "./searchIndex"
import { applyPermissionLedgerFilters, type PermissionLedgerFilterState } from "./permissionLedgerFilter"
import { nextSearchResultIndex, prevSearchResultIndex, resolveSearchResultAnchors } from "./searchNavigation"

const MarkdownMessage = lazy(async () => await import("./MarkdownMessage"))

type ConnectionState = "idle" | "connecting" | "streaming" | "retrying" | "gap" | "stopped" | "error"
type ClientMetrics = {
  eventsReceived: number
  eventsApplied: number
  flushCount: number
  maxQueueDepth: number
  staleDrops: number
  lastFlushSize: number
  lastQueueLatencyMs: number
  maxQueueLatencyMs: number
}

const STORAGE_CONNECTION_MODE_KEY = "bb.webapp.connectionMode"
const STORAGE_TOKEN_POLICY_KEY = "bb.webapp.tokenPolicy"
const STORAGE_CONNECTION_SETTINGS_KEY = "bb.webapp.connectionSettings"
const STORAGE_LAST_EVENT_IDS_KEY = "bb.webapp.lastEventIds"
const SESSION_TOKEN_SETTINGS_KEY = "bb.webapp.sessionConnectionSettings"
const DEFAULT_CONFIG_PATH = "agent_configs/base_v2.yaml"
const STREAM_HEARTBEAT_TIMEOUT_MS = 30_000
const SNAPSHOT_INTERVAL_EVENTS = 25
const EVENT_COALESCE_WINDOW_MS = 40
const INITIAL_CLIENT_METRICS: ClientMetrics = {
  eventsReceived: 0,
  eventsApplied: 0,
  flushCount: 0,
  maxQueueDepth: 0,
  staleDrops: 0,
  lastFlushSize: 0,
  lastQueueLatencyMs: 0,
  maxQueueLatencyMs: 0,
}
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const isProjectionState = (value: unknown): value is ProjectionState =>
  isRecord(value) &&
  Array.isArray(value.events) &&
  Array.isArray(value.transcript) &&
  Array.isArray(value.toolRows) &&
  Array.isArray(value.pendingPermissions) &&
  Array.isArray(value.permissionLedger) &&
  Array.isArray(value.checkpoints) &&
  isRecord(value.taskGraph) &&
  Array.isArray((value.taskGraph as Record<string, unknown>).rootIds) &&
  (typeof value.activeAssistantRowId === "string" || value.activeAssistantRowId === null)

const loadLastEventIds = (): Record<string, string> => {
  const raw = localStorage.getItem(STORAGE_LAST_EVENT_IDS_KEY)
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return {}
    const out: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      const text = readString(value)
      if (text) out[key] = text
    }
    return out
  } catch {
    return {}
  }
}

const saveLastEventIds = (value: Record<string, string>): void => {
  localStorage.setItem(STORAGE_LAST_EVENT_IDS_KEY, JSON.stringify(value))
}

const buildApiUrl = (baseUrl: string, path: string): URL =>
  new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)

const formatTimestampForFile = (value: Date): string => value.toISOString().replace(/[:.]/g, "-")
const nowMs = (): number =>
  typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now()

const appendBoundedAudit = <T,>(rows: readonly T[], next: T, max: number): T[] => {
  if (max <= 0) return []
  if (rows.length + 1 <= max) return [...rows, next]
  const drop = rows.length + 1 - max
  return [...rows.slice(drop), next]
}

const formatRuntimeError = (error: unknown): string => {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return `authorization failed (HTTP ${error.status}). verify remote token, mode, and policy.`
  }
  if (error instanceof Error) return error.message
  return String(error)
}

const loadConnectionMode = (): ConnectionMode => {
  const raw = localStorage.getItem(STORAGE_CONNECTION_MODE_KEY)
  if (raw === "local" || raw === "sandbox" || raw === "remote") return raw
  return "local"
}

const loadTokenPolicy = (): TokenStoragePolicy => {
  const raw = localStorage.getItem(STORAGE_TOKEN_POLICY_KEY)
  return raw === "session" ? "session" : "persisted"
}

const loadConnectionSettings = (policy: TokenStoragePolicy): ConnectionSettingsByMode => {
  const fallback: ConnectionSettingsByMode = {
    local: { baseUrl: DEFAULT_BASE_URLS.local, token: "" },
    sandbox: { baseUrl: DEFAULT_BASE_URLS.sandbox, token: "" },
    remote: { baseUrl: DEFAULT_BASE_URLS.remote, token: "" },
  }
  const store = policy === "session" ? sessionStorage : localStorage
  const raw = store.getItem(policy === "session" ? SESSION_TOKEN_SETTINGS_KEY : STORAGE_CONNECTION_SETTINGS_KEY)
  if (!raw) return fallback
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return fallback
    for (const mode of ["local", "sandbox", "remote"] as const) {
      const row = isRecord(parsed[mode]) ? parsed[mode] : {}
      const baseUrl = readString(row.baseUrl) ?? DEFAULT_BASE_URLS[mode]
      const token = readString(row.token) ?? ""
      fallback[mode] = { baseUrl, token }
    }
    return fallback
  } catch {
    return fallback
  }
}

const makeSyntheticEvent = (sessionId: string, type: SessionEvent["type"], payload: Record<string, unknown>): SessionEvent => ({
  id: `synthetic-${type}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  type,
  session_id: sessionId,
  turn: null,
  timestamp: Date.now(),
  payload,
})

export function App() {
  const [connectionMode, setConnectionMode] = useState<ConnectionMode>(() => loadConnectionMode())
  const [tokenStoragePolicy, setTokenStoragePolicy] = useState<TokenStoragePolicy>(() => loadTokenPolicy())
  const [connectionSettings, setConnectionSettings] = useState<ConnectionSettingsByMode>(() =>
    loadConnectionSettings(loadTokenPolicy()),
  )
  const baseUrl = connectionSettings[connectionMode].baseUrl
  const token = connectionSettings[connectionMode].token
  const [configPath, setConfigPath] = useState<string>(DEFAULT_CONFIG_PATH)
  const [task, setTask] = useState<string>("")
  const [message, setMessage] = useState<string>("")
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>("idle")
  const [connectionMessage, setConnectionMessage] = useState<string>("")
  const [projection, setProjection] = useState(initialProjectionState)
  const [currentDir, setCurrentDir] = useState<string>("")
  const [files, setFiles] = useState<SessionFileInfo[]>([])
  const [selectedFilePath, setSelectedFilePath] = useState<string>("")
  const [selectedFileContent, setSelectedFileContent] = useState<string>("")
  const [artifactId, setArtifactId] = useState<string>("")
  const [lastEventIds, setLastEventIds] = useState<Record<string, string>>(() => loadLastEventIds())
  const [busy, setBusy] = useState<boolean>(false)
  const [permissionDrafts, setPermissionDrafts] = useState<Record<string, PermissionDraft>>({})
  const [permissionRulePresets, setPermissionRulePresets] = useState<Record<string, string>>({})
  const [permissionBusyId, setPermissionBusyId] = useState<string | null>(null)
  const [permissionError, setPermissionError] = useState<string>("")
  const [projectionHash, setProjectionHash] = useState<string>("pending")
  const [metrics, setMetrics] = useState<ClientMetrics>(INITIAL_CLIENT_METRICS)
  const [diffViewMode, setDiffViewMode] = useState<"unified" | "side-by-side">("unified")
  const [taskFilters, setTaskFilters] = useState<{ failedOnly: boolean; activeOnly: boolean }>({ failedOnly: false, activeOnly: false })
  const [taskExpanded, setTaskExpanded] = useState<Record<string, boolean>>({})
  const [searchQuery, setSearchQuery] = useState<string>("")
  const [searchType, setSearchType] = useState<SearchEntryType | "all">("all")
  const [searchIndexPosition, setSearchIndexPosition] = useState<number>(-1)
  const [diagnostics, setDiagnostics] = useState<{ status: string; details: string }>({ status: "idle", details: "" })
  const [auditLog, setAuditLog] = useState<Array<{ id: string; at: number; action: string; detail: string }>>([])
  const [permissionLedgerFilter, setPermissionLedgerFilter] = useState<PermissionLedgerFilterState>({
    tool: "",
    scope: "all",
    decision: "all",
  })
  const [revokeError, setRevokeError] = useState<string>("")
  const streamAbortRef = useRef<AbortController | null>(null)
  const streamRunIdRef = useRef<number>(0)
  const lastEventIdsRef = useRef<Record<string, string>>(lastEventIds)
  const replayFileInputRef = useRef<HTMLInputElement | null>(null)
  const snapshotEventCountersRef = useRef<Record<string, number>>({})
  const pendingProjectionEventsRef = useRef<SessionEvent[]>([])
  const pendingProjectionFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingQueueStartedAtRef = useRef<number | null>(null)
  const metricsRef = useRef<ClientMetrics>(INITIAL_CLIENT_METRICS)

  const client = useMemo(
    () =>
      createBreadboardClient({
        baseUrl,
        authToken: resolveClientAuthToken(connectionMode, token),
      }),
    [baseUrl, connectionMode, token],
  )

  const transitionRuntime = useCallback((state: ConnectionState, message: string): void => {
    setConnectionState(state)
    setConnectionMessage(message)
  }, [])

  const appendAudit = useCallback((action: string, detail: string) => {
    setAuditLog((prev) => {
      const next = appendBoundedAudit(prev, { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, at: Date.now(), action, detail }, 300)
      return next
    })
  }, [])

  const setModeBaseUrl = useCallback((value: string) => {
    setConnectionSettings((prev) => ({
      ...prev,
      [connectionMode]: { ...prev[connectionMode], baseUrl: value },
    }))
  }, [connectionMode])

  const setModeToken = useCallback((value: string) => {
    setConnectionSettings((prev) => ({
      ...prev,
      [connectionMode]: { ...prev[connectionMode], token: value },
    }))
  }, [connectionMode])

  useEffect(() => {
    lastEventIdsRef.current = lastEventIds
    saveLastEventIds(lastEventIds)
  }, [lastEventIds])

  useEffect(() => {
    localStorage.setItem(STORAGE_CONNECTION_MODE_KEY, connectionMode)
  }, [connectionMode])

  useEffect(() => {
    localStorage.setItem(STORAGE_TOKEN_POLICY_KEY, tokenStoragePolicy)
    if (tokenStoragePolicy === "persisted") {
      const persistedSettings: ConnectionSettingsByMode = {
        local: { baseUrl: connectionSettings.local.baseUrl, token: sanitizeTokenForStorage("persisted", connectionSettings.local.token) },
        sandbox: { baseUrl: connectionSettings.sandbox.baseUrl, token: sanitizeTokenForStorage("persisted", connectionSettings.sandbox.token) },
        remote: { baseUrl: connectionSettings.remote.baseUrl, token: sanitizeTokenForStorage("persisted", connectionSettings.remote.token) },
      }
      localStorage.setItem(STORAGE_CONNECTION_SETTINGS_KEY, JSON.stringify(persistedSettings))
      sessionStorage.removeItem(SESSION_TOKEN_SETTINGS_KEY)
      return
    }
    const sessionSettings: ConnectionSettingsByMode = {
      local: { baseUrl: connectionSettings.local.baseUrl, token: sanitizeTokenForStorage("session", connectionSettings.local.token) },
      sandbox: { baseUrl: connectionSettings.sandbox.baseUrl, token: sanitizeTokenForStorage("session", connectionSettings.sandbox.token) },
      remote: { baseUrl: connectionSettings.remote.baseUrl, token: connectionSettings.remote.token },
    }
    sessionStorage.setItem(SESSION_TOKEN_SETTINGS_KEY, JSON.stringify(sessionSettings))
  }, [connectionSettings, tokenStoragePolicy])

  useEffect(() => {
    let cancelled = false
    void computeProjectionHash(projection.events)
      .then((hash) => {
        if (!cancelled) setProjectionHash(hash)
      })
      .catch(() => {
        if (!cancelled) setProjectionHash("unavailable")
      })
    return () => {
      cancelled = true
    }
  }, [projection.events])

  useEffect(() => {
    const id = setInterval(() => {
      setMetrics({ ...metricsRef.current })
    }, 500)
    return () => {
      clearInterval(id)
    }
  }, [])

  const flushPendingProjectionEvents = useCallback(() => {
    const batch = pendingProjectionEventsRef.current
    if (batch.length === 0) return
    pendingProjectionEventsRef.current = []
    const startedAt = pendingQueueStartedAtRef.current
    pendingQueueStartedAtRef.current = null
    const queueLatencyMs = startedAt != null ? Math.max(0, nowMs() - startedAt) : 0
    setProjection((prev) => {
      let next = prev
      for (const event of batch) {
        next = applyEventToProjection(next, event)
        const sessionCounter = (snapshotEventCountersRef.current[event.session_id] ?? 0) + 1
        snapshotEventCountersRef.current[event.session_id] = sessionCounter
        if (sessionCounter >= SNAPSHOT_INTERVAL_EVENTS) {
          snapshotEventCountersRef.current[event.session_id] = 0
          void saveSessionSnapshot(event.session_id, {
            projection: next,
            event_count: next.events.length,
            last_event_id: event.id,
          })
        }
      }
      return next
    })
    for (const event of batch) {
      void appendSessionEvent(event.session_id, event)
    }
    metricsRef.current.eventsApplied += batch.length
    metricsRef.current.flushCount += 1
    metricsRef.current.lastFlushSize = batch.length
    metricsRef.current.lastQueueLatencyMs = queueLatencyMs
    metricsRef.current.maxQueueLatencyMs = Math.max(metricsRef.current.maxQueueLatencyMs, queueLatencyMs)
    setMetrics({ ...metricsRef.current })
  }, [])

  const queueProjectionEvent = useCallback(
    (event: SessionEvent) => {
      pendingProjectionEventsRef.current.push(event)
      if (pendingProjectionEventsRef.current.length === 1) {
        pendingQueueStartedAtRef.current = nowMs()
      }
      metricsRef.current.eventsReceived += 1
      metricsRef.current.maxQueueDepth = Math.max(metricsRef.current.maxQueueDepth, pendingProjectionEventsRef.current.length)
      if (pendingProjectionFlushTimerRef.current != null) return
      pendingProjectionFlushTimerRef.current = setTimeout(() => {
        pendingProjectionFlushTimerRef.current = null
        flushPendingProjectionEvents()
      }, EVENT_COALESCE_WINDOW_MS)
    },
    [flushPendingProjectionEvents],
  )

  const stopStreaming = useCallback(() => {
    streamRunIdRef.current += 1
    if (pendingProjectionFlushTimerRef.current != null) {
      clearTimeout(pendingProjectionFlushTimerRef.current)
      pendingProjectionFlushTimerRef.current = null
    }
    flushPendingProjectionEvents()
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
      streamAbortRef.current = null
    }
  }, [flushPendingProjectionEvents])

  const refreshSessions = useCallback(async () => {
    setBusy(true)
    try {
      const rows = await client.listSessions()
      setSessions(rows)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [client, transitionRuntime])

  const checkConnection = useCallback(async () => {
    transitionRuntime("connecting", "checking engine health…")
    try {
      const health = await client.health()
      transitionRuntime(
        activeSessionId ? "streaming" : "idle",
        `connected: protocol=${health.protocol_version ?? "?"}, engine=${health.engine_version ?? health.version ?? "?"}`,
      )
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    }
  }, [activeSessionId, client, transitionRuntime])

  const runDiagnostics = useCallback(async () => {
    setDiagnostics({ status: "running", details: "collecting diagnostics…" })
    try {
      const health = await client.health()
      const statusResponse = await fetch(buildApiUrl(baseUrl, "/status")).then(async (response) => {
        const text = await response.text()
        return { code: response.status, body: text }
      })
      const models = await client.getModelCatalog(configPath)
      const details = [
        `health=${health.status}`,
        `protocol=${health.protocol_version ?? "?"}`,
        `engine=${health.engine_version ?? health.version ?? "?"}`,
        `status_http=${statusResponse.code}`,
        `models=${models.models.length}`,
      ].join(" | ")
      setDiagnostics({ status: "ok", details })
    } catch (error) {
      setDiagnostics({ status: "error", details: formatRuntimeError(error) })
    }
  }, [baseUrl, client, configPath])

  const listFiles = useCallback(
    async (sessionId: string, path: string) => {
      const rows = await client.listSessionFiles(sessionId, path || undefined)
      setFiles(rows)
      setCurrentDir(path)
    },
    [client],
  )

  const openFile = useCallback(
    async (sessionId: string, path: string) => {
      const content = await client.readSessionFile(sessionId, path, {
        mode: "snippet",
        headLines: 220,
        maxBytes: 40_000,
      })
      setSelectedFilePath(path)
      setSelectedFileContent(content.content)
    },
    [client],
  )

  const copyText = useCallback(async (text: string) => {
    if (!text) return
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text)
        transitionRuntime(connectionState, "copied to clipboard")
        return
      }
    } catch {
      // fallback path below
    }
    const input = document.createElement("textarea")
    input.value = text
    document.body.appendChild(input)
    input.select()
    document.execCommand("copy")
    document.body.removeChild(input)
  }, [connectionState, transitionRuntime])

  const jumpToEvent = useCallback((eventId: string) => {
    const element = document.getElementById(`event-${eventId}`)
    if (!element) return
    element.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [])

  const parentDir = useMemo(() => {
    if (!currentDir) return ""
    const normalized = currentDir.replace(/\/+$/, "")
    const idx = normalized.lastIndexOf("/")
    if (idx <= 0) return ""
    return normalized.slice(0, idx)
  }, [currentDir])

  const searchIndex = useMemo(
    () =>
      buildSearchEntries({
        transcript: projection.transcript,
        toolRows: projection.toolRows,
        events: projection.events,
        limits: {
          transcript: PROJECTION_LIMITS.transcript,
          toolRows: PROJECTION_LIMITS.toolRows,
          events: PROJECTION_LIMITS.events,
        },
      }),
    [projection.events, projection.toolRows, projection.transcript],
  )

  const searchResults = useMemo(() => searchEntries(searchIndex, searchQuery, { type: searchType }), [searchIndex, searchQuery, searchType])

  const filteredLedger = useMemo(
    () => applyPermissionLedgerFilters(projection.permissionLedger, permissionLedgerFilter),
    [permissionLedgerFilter, projection.permissionLedger],
  )

  useEffect(() => {
    void checkConnection()
    void refreshSessions()
    return () => {
      stopStreaming()
    }
  }, [checkConnection, refreshSessions, stopStreaming])

  const streamLoop = useCallback(
    async (sessionId: string, signal: AbortSignal, runId: number) => {
      const isActiveRun = (): boolean => streamRunIdRef.current === runId && !signal.aborted
      await runSessionStreamLoop({
        sessionId,
        signal,
        getLastEventId: () => lastEventIdsRef.current[sessionId],
        stream: ({ lastEventId, replay, fromId }) =>
          streamSessionEvents(sessionId, {
            config: { baseUrl, authToken: resolveClientAuthToken(connectionMode, token) },
            signal,
            lastEventId,
            query: replay && fromId ? { replay: true, from_id: fromId } : undefined,
          }),
        onConnecting: () => {
          if (!isActiveRun()) return
          transitionRuntime("connecting", `stream connecting: ${sessionId}`)
        },
        onEvent: (event) => {
          if (!isActiveRun()) {
            metricsRef.current.staleDrops += 1
            return
          }
          queueProjectionEvent(event)
          setLastEventIds((prev) => ({ ...prev, [sessionId]: event.id }))
        },
        onConnected: () => {
          if (!isActiveRun()) return
          transitionRuntime("streaming", `stream connected: ${sessionId}`)
        },
        onRetryError: (error, waitMs) => {
          if (!isActiveRun()) return
          const reason =
            error instanceof StaleStreamTimeoutError
              ? `stream heartbeat timeout (${error.timeoutMs}ms)`
              : error instanceof Error
                ? error.message
                : String(error)
          transitionRuntime(
            "retrying",
            `stream error (${reason}), retrying in ${waitMs}ms`,
          )
        },
        onReplayCatchupStart: ({ fromId, expectedSeq, actualSeq }) => {
          if (!isActiveRun()) return
          transitionRuntime(
            "retrying",
            `sequence gap (${expectedSeq}->${actualSeq}); attempting replay catch-up from ${fromId}`,
          )
        },
        onResumeWindowGap: () => {
          if (!isActiveRun()) return
          transitionRuntime("gap", "resume window exceeded (HTTP 409). refresh session state and re-attach.")
        },
        onSequenceGap: ({ expectedSeq, actualSeq }) => {
          if (!isActiveRun()) return
          transitionRuntime("gap", `event sequence gap detected: expected seq ${expectedSeq}, got ${actualSeq}`)
        },
        heartbeatTimeoutMs: STREAM_HEARTBEAT_TIMEOUT_MS,
      })
      if (streamRunIdRef.current === runId && streamAbortRef.current?.signal === signal) {
        streamAbortRef.current = null
      }
      flushPendingProjectionEvents()
    },
    [baseUrl, connectionMode, token, transitionRuntime, queueProjectionEvent, flushPendingProjectionEvents],
  )

  const attachSession = useCallback(
    async (sessionId: string) => {
      stopStreaming()
      setActiveSessionId(sessionId)
      setProjection(initialProjectionState)
      setPermissionDrafts({})
      setPermissionBusyId(null)
      setPermissionError("")
      setCurrentDir("")
      setFiles([])
      setSelectedFilePath("")
      setSelectedFileContent("")
      setArtifactId("")
      try {
        let seeded = initialProjectionState
        let seededLastEventId: string | undefined

        const snapshot = await loadSessionSnapshot<ProjectionState>(sessionId)
        if (snapshot && isProjectionState(snapshot.projection)) {
          seeded = snapshot.projection
          seededLastEventId = snapshot.last_event_id ?? seeded.events.at(-1)?.id
        }

        const cachedEvents =
          seededLastEventId != null
            ? await loadSessionTailEvents(sessionId, seededLastEventId, PROJECTION_LIMITS.events)
            : await loadSessionEvents(sessionId, PROJECTION_LIMITS.events)

        if (cachedEvents.length > 0) {
          seeded = cachedEvents.reduce(applyEventToProjection, seeded)
          seededLastEventId = cachedEvents.at(-1)?.id
        }

        if (seeded.events.length > 0) {
          setProjection(seeded)
          if (seededLastEventId) {
            setLastEventIds((prev) => ({ ...prev, [sessionId]: seededLastEventId }))
          }
          void saveSessionSnapshot(sessionId, {
            projection: seeded,
            event_count: seeded.events.length,
            last_event_id: seededLastEventId ?? null,
          })
        }
      } catch {
        // Cache hydration is best-effort.
      }
      try {
        await listFiles(sessionId, "")
      } catch {
        // File listing is optional in early bootstrap; stream attach should still proceed.
      }
      try {
        const ctree = await client.getCtreeSnapshot(sessionId)
        if (ctree && typeof ctree === "object") {
          setProjection((prev) => applyEventToProjection(prev, makeSyntheticEvent(sessionId, "ctree_snapshot", ctree as Record<string, unknown>)))
        }
      } catch {
        // ctree is best-effort in early attach.
      }
      try {
        await requestCheckpointList(async (command, payload) => await client.postCommand(sessionId, { command, payload }))
      } catch {
        // checkpoint list is optional; real updates can still arrive via events.
      }
      const abort = new AbortController()
      streamAbortRef.current = abort
      streamRunIdRef.current += 1
      const runId = streamRunIdRef.current
      transitionRuntime("connecting", `stream connecting: ${sessionId}`)
      void streamLoop(sessionId, abort.signal, runId)
    },
    [client, listFiles, stopStreaming, streamLoop, transitionRuntime],
  )

  const createSession = useCallback(async () => {
    if (!task.trim()) return
    setBusy(true)
    try {
      const created = await client.createSession({
        config_path: configPath,
        task: task.trim(),
      })
      setTask("")
      await refreshSessions()
      await attachSession(created.session_id)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [attachSession, client, configPath, refreshSessions, task, transitionRuntime])

  const sendMessage = useCallback(async () => {
    if (!activeSessionId || !message.trim()) return
    setBusy(true)
    try {
      await client.postInput(activeSessionId, { content: message.trim() })
      setMessage("")
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, client, message, transitionRuntime])

  const stopSession = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      await client.postCommand(activeSessionId, { command: "stop" })
      appendAudit("stop", `session=${activeSessionId}`)
      transitionRuntime("stopped", `stop requested: ${activeSessionId}`)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, appendAudit, client, transitionRuntime])

  const refreshCheckpoints = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      await requestCheckpointList(async (command, payload) => await client.postCommand(activeSessionId, { command, payload }))
      appendAudit("checkpoint.list", `session=${activeSessionId}`)
      transitionRuntime(connectionState, `requested checkpoints for ${activeSessionId}`)
    } catch (error) {
      transitionRuntime("error", `checkpoint list failed: ${formatRuntimeError(error)}`)
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, appendAudit, client, connectionState, transitionRuntime])

  const restoreCheckpoint = useCallback(async () => {
    if (!activeSessionId || !projection.activeCheckpointId) return
    const confirmed = window.confirm(
      `Restore checkpoint '${projection.activeCheckpointId}'?\n\nThis rewinds session state and will reconnect the stream.`,
    )
    if (!confirmed) return
    setBusy(true)
    setProjection((prev) => ({ ...prev, checkpointRestoreInFlight: true }))
    try {
      await requestCheckpointRestore(
        async (command, payload) => await client.postCommand(activeSessionId, { command, payload }),
        projection.activeCheckpointId,
      )
      appendAudit("checkpoint.restore", `session=${activeSessionId} checkpoint=${projection.activeCheckpointId}`)
      transitionRuntime("connecting", `checkpoint restore requested: ${projection.activeCheckpointId}`)
      await attachSession(activeSessionId)
    } catch (error) {
      setProjection((prev) => ({ ...prev, checkpointRestoreInFlight: false }))
      transitionRuntime("error", `checkpoint restore failed: ${formatRuntimeError(error)}`)
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, appendAudit, attachSession, client, projection.activeCheckpointId, transitionRuntime])

  const updatePermissionDraft = useCallback((requestId: string, patch: Partial<PermissionDraft>) => {
    setPermissionDrafts((prev) => {
      const base = prev[requestId] ?? { note: "", rule: "", scope: "project" as PermissionScope }
      return {
        ...prev,
        [requestId]: { ...base, ...patch },
      }
    })
  }, [])

  const resolvePermissionDraft = useCallback(
    (request: PermissionRequestRow): PermissionDraft => {
      const existing = permissionDrafts[request.requestId]
      if (existing) return existing
      return {
        note: "",
        rule: request.ruleSuggestion ?? permissionRulePresets[request.tool] ?? "",
        scope: request.defaultScope,
      }
    },
    [permissionDrafts, permissionRulePresets],
  )

  const submitPermissionDecision = useCallback(
    async (request: PermissionRequestRow, decision: PermissionDecision) => {
      if (!activeSessionId) return
      const draft = resolvePermissionDraft(request)
      const payload = buildPermissionDecisionPayload(request, draft, decision)

      setPermissionBusyId(request.requestId)
      setPermissionError("")
      try {
        await client.postCommand(activeSessionId, {
          command: "permission_decision",
          payload,
        })
        appendAudit("permission.decision", `${request.requestId} -> ${decision}`)
        if ((decision === "allow-always" || decision === "deny-always") && draft.rule.trim().length > 0) {
          setPermissionRulePresets((prev) => ({ ...prev, [request.tool]: draft.rule.trim() }))
        }
        setProjection((prev) => dismissPermissionRequest(prev, request.requestId))
        setPermissionDrafts((prev) => {
          const next = { ...prev }
          delete next[request.requestId]
          return next
        })
      } catch (error) {
        setPermissionError(formatRuntimeError(error))
      } finally {
        setPermissionBusyId(null)
      }
    },
    [activeSessionId, appendAudit, client, resolvePermissionDraft],
  )

  const onFileClick = useCallback(
    async (entry: SessionFileInfo) => {
      if (!activeSessionId) return
      if (entry.type === "directory") {
        await listFiles(activeSessionId, entry.path)
        return
      }
      await openFile(activeSessionId, entry.path)
    },
    [activeSessionId, listFiles, openFile],
  )

  const revokePermission = useCallback(
    async (entry: PermissionLedgerRow) => {
      if (!activeSessionId) return
      setRevokeError("")
      setBusy(true)
      try {
        await requestPermissionRevoke(
          async (command, payload) => await client.postCommand(activeSessionId, { command, payload }),
          {
            requestId: entry.requestId,
            tool: entry.tool,
            scope: entry.scope,
            rule: entry.rule,
          },
        )
        appendAudit("permission.revoke", `${entry.requestId} (${entry.tool})`)
        setProjection((prev) => ({
          ...prev,
          permissionLedger: prev.permissionLedger.map((row) =>
            row.requestId === entry.requestId ? { ...row, revoked: true, decision: "revoke", timestamp: Date.now() } : row,
          ),
        }))
      } catch (error) {
        setRevokeError(`revoke unavailable or failed: ${formatRuntimeError(error)}`)
      } finally {
        setBusy(false)
      }
    },
    [activeSessionId, appendAudit, client],
  )

  const refreshCurrentDir = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      await listFiles(activeSessionId, currentDir)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, currentDir, listFiles, transitionRuntime])

  const openArtifact = useCallback(async () => {
    if (!activeSessionId || !artifactId.trim()) return
    setBusy(true)
    try {
      const path = buildSessionDownloadPath(activeSessionId)
      const url = buildApiUrl(baseUrl, path)
      url.searchParams.set("artifact", artifactId.trim())
      const response = await fetch(url, {
        headers:
          shouldSendAuthorizationHeader(connectionMode) && token.trim().length > 0
            ? { Authorization: `Bearer ${token.trim()}` }
            : undefined,
      })
      if (!response.ok) {
        throw new Error(`artifact download failed: HTTP ${response.status}`)
      }
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = objectUrl
      link.download = artifactId.trim()
      link.click()
      URL.revokeObjectURL(objectUrl)
      appendAudit("artifact.download", `session=${activeSessionId} artifact=${artifactId.trim()}`)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, appendAudit, artifactId, baseUrl, connectionMode, token, transitionRuntime])

  const exportReplay = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      const cachedEvents = await loadSessionEvents(activeSessionId, EVENT_STORE_LIMITS.maxEventsPerSession)
      const liveEvents = projection.events.filter((event) => event.session_id === activeSessionId)
      const sourceEvents = cachedEvents.length > 0 ? cachedEvents : liveEvents
      if (sourceEvents.length === 0) {
        throw new Error("no local events to export for this session")
      }
      const redactedEvents = sourceEvents.map((event) => ({
        ...event,
        payload: redactSensitiveValue(event.payload) as SessionEvent["payload"],
      }))
      const hash = await computeProjectionHash(redactedEvents)
      const replay = buildReplayPackage({
        sessionId: activeSessionId,
        events: redactedEvents,
        projectionHash: hash,
      })
      const blob = new Blob([serializeReplayPackage(replay)], { type: "application/json" })
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = objectUrl
      link.download = `bb-replay-${activeSessionId}-${formatTimestampForFile(new Date())}.json`
      link.click()
      URL.revokeObjectURL(objectUrl)
      appendAudit("replay.export", `session=${activeSessionId} events=${replay.events.length}`)
      transitionRuntime("idle", `replay exported: ${replay.events.length} events for ${activeSessionId}`)
    } catch (error) {
      transitionRuntime("error", formatRuntimeError(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, appendAudit, projection.events, transitionRuntime])

  const onImportReplayFile = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      event.target.value = ""
      if (!file) return

      setBusy(true)
      try {
        const replay = parseReplayPackage(await file.text())
        stopStreaming()
        setActiveSessionId(replay.session_id)
        const seeded = replay.events.reduce(applyEventToProjection, initialProjectionState)
        setProjection(seeded)
        setPermissionDrafts({})
        setPermissionBusyId(null)
        setPermissionError("")
        setCurrentDir("")
        setFiles([])
        setSelectedFilePath("")
        setSelectedFileContent("")
        setArtifactId("")

        for (const row of replay.events) {
          await appendSessionEvent(replay.session_id, row)
        }

        const lastEventId = replay.events.at(-1)?.id
        if (lastEventId) {
          setLastEventIds((prev) => ({ ...prev, [replay.session_id]: lastEventId }))
        }
        void saveSessionSnapshot(replay.session_id, {
          projection: seeded,
          event_count: seeded.events.length,
          last_event_id: lastEventId ?? null,
        })

        setSessions((prev) => {
          if (prev.some((row) => row.session_id === replay.session_id)) return prev
          return [
            {
              session_id: replay.session_id,
              status: "replay-imported",
              created_at: replay.exported_at,
              last_activity_at: replay.exported_at,
            },
            ...prev,
          ]
        })

        appendAudit("replay.import", `session=${replay.session_id} events=${replay.events.length}`)
        transitionRuntime("stopped", `replay imported: ${replay.events.length} events for ${replay.session_id}`)
      } catch (error) {
        transitionRuntime("error", formatRuntimeError(error))
      } finally {
        setBusy(false)
      }
    },
    [stopStreaming, applyEventToProjection, appendAudit, transitionRuntime],
  )

  const triggerReplayImport = useCallback(() => {
    replayFileInputRef.current?.click()
  }, [])

  const recoverStream = useCallback(async () => {
    if (!activeSessionId) return
    setLastEventIds((prev) => {
      const next = { ...prev }
      delete next[activeSessionId]
      return next
    })
    appendAudit("stream.recover", `session=${activeSessionId}`)
    transitionRuntime("connecting", `recovering stream: ${activeSessionId}`)
    await attachSession(activeSessionId)
  }, [activeSessionId, appendAudit, attachSession, transitionRuntime])

  const jumpToSearchResult = useCallback(
    (index: number) => {
      if (index < 0 || index >= searchResults.length) return
      const row = searchResults[index]
      setSearchIndexPosition(index)
      const anchors = resolveSearchResultAnchors(row.id)
      const direct = document.getElementById(anchors.primary)
      const fallback = anchors.fallback ? document.getElementById(anchors.fallback) : null
      const element = direct ?? fallback
      if (!element) return
      element.scrollIntoView({ behavior: "smooth", block: "center" })
    },
    [searchResults],
  )

  const nextSearchResult = useCallback(() => {
    const next = nextSearchResultIndex(searchResults.length, searchIndexPosition)
    if (next < 0) return
    jumpToSearchResult(next)
  }, [jumpToSearchResult, searchIndexPosition, searchResults.length])

  const prevSearchResult = useCallback(() => {
    const next = prevSearchResultIndex(searchResults.length, searchIndexPosition)
    if (next < 0) return
    jumpToSearchResult(next)
  }, [jumpToSearchResult, searchIndexPosition, searchResults.length])

  const toggleTaskExpand = useCallback((id: string) => {
    setTaskExpanded((prev) => ({ ...prev, [id]: !(prev[id] ?? true) }))
  }, [])

  return (
    <div className="app">
      <header className="header">
        <h1>BreadBoard Webapp V1 (P0 Scaffold)</h1>
        <div className={`pill ${connectionState}`}>{connectionState}</div>
      </header>
      <p className="subtle">{connectionMessage || "No connection status yet."}</p>

      <section className="panel">
        <div className="row">
          <label>
            Mode
            <select value={connectionMode} onChange={(event) => setConnectionMode(event.target.value as ConnectionMode)}>
              <option value="local">local</option>
              <option value="sandbox">sandbox</option>
              <option value="remote">remote</option>
            </select>
          </label>
          <label>
            Token Storage
            <select value={tokenStoragePolicy} onChange={(event) => setTokenStoragePolicy(event.target.value as TokenStoragePolicy)}>
              <option value="persisted">persisted</option>
              <option value="session">session-only</option>
            </select>
          </label>
          <label>
            Engine Base URL
            <input value={baseUrl} onChange={(event) => setModeBaseUrl(event.target.value)} placeholder={DEFAULT_BASE_URLS[connectionMode]} />
          </label>
          <label>
            API Token (optional)
            <input value={token} onChange={(event) => setModeToken(event.target.value)} placeholder="Bearer token" />
          </label>
          <button onClick={() => void checkConnection()} disabled={busy}>
            Check
          </button>
          <button onClick={() => void runDiagnostics()} disabled={busy}>
            Diagnostics
          </button>
          <button onClick={() => void recoverStream()} disabled={busy || !activeSessionId || connectionState !== "gap"}>
            Recover Stream
          </button>
        </div>
        {connectionMode === "remote" ? (
          <p className="errorText">Remote mode trust boundary: treat server/workspace as external and keep a valid token posture.</p>
        ) : null}
        <p className="subtle">
          diagnostics: <strong>{diagnostics.status}</strong> {diagnostics.details}
        </p>
      </section>

      <main className="layout">
        <section className="panel">
          <h2>Sessions</h2>
          <div className="row">
            <button onClick={() => void refreshSessions()} disabled={busy}>
              Refresh
            </button>
          </div>
          <ul className="sessionList">
            {sessions.map((session) => (
              <li key={session.session_id} className={session.session_id === activeSessionId ? "active" : ""}>
                <button onClick={() => void attachSession(session.session_id)}>{session.session_id}</button>
                <span>{session.status}</span>
              </li>
            ))}
          </ul>
          <h3>Create Session</h3>
          <label>
            Config Path
            <input value={configPath} onChange={(event) => setConfigPath(event.target.value)} />
          </label>
          <label>
            Task
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              placeholder="Describe the work for the agent..."
              rows={4}
            />
          </label>
          <button onClick={() => void createSession()} disabled={busy || projection.checkpointRestoreInFlight || !task.trim()}>
            Create + Attach
          </button>
        </section>

        <section className="panel">
          <h2>Transcript</h2>
          <div className="row">
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder={activeSessionId ? "Send message..." : "Attach a session first"}
              disabled={!activeSessionId}
            />
            <button onClick={() => void sendMessage()} disabled={busy || projection.checkpointRestoreInFlight || !activeSessionId || !message.trim()}>
              Send
            </button>
            <button onClick={() => void stopSession()} disabled={busy || projection.checkpointRestoreInFlight || !activeSessionId}>
              Stop
            </button>
          </div>
          <div className="transcript">
            {projection.transcript.length === 0 ? <p className="subtle">No transcript events yet.</p> : null}
            {projection.transcript.map((row) => (
              <article key={row.id} id={`entry-${row.id}`} className={`bubble ${row.role}`}>
                <header>
                  <strong>{row.role}</strong>
                  <span>{row.final ? "final" : "streaming"}</span>
                </header>
                {row.role === "assistant" ? (
                  <Suspense fallback={<pre>{row.text}</pre>}>
                    <MarkdownMessage text={row.text} final={row.final} />
                  </Suspense>
                ) : (
                  <pre>{row.text}</pre>
                )}
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Checkpoints</h2>
          <div className="row">
            <button onClick={() => void refreshCheckpoints()} disabled={busy || !activeSessionId || projection.checkpointRestoreInFlight}>
              Refresh Checkpoints
            </button>
            <select
              value={projection.activeCheckpointId ?? ""}
              onChange={(event) =>
                setProjection((prev) => ({
                  ...prev,
                  activeCheckpointId: event.target.value || null,
                }))
              }
              disabled={!activeSessionId || projection.checkpoints.length === 0 || projection.checkpointRestoreInFlight}
            >
              <option value="">select checkpoint</option>
              {projection.checkpoints.map((checkpoint) => (
                <option key={checkpoint.id} value={checkpoint.id}>
                  {checkpoint.id}
                </option>
              ))}
            </select>
            <button
              onClick={() => void restoreCheckpoint()}
              disabled={busy || !activeSessionId || !projection.activeCheckpointId || projection.checkpointRestoreInFlight}
            >
              {projection.checkpointRestoreInFlight ? "Restoring…" : "Restore"}
            </button>
          </div>
          <div className="checkpointList">
            {projection.checkpoints.length === 0 ? <p className="subtle">No checkpoint data yet.</p> : null}
            {projection.checkpoints.map((row) => (
              <article key={row.id} className={`checkpointRow ${projection.activeCheckpointId === row.id ? "active" : ""}`}>
                <strong>{row.label}</strong>
                <code>{row.id}</code>
                <span className="subtle">{new Date(row.createdAt).toLocaleString()}</span>
              </article>
            ))}
          </div>
          {projection.lastCheckpointRestore ? (
            <p className={projection.lastCheckpointRestore.status === "ok" ? "subtle" : "errorText"}>
              last restore: {projection.lastCheckpointRestore.message}
            </p>
          ) : null}

          <h2>Search</h2>
          <div className="row">
            <input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="search transcript/tools/artifacts" />
            <select value={searchType} onChange={(event) => setSearchType(event.target.value as SearchEntryType | "all")}>
              <option value="all">all</option>
              <option value="transcript">transcript</option>
              <option value="tool">tool</option>
              <option value="artifact">artifact</option>
            </select>
            <button onClick={() => void prevSearchResult()} disabled={searchResults.length === 0}>
              Prev
            </button>
            <button onClick={() => void nextSearchResult()} disabled={searchResults.length === 0}>
              Next
            </button>
          </div>
          <div className="searchResults">
            {searchQuery.trim().length === 0 ? <p className="subtle">Enter a query to search.</p> : null}
            {searchQuery.trim().length > 0 && searchResults.length === 0 ? <p className="subtle">No search matches.</p> : null}
            {searchResults.slice(0, 80).map((row, index) => (
              <button
                key={`${row.type}:${row.id}`}
                className={`searchResult ${searchIndexPosition === index ? "active" : ""}`}
                onClick={() => jumpToSearchResult(index)}
              >
                <span>{row.type}</span>
                <code>{row.path ?? row.id}</code>
                <small>{row.text.slice(0, 120)}</small>
              </button>
            ))}
          </div>

          <h2>Permissions</h2>
          {permissionError ? <p className="errorText">{permissionError}</p> : null}
          <div className="permissionList">
            {projection.pendingPermissions.length === 0 ? <p className="subtle">No pending permission requests.</p> : null}
            {projection.pendingPermissions.map((request) => {
              const draft = resolvePermissionDraft(request)
              const isBusy = permissionBusyId === request.requestId
              return (
                <article key={request.requestId} className="permissionCard">
                  <header>
                    <strong>{request.tool}</strong>
                    <span>{request.kind}</span>
                  </header>
                  <p className="subtle">{request.summary}</p>
                  {request.diffText ? <pre className="permissionDiff">{request.diffText}</pre> : null}
                  <label>
                    Note
                    <input
                      value={draft.note}
                      onChange={(event) => updatePermissionDraft(request.requestId, { note: event.target.value })}
                      placeholder="optional note"
                    />
                  </label>
                  <div className="row">
                    <label>
                      Scope
                      <select
                        value={draft.scope}
                        onChange={(event) =>
                          updatePermissionDraft(request.requestId, {
                            scope: (event.target.value === "session" ? "session" : "project") as PermissionScope,
                          })
                        }
                      >
                        <option value="project">project</option>
                        <option value="session">session</option>
                      </select>
                    </label>
                    <label>
                      Rule
                      <input
                        value={draft.rule}
                        onChange={(event) => updatePermissionDraft(request.requestId, { rule: event.target.value })}
                        placeholder={request.ruleSuggestion ?? "optional pattern"}
                      />
                    </label>
                  </div>
                  <div className="row permissionActions">
                    <button disabled={isBusy || !activeSessionId} onClick={() => void submitPermissionDecision(request, "allow-once")}>
                      Allow Once
                    </button>
                    <button disabled={isBusy || !activeSessionId} onClick={() => void submitPermissionDecision(request, "allow-always")}>
                      Allow Always
                    </button>
                    <button disabled={isBusy || !activeSessionId} onClick={() => void submitPermissionDecision(request, "deny-once")}>
                      Deny Once
                    </button>
                    <button disabled={isBusy || !activeSessionId} onClick={() => void submitPermissionDecision(request, "deny-always")}>
                      Deny Always
                    </button>
                    <button disabled={isBusy || !activeSessionId} onClick={() => void submitPermissionDecision(request, "deny-stop")}>
                      Deny + Stop
                    </button>
                  </div>
                </article>
              )
            })}
          </div>
          <h3>Permission Ledger</h3>
          {revokeError ? <p className="errorText">{revokeError}</p> : null}
          <div className="row">
            <input
              value={permissionLedgerFilter.tool}
              onChange={(event) => setPermissionLedgerFilter((prev) => ({ ...prev, tool: event.target.value }))}
              placeholder="filter tool"
            />
            <select
              value={permissionLedgerFilter.scope}
              onChange={(event) =>
                setPermissionLedgerFilter((prev) => ({ ...prev, scope: event.target.value as PermissionScope | "all" }))
              }
            >
              <option value="all">all scopes</option>
              <option value="project">project</option>
              <option value="session">session</option>
            </select>
            <select
              value={permissionLedgerFilter.decision}
              onChange={(event) => setPermissionLedgerFilter((prev) => ({ ...prev, decision: event.target.value as PermissionLedgerFilterState["decision"] }))}
            >
              <option value="all">all decisions</option>
              <option value="allow-once">allow-once</option>
              <option value="allow-always">allow-always</option>
              <option value="deny-once">deny-once</option>
              <option value="deny-always">deny-always</option>
              <option value="deny-stop">deny-stop</option>
              <option value="revoke">revoke</option>
            </select>
          </div>
          <div className="ledgerList">
            {filteredLedger.length === 0 ? <p className="subtle">No permission ledger rows.</p> : null}
            {filteredLedger.map((entry) => (
              <article key={entry.requestId} className="ledgerRow">
                <header>
                  <strong>{entry.tool}</strong>
                  <span>{entry.decision}</span>
                </header>
                <p className="subtle">
                  {entry.scope} · {new Date(entry.timestamp).toLocaleString()} · {entry.requestId}
                </p>
                <div className="row">
                  <button onClick={() => void copyText(entry.rule ?? "")} disabled={!entry.rule}>
                    Copy Rule
                  </button>
                  <button
                    onClick={() => {
                      if (!entry.rule) return
                      setPermissionRulePresets((prev) => ({ ...prev, [entry.tool]: entry.rule as string }))
                    }}
                    disabled={!entry.rule}
                  >
                    Re-Apply Rule
                  </button>
                  <button onClick={() => void revokePermission(entry)} disabled={busy || !activeSessionId || entry.revoked}>
                    {entry.revoked ? "Revoked" : "Revoke"}
                  </button>
                </div>
              </article>
            ))}
          </div>

          <h2>Tools</h2>
          <div className="toolRows">
            {projection.toolRows.length === 0 ? <p className="subtle">No tool events yet.</p> : null}
            {projection.toolRows.map((row) => (
              <article key={row.id} id={`entry-${row.id}`} className={`tool ${row.type}`}>
                <header>
                  <strong>{row.label}</strong>
                  <span>{row.type}</span>
                </header>
                <pre>{row.summary}</pre>
                {row.diffText ? (
                  <div className="toolDiff">
                    <div className="row">
                      <span className="subtle">Diff Viewer</span>
                      <button onClick={() => setDiffViewMode((prev) => (prev === "unified" ? "side-by-side" : "unified"))}>
                        {diffViewMode === "unified" ? "Side-by-side" : "Unified"}
                      </button>
                      {row.diffFilePath ? (
                        <button
                          onClick={() => {
                            if (!activeSessionId) return
                            void openFile(activeSessionId, row.diffFilePath as string)
                          }}
                        >
                          Open In File Preview
                        </button>
                      ) : null}
                    </div>
                    <DiffViewer
                      text={row.diffText}
                      mode={diffViewMode}
                      onCopyHunk={(text) => void copyText(text)}
                      onCopyPath={(path) => void copyText(path)}
                      onOpenPath={(path) => {
                        if (!activeSessionId) return
                        void openFile(activeSessionId, path)
                      }}
                    />
                  </div>
                ) : null}
              </article>
            ))}
          </div>
          <h2>Task Tree</h2>
          <div className="row">
            <button onClick={() => setTaskFilters((prev) => ({ ...prev, failedOnly: !prev.failedOnly }))}>
              {taskFilters.failedOnly ? "Show All" : "Failed Only"}
            </button>
            <button onClick={() => setTaskFilters((prev) => ({ ...prev, activeOnly: !prev.activeOnly }))}>
              {taskFilters.activeOnly ? "Show All" : "Active Only"}
            </button>
            <button
              onClick={() => {
                if (!projection.taskGraph.activeNodeId) return
                const node = projection.taskGraph.nodesById[projection.taskGraph.activeNodeId]
                if (!node) return
                jumpToEvent(node.eventId)
              }}
              disabled={!projection.taskGraph.activeNodeId}
            >
              Focus Active
            </button>
          </div>
          <TaskTreePanel
            graph={projection.taskGraph}
            expanded={taskExpanded}
            onToggleExpand={toggleTaskExpand}
            onJumpToEvent={jumpToEvent}
            failedOnly={taskFilters.failedOnly}
            activeOnly={taskFilters.activeOnly}
          />

          <h2>Files</h2>
          <div className="row">
            <button onClick={() => void refreshCurrentDir()} disabled={!activeSessionId || busy}>
              Refresh
            </button>
            <button
              onClick={() => {
                if (!activeSessionId) return
                void listFiles(activeSessionId, parentDir)
              }}
              disabled={!activeSessionId || busy || (!currentDir && !parentDir)}
            >
              Up
            </button>
            <span className="subtle">{currentDir || "/"}</span>
          </div>
          <div className="files">
            {files.length === 0 ? <p className="subtle">No file entries loaded.</p> : null}
            {files.map((entry) => (
              <button key={`${entry.type}:${entry.path}`} className="fileRow" onClick={() => void onFileClick(entry)}>
                <span>{entry.type === "directory" ? "dir" : "file"}</span>
                <code>{entry.path}</code>
              </button>
            ))}
          </div>
          <h3>File Preview</h3>
          <p className="subtle">{selectedFilePath || "No file selected."}</p>
          <div className="filePreview">{selectedFileContent || "Select a file to preview snippet content."}</div>
          <h2>Artifacts</h2>
          <div className="row">
            <input
              value={artifactId}
              onChange={(event) => setArtifactId(event.target.value)}
              placeholder={activeSessionId ? "artifact id/path" : "attach a session first"}
              disabled={!activeSessionId}
            />
            <button onClick={() => void openArtifact()} disabled={!activeSessionId || !artifactId.trim() || busy}>
              Download
            </button>
          </div>
          <h2>Raw Events</h2>
          <div className="row">
            <button onClick={() => void exportReplay()} disabled={busy || !activeSessionId}>
              Export Replay
            </button>
            <button onClick={() => void triggerReplayImport()} disabled={busy}>
              Import Replay
            </button>
            <input
              ref={replayFileInputRef}
              type="file"
              accept="application/json,.json"
              onChange={(event) => void onImportReplayFile(event)}
              style={{ display: "none" }}
            />
          </div>
          <p className="subtle">
            Projection hash: <code>{projectionHash}</code> · events: {projection.events.length}
          </p>
          <p className="subtle">
            metrics: received={metrics.eventsReceived} applied={metrics.eventsApplied} flushes={metrics.flushCount} max_queue=
            {metrics.maxQueueDepth} stale_drops={metrics.staleDrops} last_latency_ms={metrics.lastQueueLatencyMs.toFixed(1)}
          </p>
          <div className="events">
            {projection.events.slice(-120).map((event) => (
              <div key={event.id} id={`event-${event.id}`}>
                {event.id} {event.type} {safeJson(redactSensitiveValue(event.payload))}
              </div>
            ))}
          </div>
          <h3>Audit Log</h3>
          <div className="events">
            {auditLog.length === 0 ? <span className="subtle">No audit actions yet.</span> : null}
            {auditLog
              .slice()
              .reverse()
              .slice(0, 80)
              .map((row) => (
                <div key={row.id}>
                  {new Date(row.at).toLocaleTimeString()} {row.action} {row.detail}
                </div>
              ))}
          </div>
        </section>
      </main>
    </div>
  )
}
