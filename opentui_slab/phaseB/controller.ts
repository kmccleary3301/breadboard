import fs from "node:fs"
import path from "node:path"
import { spawn, type ChildProcess } from "node:child_process"

import { connectIpcClient, createIpcServer, type IpcConnection } from "./ipc.ts"
import {
  nowEnvelope,
  type UIToControllerMessage,
  type ControllerToUIMessage,
  type PaletteItem,
  type PaletteKind,
  IPC_PROTOCOL_VERSION,
} from "./protocol.ts"
import {
  ensureBridge,
  createSession,
  postInput,
  postCommand,
  streamSessionEvents,
  type BridgeHandle,
  type BridgeEvent,
  getModelCatalog,
  type ModelCatalogResponse,
  resolveEngineRoot,
  healthCheck,
} from "./bridge.ts"
import { formatBridgeEventForStdout } from "./format.ts"
import { formatNormalizedEventForStdout, normalizeBridgeEvent } from "./event_adapter.ts"
import { resolveCommandTargetSessionId, type SessionNavCommand } from "./session_switch.ts"
import {
  appendContextBurstEvent,
  formatContextBurstBlock,
  formatContextBurstDetail,
  formatContextBurstSummary,
  isContextCollectionEvent,
  type ContextBurstState,
} from "./context_burst.ts"

type ParsedArgs = {
  readonly configPath?: string
  readonly workspace?: string
  readonly baseUrl?: string
  readonly python?: string
  readonly noUi?: boolean
  readonly uiCmd?: string
  readonly task?: string
  readonly exitAfterMs?: number
  readonly permissionMode?: string
  readonly respawnUi?: boolean
  readonly externalBridge?: boolean
  readonly printIpc?: boolean
}

const parseArgs = (argv: string[]): ParsedArgs => {
  const args: Record<string, string | boolean> = {}
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i] ?? ""
    if (!token.startsWith("--")) continue
    const key = token.slice(2)
    const next = argv[i + 1]
    if (!next || next.startsWith("--")) {
      args[key] = true
      continue
    }
    args[key] = next
    i += 1
  }
  return {
    configPath: typeof args["config"] === "string" ? (args["config"] as string) : undefined,
    workspace: typeof args["workspace"] === "string" ? (args["workspace"] as string) : undefined,
    baseUrl: typeof args["base-url"] === "string" ? (args["base-url"] as string) : undefined,
    python: typeof args["python"] === "string" ? (args["python"] as string) : undefined,
    noUi: Boolean(args["no-ui"]),
    uiCmd: typeof args["ui-cmd"] === "string" ? (args["ui-cmd"] as string) : undefined,
    task: typeof args["task"] === "string" ? (args["task"] as string) : undefined,
    exitAfterMs: typeof args["exit-after-ms"] === "string" ? Number(args["exit-after-ms"]) : undefined,
    permissionMode: typeof args["permission-mode"] === "string" ? (args["permission-mode"] as string) : undefined,
    respawnUi:
      args["respawn-ui"] === undefined
        ? true
        : typeof args["respawn-ui"] === "string"
          ? !["0", "false", "no"].includes((args["respawn-ui"] as string).trim().toLowerCase())
          : Boolean(args["respawn-ui"]),
    externalBridge: Boolean(args["external-bridge"]),
    printIpc: Boolean(args["print-ipc"]),
  }
}

