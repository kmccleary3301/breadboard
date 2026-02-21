import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
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
  type PermissionRequestRow,
  type PermissionScope,
} from "./projection"
import { appendSessionEvent, loadSessionEvents } from "./eventStore"
import { runSessionStreamLoop } from "./sessionStream"
import { buildPermissionDecisionPayload, type PermissionDecision, type PermissionDraft } from "./permissions"

const MarkdownMessage = lazy(async () => await import("./MarkdownMessage"))

type ConnectionState = "idle" | "connecting" | "connected" | "error"

const STORAGE_BASE_URL_KEY = "bb.webapp.baseUrl"
const STORAGE_TOKEN_KEY = "bb.webapp.token"
const STORAGE_LAST_EVENT_IDS_KEY = "bb.webapp.lastEventIds"
const DEFAULT_BASE_URL = "http://127.0.0.1:9099"
const DEFAULT_CONFIG_PATH = "agent_configs/base_v2.yaml"
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

export function App() {
  const [baseUrl, setBaseUrl] = useState<string>(() => localStorage.getItem(STORAGE_BASE_URL_KEY) ?? DEFAULT_BASE_URL)
  const [token, setToken] = useState<string>(() => localStorage.getItem(STORAGE_TOKEN_KEY) ?? "")
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
  const [permissionBusyId, setPermissionBusyId] = useState<string | null>(null)
  const [permissionError, setPermissionError] = useState<string>("")
  const streamAbortRef = useRef<AbortController | null>(null)
  const lastEventIdsRef = useRef<Record<string, string>>(lastEventIds)

  const client = useMemo(
    () =>
      createBreadboardClient({
        baseUrl,
        authToken: token.trim().length > 0 ? token.trim() : undefined,
      }),
    [baseUrl, token],
  )

  useEffect(() => {
    lastEventIdsRef.current = lastEventIds
    saveLastEventIds(lastEventIds)
  }, [lastEventIds])

  useEffect(() => {
    localStorage.setItem(STORAGE_BASE_URL_KEY, baseUrl)
  }, [baseUrl])

  useEffect(() => {
    localStorage.setItem(STORAGE_TOKEN_KEY, token)
  }, [token])

  const stopStreaming = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
      streamAbortRef.current = null
    }
  }, [])

  const refreshSessions = useCallback(async () => {
    setBusy(true)
    try {
      const rows = await client.listSessions()
      setSessions(rows)
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [client])

  const checkConnection = useCallback(async () => {
    setConnectionState("connecting")
    setConnectionMessage("")
    try {
      const health = await client.health()
      setConnectionState("connected")
      setConnectionMessage(
        `connected: protocol=${health.protocol_version ?? "?"}, engine=${health.engine_version ?? health.version ?? "?"}`,
      )
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    }
  }, [client])

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

  const parentDir = useMemo(() => {
    if (!currentDir) return ""
    const normalized = currentDir.replace(/\/+$/, "")
    const idx = normalized.lastIndexOf("/")
    if (idx <= 0) return ""
    return normalized.slice(0, idx)
  }, [currentDir])

  useEffect(() => {
    void checkConnection()
    void refreshSessions()
    return () => {
      stopStreaming()
    }
  }, [checkConnection, refreshSessions, stopStreaming])

  const applyEvent = useCallback((event: SessionEvent) => {
    setProjection((prev) => applyEventToProjection(prev, event))
    void appendSessionEvent(event.session_id, event)
  }, [])

  const streamLoop = useCallback(
    async (sessionId: string, signal: AbortSignal) => {
      await runSessionStreamLoop({
        sessionId,
        signal,
        getLastEventId: () => lastEventIdsRef.current[sessionId],
        stream: ({ lastEventId }) =>
          streamSessionEvents(sessionId, {
            config: { baseUrl, authToken: token.trim().length > 0 ? token.trim() : undefined },
            signal,
            lastEventId,
          }),
        onConnecting: () => {
          setConnectionState("connecting")
          setConnectionMessage(`stream connecting: ${sessionId}`)
        },
        onEvent: (event) => {
          applyEvent(event)
          setLastEventIds((prev) => ({ ...prev, [sessionId]: event.id }))
        },
        onConnected: () => {
          setConnectionState("connected")
          setConnectionMessage(`stream connected: ${sessionId}`)
        },
        onRetryError: (error, waitMs) => {
          setConnectionState("error")
          setConnectionMessage(
            `stream error (${error instanceof Error ? error.message : String(error)}), retrying in ${waitMs}ms`,
          )
        },
        onResumeWindowGap: () => {
          setConnectionState("error")
          setConnectionMessage("resume window exceeded (HTTP 409). refresh session state and re-attach.")
        },
      })
    },
    [applyEvent, baseUrl, token],
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
        const cachedEvents = await loadSessionEvents(sessionId, 1000)
        if (cachedEvents.length > 0) {
          const seeded = cachedEvents.reduce(applyEventToProjection, initialProjectionState)
          setProjection(seeded)
        }
      } catch {
        // Cache hydration is best-effort.
      }
      try {
        await listFiles(sessionId, "")
      } catch {
        // File listing is optional in early bootstrap; stream attach should still proceed.
      }
      const abort = new AbortController()
      streamAbortRef.current = abort
      void streamLoop(sessionId, abort.signal)
    },
    [listFiles, stopStreaming, streamLoop],
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
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [attachSession, client, configPath, refreshSessions, task])

  const sendMessage = useCallback(async () => {
    if (!activeSessionId || !message.trim()) return
    setBusy(true)
    try {
      await client.postInput(activeSessionId, { content: message.trim() })
      setMessage("")
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, client, message])

  const stopSession = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      await client.postCommand(activeSessionId, { command: "stop" })
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, client])

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
        rule: request.ruleSuggestion ?? "",
        scope: request.defaultScope,
      }
    },
    [permissionDrafts],
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
        setProjection((prev) => dismissPermissionRequest(prev, request.requestId))
        setPermissionDrafts((prev) => {
          const next = { ...prev }
          delete next[request.requestId]
          return next
        })
      } catch (error) {
        setPermissionError(error instanceof Error ? error.message : String(error))
      } finally {
        setPermissionBusyId(null)
      }
    },
    [activeSessionId, client, resolvePermissionDraft],
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

  const refreshCurrentDir = useCallback(async () => {
    if (!activeSessionId) return
    setBusy(true)
    try {
      await listFiles(activeSessionId, currentDir)
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, currentDir, listFiles])

  const openArtifact = useCallback(async () => {
    if (!activeSessionId || !artifactId.trim()) return
    setBusy(true)
    try {
      const url = buildApiUrl(baseUrl, `/sessions/${activeSessionId}/download`)
      url.searchParams.set("artifact", artifactId.trim())
      const response = await fetch(url, {
        headers: token.trim().length > 0 ? { Authorization: `Bearer ${token.trim()}` } : undefined,
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
    } catch (error) {
      setConnectionState("error")
      setConnectionMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }, [activeSessionId, artifactId, baseUrl, token])

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
            Engine Base URL
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder={DEFAULT_BASE_URL} />
          </label>
          <label>
            API Token (optional)
            <input value={token} onChange={(event) => setToken(event.target.value)} placeholder="Bearer token" />
          </label>
          <button onClick={() => void checkConnection()} disabled={busy}>
            Check
          </button>
        </div>
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
          <button onClick={() => void createSession()} disabled={busy || !task.trim()}>
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
            <button onClick={() => void sendMessage()} disabled={busy || !activeSessionId || !message.trim()}>
              Send
            </button>
            <button onClick={() => void stopSession()} disabled={busy || !activeSessionId}>
              Stop
            </button>
          </div>
          <div className="transcript">
            {projection.transcript.length === 0 ? <p className="subtle">No transcript events yet.</p> : null}
            {projection.transcript.map((row) => (
              <article key={row.id} className={`bubble ${row.role}`}>
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

          <h2>Tools</h2>
          <div className="toolRows">
            {projection.toolRows.length === 0 ? <p className="subtle">No tool events yet.</p> : null}
            {projection.toolRows.map((row) => (
              <article key={row.id} className={`tool ${row.type}`}>
                <header>
                  <strong>{row.label}</strong>
                  <span>{row.type}</span>
                </header>
                <pre>{row.summary}</pre>
              </article>
            ))}
          </div>
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
          <div className="events">
            {projection.events
              .slice(-120)
              .map((event) => `${event.id} ${event.type} ${safeJson(event.payload)}`)
              .join("\n")}
          </div>
        </section>
      </main>
    </div>
  )
}