const utcStamp = (): string => {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`
}

const safeAppendLog = async (logPath: string, line: string) => {
  await fs.promises.appendFile(logPath, `${line}\n`, "utf8").catch(() => undefined)
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const isUiMsg = (value: unknown): value is UIToControllerMessage =>
  isRecord(value) && typeof value.type === "string" && value.protocol_version === IPC_PROTOCOL_VERSION

type ControllerState = {
  status: "idle" | "starting" | "running" | "stopped" | "error"
  baseUrl: string | null
  configPath: string
  workspace: string | null
  activeSessionId: string | null
  currentModel: string | null
  pendingPermissions: Array<Record<string, unknown>>
}

type BacklogItem = ControllerToUIMessage

const scoreFuzzy = (candidate: string, query: string): number | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return 0
  const haystack = candidate.toLowerCase()
  let score = 0
  let lastIndex = -1
  let consecutive = 0
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    if (!ch) continue
    const index = haystack.indexOf(ch, lastIndex + 1)
    if (index === -1) return null
    score += 10
    if (index === lastIndex + 1) {
      consecutive += 1
      score += 8 + consecutive
    } else {
      consecutive = 0
      score -= Math.max(0, index - lastIndex - 1)
    }
    lastIndex = index
  }
  score += Math.max(0, 48 - haystack.length)
  return score
}

const filterPaletteItems = (items: ReadonlyArray<PaletteItem>, query: string, limit = 80): PaletteItem[] => {
  const needle = query.trim()
  if (!needle) return items.slice(0, limit)
  const scored = items
    .map((item) => {
      const score = scoreFuzzy(`${item.title} ${item.detail ?? ""} ${item.id}`, needle)
      return score == null ? null : { item, score }
    })
    .filter((entry): entry is { item: PaletteItem; score: number } => entry != null)
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    return a.item.title.localeCompare(b.item.title)
  })
  return scored.slice(0, limit).map((entry) => entry.item)
}

const main = async () => {
  const args = parseArgs(process.argv)
  const configPathRaw =
    args.configPath?.trim() ||
    process.env.BREADBOARD_CONFIG_PATH?.trim() ||
    "agent_configs/codex_cli_gpt51mini_e4_live.yaml"
  const workspace = args.workspace?.trim() || process.env.BREADBOARD_WORKSPACE?.trim() || ""

  const runDir = path.resolve(process.cwd(), "artifacts", "phaseB", utcStamp())
  await fs.promises.mkdir(runDir, { recursive: true })
  const logPath = path.join(runDir, "controller.log")
  const bridgeLogPath = path.join(runDir, "bridge.log")

  const engineRoot = resolveEngineRoot(process.cwd()) ?? resolveEngineRoot(path.dirname(process.cwd()))
  if (!engineRoot) {
    throw new Error("Unable to locate engine root (agentic_coder_prototype/).")
  }

  const resolveConfigPath = (value: string): string => {
    const trimmed = value.trim()
    if (!trimmed) return value
    if (path.isAbsolute(trimmed)) return trimmed
    const candidates = [
      path.resolve(process.cwd(), trimmed),
      path.resolve(engineRoot, trimmed),
    ]
    for (const candidate of candidates) {
      if (fs.existsSync(candidate)) return candidate
    }
    return trimmed
  }

  const configPath = resolveConfigPath(configPathRaw)

  const state: ControllerState = {
    status: "starting",
    baseUrl: null,
    configPath,
    workspace: workspace || null,
    activeSessionId: null,
    currentModel: null,
    pendingPermissions: [],
  }
  const permissionMode = args.permissionMode?.trim() || process.env.BREADBOARD_PERMISSION_MODE?.trim() || ""

  let uiChild: ChildProcess | null = null
  let bridge: BridgeHandle | null = null
  let activeUiConn: IpcConnection | null = null
  let shuttingDown = false

  const shouldBufferUiBacklog = !args.noUi
  const uiBacklogMaxItemsRaw = Number(process.env.BREADBOARD_UI_BACKLOG_MAX_ITEMS ?? "")
  const uiBacklogMaxBytesRaw = Number(process.env.BREADBOARD_UI_BACKLOG_MAX_BYTES ?? "")
  const uiBacklogMaxItems =
    Number.isFinite(uiBacklogMaxItemsRaw) && uiBacklogMaxItemsRaw > 0 ? uiBacklogMaxItemsRaw : 2000
  const uiBacklogMaxBytes =
    Number.isFinite(uiBacklogMaxBytesRaw) && uiBacklogMaxBytesRaw > 0 ? uiBacklogMaxBytesRaw : 1_500_000

  const uiBacklog: Array<{ msg: BacklogItem; approxBytes: number }> = []
  let uiBacklogBytes = 0

  const enqueueUiBacklog = (msg: BacklogItem) => {
    if (!shouldBufferUiBacklog) return
    const approxBytes = JSON.stringify(msg).length
    uiBacklog.push({ msg, approxBytes })
    uiBacklogBytes += approxBytes

    while (uiBacklog.length > uiBacklogMaxItems || uiBacklogBytes > uiBacklogMaxBytes) {
      const dropped = uiBacklog.shift()
      if (!dropped) break
      uiBacklogBytes -= dropped.approxBytes
    }
  }

  const sendToUi = (msg: BacklogItem) => {
    if (activeUiConn) {
      activeUiConn.send(msg)
      return
    }
    enqueueUiBacklog(msg)
  }

  const sendState = () => {
    const msg: BacklogItem = nowEnvelope("ctrl.state", {
      active_session_id: state.activeSessionId,
      base_url: state.baseUrl,
      config_path: state.configPath,
      current_model: state.currentModel,
      status: state.status,
      pending_permissions: state.pendingPermissions,
    })
    sendToUi(msg)
  }

  const sendPaletteStatus = (kind: PaletteKind, status: "idle" | "loading" | "ready" | "error", message?: string) => {
    sendToUi(nowEnvelope("ctrl.palette.status", { kind, status, message: message ?? null }))
  }

  const sendPaletteItems = (kind: PaletteKind, query: string, items: ReadonlyArray<PaletteItem>) => {
    sendToUi(nowEnvelope("ctrl.palette.items", { kind, query, items }))
  }

  const transcriptMaxBytesRaw = Number(process.env.BREADBOARD_TRANSCRIPT_BUFFER_MAX_BYTES ?? "")
  const transcriptMaxBytes =
    Number.isFinite(transcriptMaxBytesRaw) && transcriptMaxBytesRaw > 0 ? Math.floor(transcriptMaxBytesRaw) : 1_000_000
  const transcriptChunks: string[] = []
  let transcriptBytes = 0

  const appendTranscriptChunk = (text: string) => {
    if (!text) return
    const bytes = Buffer.byteLength(text, "utf8")
    transcriptChunks.push(text)
    transcriptBytes += bytes
    while (transcriptBytes > transcriptMaxBytes && transcriptChunks.length > 1) {
      const dropped = transcriptChunks.shift()
      if (!dropped) break
      transcriptBytes -= Buffer.byteLength(dropped, "utf8")
    }
  }

  const sendTranscript = (text: string) => {
    appendTranscriptChunk(text)
    sendToUi(nowEnvelope("ctrl.transcript.append", { text }))
  }

  const debugFakePermission =
    String(process.env.BREADBOARD_DEBUG_FAKE_PERMISSION ?? "")
      .trim()
      .toLowerCase() === "1" ||
    String(process.env.BREADBOARD_DEBUG_FAKE_PERMISSION ?? "")
      .trim()
      .toLowerCase() === "true"

  const COMMAND_ITEMS: ReadonlyArray<PaletteItem> = [
    { id: "stop_run", title: "Stop run", detail: "Interrupt current run (stop)" },
    { id: "retry_last", title: "Retry", detail: "Restart stream (retry)" },
    { id: "status", title: "Status", detail: "Fetch session status summary" },
    {
      id: "session_child_previous",
      title: "Previous subagent",
      detail: "Cycle to previous child session (OpenCode parity scaffold)",
    },
    {
      id: "session_child_next",
      title: "Next subagent",
      detail: "Cycle to next child session (OpenCode parity scaffold)",
    },
    {
      id: "session_parent",
      title: "Parent session",
      detail: "Jump to parent session (OpenCode parity scaffold)",
    },
    { id: "save_transcript", title: "Save transcript", detail: "Write buffered transcript to artifacts" },
    ...(debugFakePermission
      ? ([{ id: "debug_permission", title: "Debug permission", detail: "Inject a fake permission request" }] satisfies ReadonlyArray<PaletteItem>)
      : []),
  ]

  const pendingPaletteRequests = new Map<PaletteKind, string>()

  let modelCatalogCache: { fetchedAtMs: number; catalog: ModelCatalogResponse } | null = null

  const filePickerMaxResultsRaw = Number(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_RESULTS ?? "")
  const filePickerMaxIndexFilesRaw = Number(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_INDEX_FILES ?? "")
  const filePickerIndexNodeModules =
    String(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_NODE_MODULES ?? "")
      .trim()
      .toLowerCase() === "true"
  const filePickerIndexHiddenDirs =
    String(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_HIDDEN_DIRS ?? "")
      .trim()
      .toLowerCase() === "true"
  const filePickerMaxResults =
    Number.isFinite(filePickerMaxResultsRaw) && filePickerMaxResultsRaw > 0 ? Math.floor(filePickerMaxResultsRaw) : 20
  const filePickerMaxIndexFiles =
    Number.isFinite(filePickerMaxIndexFilesRaw) && filePickerMaxIndexFilesRaw > 0
      ? Math.floor(filePickerMaxIndexFilesRaw)
      : 50_000

  const workspaceRoot = state.workspace?.trim()
    ? path.resolve(state.workspace.trim())
    : path.resolve(process.cwd(), "..")

  let fileIndexCache: { root: string; indexedAtMs: number; files: string[]; truncated: boolean } | null = null
  let fileIndexInFlight: Promise<{ files: string[]; truncated: boolean }> | null = null

  const shouldSkipDir = (name: string): boolean => {
    if (!name) return true
    if (!filePickerIndexNodeModules && name === "node_modules") return true
    if (!filePickerIndexHiddenDirs && name.startsWith(".")) return true
    if (name === ".git") return true
    if (name === "__pycache__") return true
    if (name === ".venv") return true
    if (name === ".pytest_cache") return true
    if (name === "dist") return true
    if (name === "build") return true
    if (name === "artifacts") return true
    return false
  }

  const indexWorkspaceFiles = async (): Promise<{ files: string[]; truncated: boolean }> => {
    const started = Date.now()
    const files: string[] = []
    let truncated = false

    const queue: string[] = ["."]
    while (queue.length > 0) {
      const relDir = queue.shift()!
      const absDir = path.join(workspaceRoot, relDir)
      let entries: fs.Dirent[]
      try {
        entries = await fs.promises.readdir(absDir, { withFileTypes: true })
      } catch {
        continue
      }
      for (const entry of entries) {
        const name = entry.name
        if (entry.isDirectory()) {
          if (shouldSkipDir(name)) continue
          const childRel = relDir === "." ? name : path.join(relDir, name)
          queue.push(childRel)
          continue
        }
        if (!entry.isFile()) continue
        const relPath = relDir === "." ? name : path.join(relDir, name)
        files.push(relPath)
        if (files.length % 2500 === 0) {
          sendPaletteStatus("files", "loading", `Indexing… ${files.length} files`)
        }
        if (files.length >= filePickerMaxIndexFiles) {
          truncated = true
          break
        }
      }
      if (truncated) break
    }

    files.sort((a, b) => a.localeCompare(b))
    await safeAppendLog(logPath, `[files] indexed count=${files.length} truncated=${truncated} root=${workspaceRoot} ms=${Date.now() - started}`)
    return { files, truncated }
  }

  const loadFileIndexCached = async (): Promise<{ files: string[]; truncated: boolean }> => {
    const now = Date.now()
    if (fileIndexCache && now - fileIndexCache.indexedAtMs < 60_000 && fileIndexCache.root === workspaceRoot) {
      return { files: fileIndexCache.files, truncated: fileIndexCache.truncated }
    }
    if (fileIndexInFlight) return await fileIndexInFlight
    fileIndexInFlight = (async () => {
      const result = await indexWorkspaceFiles()
      fileIndexCache = { root: workspaceRoot, indexedAtMs: Date.now(), files: result.files, truncated: result.truncated }
      fileIndexInFlight = null
      return result
    })().catch((err) => {
      fileIndexInFlight = null
      throw err
    })
    return await fileIndexInFlight
  }

  const filterFilePaths = (paths: ReadonlyArray<string>, query: string, limit: number): string[] => {
    const needle = query.trim().toLowerCase()
    if (!needle) return paths.slice(0, limit)
    const matches: string[] = []
    for (const p of paths) {
      if (p.toLowerCase().includes(needle)) {
        matches.push(p)
        if (matches.length >= limit * 10) break
      }
    }
    if (matches.length <= limit) return matches
    const scored = matches
      .map((p) => {
        const score = scoreFuzzy(p, needle)
        return score == null ? null : { p, score }
      })
      .filter((entry): entry is { p: string; score: number } => entry != null)
    scored.sort((a, b) => b.score - a.score)
    return scored.slice(0, limit).map((entry) => entry.p)
  }

  const normalizeProviderLabel = (raw: string | null | undefined): string => {
    const value = (raw ?? "unknown").trim()
    const lowered = value.toLowerCase()
    if (!value) return "Unknown"
    if (lowered === "openrouter") return "OpenRouter"
    if (lowered === "openai") return "OpenAI"
    return value
      .split(/[^a-z0-9]+/i)
      .filter((part) => part.length > 0)
      .map((part) => part[0]!.toUpperCase() + part.slice(1))
      .join(" ")
  }

  const loadModelCatalogCached = async (): Promise<ModelCatalogResponse> => {
    if (!bridge) throw new Error("bridge missing")
    const now = Date.now()
    if (modelCatalogCache && now - modelCatalogCache.fetchedAtMs < 60_000) {
      return modelCatalogCache.catalog
    }
    const catalog = await getModelCatalog(bridge.baseUrl, state.configPath)
    modelCatalogCache = { fetchedAtMs: now, catalog }
    if (!state.currentModel) {
      const defaultModel = typeof catalog.default_model === "string" ? catalog.default_model.trim() : ""
      if (defaultModel) {
        state.currentModel = defaultModel
        sendState()
      }
    }
    return catalog
  }

  const handlePaletteRequest = async (kind: PaletteKind, query: string) => {
    sendPaletteStatus(kind, "loading")
    if (kind === "commands") {
      const items = filterPaletteItems(COMMAND_ITEMS, query)
      sendPaletteItems(kind, query, items)
      sendPaletteStatus(kind, "ready", items.length ? `${items.length} item(s)` : "No matches")
      return
    }
    if (kind === "models") {
      if (!bridge) {
        pendingPaletteRequests.set(kind, query)
        sendPaletteItems(kind, query, [])
        sendPaletteStatus(kind, "loading", "Bridge starting…")
        return
      }
      try {
        const catalog = await loadModelCatalogCached()
        const defaultModel = typeof catalog.default_model === "string" ? catalog.default_model.trim() : ""
        const models = Array.isArray(catalog.models) ? catalog.models : []
        const itemsUnfiltered: PaletteItem[] = models.map((model) => {
          const providerLabel = normalizeProviderLabel(model.provider ?? null)
          const displayName = (model.name ?? "").trim() || model.id
          const ctx = typeof model.context_length === "number" ? model.context_length : null
          const ctxK = ctx != null ? Math.max(1, Math.round(ctx / 1000)) : null
          const flags: string[] = []
          if (state.currentModel && model.id === state.currentModel) flags.push("current")
          else if (!state.currentModel && defaultModel && model.id === defaultModel) flags.push("default")
          const prefix = flags.length > 0 ? `[${flags.join(",")}] ` : ""
          return {
            id: model.id,
            title: `${prefix}${providerLabel} · ${displayName}`,
            detail: ctxK != null ? `${ctxK}k ctx` : null,
            group: providerLabel,
            meta: { provider: model.provider ?? null },
          }
        })
        const items = filterPaletteItems(itemsUnfiltered, query, 120)
        sendPaletteItems(kind, query, items)
        sendPaletteStatus(kind, "ready", items.length ? `${items.length} item(s)` : "No matches")
      } catch (err) {
        sendPaletteItems(kind, query, [])
        sendPaletteStatus(kind, "error", (err as Error).message)
      }
      return
    }
    if (kind === "files") {
      try {
        const index = await loadFileIndexCached()
        const limit = Math.max(5, filePickerMaxResults)
        const filtered = filterFilePaths(index.files, query, limit)
        const items: PaletteItem[] = filtered.map((p) => ({ id: p, title: p, detail: null }))
        sendPaletteItems(kind, query, items)
        const suffix = index.truncated ? " (truncated)" : ""
        sendPaletteStatus(kind, "ready", `${items.length} item(s)${suffix}`)
      } catch (err) {
        sendPaletteItems(kind, query, [])
        sendPaletteStatus(kind, "error", (err as Error).message)
      }
      return
    }
    if (kind === "transcript_search") {
      const needle = query.trim().toLowerCase()
      const allText = transcriptChunks.join("")
      const lines = allText.split(/\r?\n/)
      const maxResults = 60
      const results: Array<{ line: string; index: number }> = []
      if (!needle) {
        const tail = lines.slice(-maxResults)
        for (let i = 0; i < tail.length; i += 1) {
          const line = tail[i] ?? ""
          const index = Math.max(0, lines.length - tail.length + i)
          results.push({ line, index })
        }
      } else {
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i] ?? ""
          if (!line.toLowerCase().includes(needle)) continue
          results.push({ line, index: i })
          if (results.length >= maxResults) break
        }
      }
      const items: PaletteItem[] = results.map((r) => ({
        id: `line:${r.index}`,
        title: r.line.length > 160 ? `${r.line.slice(0, 160)}…` : r.line,
        detail: `line ${r.index + 1}`,
        meta: { line: r.line, line_index: r.index },
      }))
      sendPaletteItems(kind, query, items)
      sendPaletteStatus(kind, "ready", items.length ? `${items.length} match(es)` : "No matches")
      return
    }
    sendPaletteItems(kind, query, [])
    sendPaletteStatus(kind, "error", `${kind} palette not implemented yet`)
  }

  const flushPendingPalettes = async () => {
    const entries = Array.from(pendingPaletteRequests.entries())
    pendingPaletteRequests.clear()
    for (const [kind, query] of entries) {
      await handlePaletteRequest(kind, query)
    }
  }

  const flushUiBacklog = async () => {
    if (!activeUiConn) return
    if (!uiBacklog.length) return
    const count = uiBacklog.length
    const bytes = uiBacklogBytes
    for (const entry of uiBacklog) {
      activeUiConn.send(entry.msg)
    }
    uiBacklog.length = 0
    uiBacklogBytes = 0
    await safeAppendLog(logPath, `[ipc] flushed backlog items=${count} bytes=${bytes}`)
  }

  const ipcServer = await createIpcServer({ host: "127.0.0.1" })
  await safeAppendLog(logPath, `[ipc] listening ${ipcServer.host}:${ipcServer.port}`)
  if (args.printIpc) {
    console.error(JSON.stringify({ ipc: { host: ipcServer.host, port: ipcServer.port }, run_dir: runDir }))
  }

  ipcServer.onConnection((conn) => {
    void safeAppendLog(logPath, `[ipc] ui connected ${conn.remote}`)
    if (activeUiConn) {
      activeUiConn.close()
    }
    activeUiConn = conn

    conn.send(
      nowEnvelope("ctrl.hello", {
        controller_version: "phaseB-0.1",
        ipc_protocol_version: IPC_PROTOCOL_VERSION,
      }),
    )

    sendState()
    void flushUiBacklog()

    conn.onMessage((msg) => {
      if (!isUiMsg(msg)) return
      void handleUiMessage(msg).catch((err) => {
        sendToUi(nowEnvelope("ctrl.error", { message: (err as Error).message }))
      })
    })

    conn.onClose(() => {
      if (activeUiConn === conn) {
        activeUiConn = null
      }
      void safeAppendLog(logPath, `[ipc] ui disconnected ${conn.remote}`)
    })
  })

  const startUi = async () => {
    if (args.noUi) return
    const cmd = args.uiCmd?.trim() || "bun"
    const uiEntrypoint = path.resolve(process.cwd(), "phaseB", "ui.ts")
    uiChild = spawn(cmd, ["run", uiEntrypoint], {
      stdio: "inherit",
      env: {
        ...process.env,
        BREADBOARD_IPC_HOST: ipcServer.host,
        BREADBOARD_IPC_PORT: String(ipcServer.port),
      },
      cwd: process.cwd(),
    })
    uiChild.once("exit", () => {
      uiChild = null
      if (shuttingDown) return
      if (args.noUi) return
      if (!args.respawnUi) return
      // Best-effort: respawn after a small delay so the controller can keep running.
      setTimeout(() => {
        if (!shuttingDown) void startUi()
      }, 300)
    })
  }

  const startBridge = async () => {
    const baseUrlHint = args.baseUrl?.trim() || ""
    if (args.externalBridge) {
      if (!baseUrlHint) {
        throw new Error("--external-bridge requires --base-url")
      }
      const ok = await healthCheck(baseUrlHint)
      if (!ok) {
        throw new Error(`External bridge not reachable at ${baseUrlHint}`)
      }
      bridge = { baseUrl: baseUrlHint.replace(/\/$/, ""), child: null, started: false }
    } else {
      bridge = await ensureBridge({
        baseUrlHint: baseUrlHint || undefined,
        python: args.python,
        engineRoot,
        logPath: bridgeLogPath,
      })
    }
    state.baseUrl = bridge.baseUrl
    state.status = "idle"
    sendState()
    await flushPendingPalettes()
  }

  let sseAbort: AbortController | null = null
  let contextBurst: ContextBurstState | null = null
  let stopResolve: (() => void) | null = null
  const flushContextBurst = () => {
    if (!contextBurst) return
    const summaryLine = formatContextBurstSummary(contextBurst)
    const detail = formatContextBurstDetail(contextBurst)
    const groupedBlock = formatContextBurstBlock(contextBurst)
    appendTranscriptChunk(groupedBlock)
    sendToUi(
      nowEnvelope("ctrl.event", {
        event: {
          type: "context_burst",
          count: contextBurst.count,
          tool_counts: contextBurst.toolCounts,
          failed_count: contextBurst.failedCount,
        },
        adapter_output: {
          stdout_text: groupedBlock,
          summary_text: summaryLine.replace(/\n/g, " ").trim(),
          normalized_event: {
            type: "context.burst",
            count: contextBurst.count,
            toolCounts: contextBurst.toolCounts,
            failedCount: contextBurst.failedCount,
            summary: summaryLine.replace(/\n/g, " ").trim(),
            detail,
            entries: contextBurst.entries,
          },
          context_block: {
            summary: summaryLine.replace(/\n/g, " ").trim(),
            detail,
            block_text: groupedBlock.trim(),
            entries: contextBurst.entries,
          },
          hints: {
            lane: "tool",
            badge: "context",
            tone: contextBurst.failedCount > 0 ? "warning" : "info",
            priority: "normal",
            stream: false,
          },
          tool_render: {
            mode: "compact",
            reason: "context-burst-grouped",
          },
          overlay_intent: null,
        },
      }),
    )
    contextBurst = null
  }

  const startSse = async (sessionId: string) => {
    if (!bridge) return
    if (sseAbort) {
      sseAbort.abort()
      sseAbort = null
    }
    sseAbort = new AbortController()
    state.status = "running"
    sendState()
    void (async () => {
      try {
        for await (const evt of streamSessionEvents(bridge.baseUrl, sessionId, { signal: sseAbort!.signal })) {
          handleBridgeEvent(evt)
        }
      } catch (err) {
        sendToUi(nowEnvelope("ctrl.error", { message: `SSE stream error: ${(err as Error).message}` }))
      }
    })()
  }

  const handleBridgeEvent = (evt: BridgeEvent) => {
    const rawEvent = evt as unknown as Record<string, unknown>
    const normalized = normalizeBridgeEvent(evt)
    const isContextEvent = isContextCollectionEvent(normalized)

    if (isContextEvent) {
      contextBurst = appendContextBurstEvent(contextBurst, normalized)
    } else if (contextBurst) {
      flushContextBurst()
    }

    const rendered = isContextEvent
      ? null
      : formatNormalizedEventForStdout(normalized) ?? formatBridgeEventForStdout(rawEvent)
    sendToUi(
      nowEnvelope("ctrl.event", {
        event: rawEvent,
        adapter_output: {
          stdout_text: rendered ?? null,
          summary_text: normalized.summary.short,
          normalized_event: normalized as unknown as Record<string, unknown>,
          hints: {
            lane: normalized.hints.lane,
            badge: normalized.hints.badge,
            tone: normalized.hints.tone,
            priority: normalized.hints.priority,
            stream: normalized.hints.stream,
          },
          tool_render: normalized.toolRenderPolicy
            ? {
                mode: normalized.toolRenderPolicy.mode,
                reason: normalized.toolRenderPolicy.reason,
              }
            : null,
          overlay_intent: normalized.overlayIntent
            ? {
                kind: normalized.overlayIntent.kind,
                action: normalized.overlayIntent.action,
                requestId: normalized.overlayIntent.requestId ?? null,
                taskId: normalized.overlayIntent.taskId ?? null,
              }
            : null,
        },
      }),
    )
    if (rendered) appendTranscriptChunk(rendered)
    if (evt.type === "permission_request") {
      const requestId =
        (evt.payload as any)?.request_id ||
        (evt.payload as any)?.requestId ||
        (evt.payload as any)?.permission_id ||
        (evt.payload as any)?.id
      if (typeof requestId === "string" && requestId.trim()) {
        const entry: Record<string, unknown> = { request_id: requestId.trim(), ...(evt.payload ?? {}) }
        state.pendingPermissions = state.pendingPermissions.filter((p) => p.request_id !== requestId.trim())
        state.pendingPermissions.push(entry)
        sendState()
        sendToUi(nowEnvelope("ctrl.permission.request", { request_id: requestId.trim(), context: entry }))
      }
    }
    if (evt.type === "permission_response") {
      const requestId =
        (evt.payload as any)?.request_id ||
        (evt.payload as any)?.requestId ||
        (evt.payload as any)?.permission_id ||
        (evt.payload as any)?.id
      if (typeof requestId === "string" && requestId.trim()) {
        state.pendingPermissions = state.pendingPermissions.filter((p) => p.request_id !== requestId.trim())
        sendState()
      }
    }
    if (evt.type === "run_finished") {
      flushContextBurst()
      state.status = "idle"
      sendState()
    }
  }

  const handleUiMessage = async (msg: UIToControllerMessage) => {
    if (msg.type === "ui.ready") {
      sendState()
      return
    }
    if (msg.type === "ui.palette.open") {
      const kind = String((msg.payload as any).kind ?? "").trim() as PaletteKind
      const query = String((msg.payload as any).query ?? "").trim()
      if (!kind) return
      await handlePaletteRequest(kind, query)
      return
    }
    if (msg.type === "ui.palette.query") {
      const kind = String((msg.payload as any).kind ?? "").trim() as PaletteKind
      const query = String((msg.payload as any).query ?? "").trim()
      if (!kind) return
      await handlePaletteRequest(kind, query)
      return
    }
    if (msg.type === "ui.palette.select") {
      const kind = String((msg.payload as any).kind ?? "").trim() as PaletteKind
      const itemId = String((msg.payload as any).item_id ?? "").trim()
      if (!kind || !itemId) return
      if (kind === "commands") {
        await handleUiMessage(nowEnvelope("ui.command", { name: itemId }, { id: msg.id }))
      }
      return
    }
    if (msg.type === "ui.palette.close") {
      return
    }
    if (msg.type === "ui.shutdown") {
      await shutdown("ui.shutdown")
      return
    }
    if (msg.type === "ui.submit") {
      if (!bridge) throw new Error("bridge missing")
      const text = String((msg.payload as any).text ?? "")
      if (!text.trim()) return

      if (!state.activeSessionId) {
        const created = await createSession(bridge.baseUrl, {
          config_path: state.configPath,
          task: text,
          workspace: state.workspace,
          permission_mode: permissionMode || null,
          stream: true,
        })
        state.activeSessionId = created.session_id
        sendTranscript(`\n[session] ${created.session_id}\n`)
        await safeAppendLog(logPath, `[session] created ${created.session_id}`)
        sendState()
        await startSse(created.session_id)
        return
      }

      await postInput(bridge.baseUrl, state.activeSessionId, { content: text })
      return
    }
    if (msg.type === "ui.permission.respond") {
      if (!bridge) throw new Error("bridge missing")
      if (!state.activeSessionId) throw new Error("no active session")
      const requestId = String((msg.payload as any).request_id ?? "").trim()
      const decision = String((msg.payload as any).decision ?? "").trim()
      if (!requestId || !decision) return
      const noteRaw = (msg.payload as any).note
      const scopeRaw = (msg.payload as any).scope
      const ruleRaw = (msg.payload as any).rule
      const stopRaw = (msg.payload as any).stop
      const permissionPayload: Record<string, unknown> = { request_id: requestId, decision }
      if (typeof noteRaw === "string" && noteRaw.trim()) permissionPayload.note = noteRaw.trim()
      if (typeof scopeRaw === "string" && scopeRaw.trim()) permissionPayload.scope = scopeRaw.trim()
      if (typeof ruleRaw === "string" && ruleRaw.trim()) permissionPayload.rule = ruleRaw.trim()
      if (typeof stopRaw === "boolean") permissionPayload.stop = stopRaw
      await postCommand(bridge.baseUrl, state.activeSessionId, {
        command: "permission_decision",
        payload: permissionPayload,
      })
      sendTranscript(`\n[permission] decision ${decision}\n`)
      return
    }
    if (msg.type === "ui.command") {
      const name = String((msg.payload as any).name ?? "").trim()
      if (name === "restart_ui") {
        if (uiChild) {
          try {
            uiChild.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
          } catch {
            // ignore
          }
        } else if (!args.noUi) {
          void startUi()
        }
        return
      }
      if (name === "stop_run") {
        if (!bridge) throw new Error("bridge missing")
        if (!state.activeSessionId) throw new Error("no active session")
        await postCommand(bridge.baseUrl, state.activeSessionId, { command: "stop" })
        sendTranscript(`\n[command] stop\n`)
        return
      }
      if (name === "retry_last") {
        if (!bridge) throw new Error("bridge missing")
        if (!state.activeSessionId) throw new Error("no active session")
        await postCommand(bridge.baseUrl, state.activeSessionId, { command: "retry" })
        sendTranscript(`\n[command] retry\n`)
        return
      }
      if (name === "status") {
        if (!bridge) throw new Error("bridge missing")
        if (!state.activeSessionId) throw new Error("no active session")
        await postCommand(bridge.baseUrl, state.activeSessionId, { command: "status" })
        sendTranscript(`\n[command] status\n`)
        return
      }
      if (name === "save_transcript") {
        const outPath = path.join(runDir, "transcript.txt")
        await fs.promises.writeFile(outPath, transcriptChunks.join(""), "utf8")
        sendTranscript(`\n[transcript] saved ${outPath}\n`)
        return
      }
      if (name === "debug_permission") {
        if (!debugFakePermission) return
        const requestId = `debug-${Date.now()}`
        handleBridgeEvent({
          id: requestId,
          type: "permission_request",
          session_id: state.activeSessionId ?? "debug",
          turn: null,
          timestamp_ms: Date.now(),
          payload: {
            request_id: requestId,
            tool: "debug_tool",
            kind: "shell",
            summary: "Debug: allow once / deny once / stop",
            defaultScope: "session",
            ruleSuggestion: "shell:*",
            createdAt: Date.now(),
          },
        } as unknown as BridgeEvent)
        sendTranscript(`\n[permission] debug request_id=${requestId}\n`)
        return
      }
      if (name === "set_model") {
        if (!bridge) throw new Error("bridge missing")
        if (!state.activeSessionId) throw new Error("no active session")
        const model = String((msg.payload as any).args?.model ?? "").trim()
        if (!model) return
        await postCommand(bridge.baseUrl, state.activeSessionId, { command: "set_model", payload: { model } })
        state.currentModel = model
        sendState()
        sendTranscript(`\n[command] set_model ${model}\n`)
        return
      }
      if (name === "session_child_next" || name === "session_child_previous" || name === "session_parent") {
        if (!bridge) throw new Error("bridge missing")
        if (!state.activeSessionId) throw new Error("no active session")
        const bridgeCommand: SessionNavCommand =
          name === "session_child_next"
            ? "session_child_next"
            : name === "session_child_previous"
              ? "session_child_previous"
              : "session_parent"
        const maybeChild = String((msg.payload as any).args?.child_session_id ?? "").trim()
        const maybeParent = String((msg.payload as any).args?.parent_session_id ?? "").trim()
        const payload =
          maybeChild || maybeParent
            ? ({
                child_session_id: maybeChild || null,
                parent_session_id: maybeParent || null,
              } satisfies Record<string, unknown>)
            : null
        try {
          const response = await postCommand(bridge.baseUrl, state.activeSessionId, {
            command: bridgeCommand,
            payload,
          })
          const targetSessionId = resolveCommandTargetSessionId(bridgeCommand, response.detail ?? null)
          const previousSessionId = state.activeSessionId
          if (targetSessionId && targetSessionId !== previousSessionId) {
            state.activeSessionId = targetSessionId
            sendState()
            await startSse(targetSessionId)
            sendTranscript(`\n[session] switched ${previousSessionId} -> ${targetSessionId}\n`)
          }
          sendTranscript(
            `\n[command] ${bridgeCommand}${payload ? ` child=${maybeChild || "-"} parent=${maybeParent || "-"}` : ""}\n`,
          )
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error)
          sendTranscript(`\n[command] ${bridgeCommand} unavailable (${detail})\n`)
        }
      }
      return
    }
  }

  const shutdown = async (reason: string) => {
    shuttingDown = true
    if (contextBurst) {
      flushContextBurst()
    }
    state.status = "stopped"
    sendToUi(nowEnvelope("ctrl.shutdown", { reason }))
    sendState()
    if (sseAbort) {
      sseAbort.abort()
      sseAbort = null
    }
    if (bridge?.child) {
      try {
        bridge.child.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
      } catch {
        // ignore
      }
    }
    if (uiChild) {
      try {
        uiChild.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
      } catch {
        // ignore
      }
    }
    await ipcServer.close().catch(() => undefined)
    if (stopResolve) {
      stopResolve()
      stopResolve = null
    }
  }

  process.on("SIGINT", () => void shutdown("SIGINT"))
  process.on("SIGTERM", () => void shutdown("SIGTERM"))

  await startBridge()
  await startUi()

  if (args.task && bridge) {
    await safeAppendLog(logPath, `[task] auto-submit enabled`)
    const conn = await connectIpcClient({ host: ipcServer.host, port: ipcServer.port, timeoutMs: 2000 }).catch(() => null)
    conn?.close()
    await handleUiMessage(nowEnvelope("ui.submit", { text: args.task }, { id: "auto" }) as any)
  }

  if (Number.isFinite(args.exitAfterMs) && (args.exitAfterMs as number) > 0) {
    setTimeout(() => void shutdown("exit-after-ms"), args.exitAfterMs as number)
  }

  await new Promise<void>((resolve) => {
    stopResolve = resolve
  })
}

await main()
