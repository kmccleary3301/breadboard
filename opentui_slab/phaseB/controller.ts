// NOTE: Recreated after repo reset; see docs_tmp/cli_phase_3 for Phase B/C notes.
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

const restoreTerminalModes = () => {
  if (!process.stdout.isTTY) return
  try {
    process.stdout.write(
      "\u001b[?2004l\u001b[?1000l\u001b[?1002l\u001b[?1003l\u001b[?1006l\u001b[?1015l\u001b[?25h",
    )
  } catch {
    // ignore
  }
}

const readyMarkerPathRaw = (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim()
const readyMarkerPath = readyMarkerPathRaw
  ? path.isAbsolute(readyMarkerPathRaw)
    ? readyMarkerPathRaw
    : path.resolve(process.cwd(), readyMarkerPathRaw)
  : ""

const writeReadyMarker = async (label: string, extra?: Record<string, unknown>) => {
  if (!readyMarkerPath) return
  const payload = { ts: Date.now(), label, ...(extra ?? {}) }
  await safeAppendLog(readyMarkerPath, JSON.stringify(payload))
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
  draftText: string
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
    const candidates = [path.resolve(process.cwd(), trimmed), path.resolve(engineRoot, trimmed)]
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
	    draftText: "",
	    pendingPermissions: [],
	  }
  const permissionMode = args.permissionMode?.trim() || process.env.BREADBOARD_PERMISSION_MODE?.trim() || ""

  let uiChild: ChildProcess | null = null
  let bridge: BridgeHandle | null = null
  let activeUiConn: IpcConnection | null = null
  let shuttingDown = false
  let debugStreamTimer: NodeJS.Timeout | null = null

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

  let cachedDefaultModel: string | null = null
  let cachedModelProviders: string[] | null = null

	  const sendState = () => {
	    sendToUi(
	      nowEnvelope("ctrl.state", {
	        active_session_id: state.activeSessionId,
	        base_url: state.baseUrl,
	        config_path: state.configPath,
	        current_model: state.currentModel,
	        model_default: cachedDefaultModel,
	        model_providers: cachedModelProviders,
	        draft_text: state.draftText,
	        status: state.status,
	        pending_permissions: state.pendingPermissions,
	      }),
	    )
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
    ["1", "true", "yes", "on"].includes(String(process.env.BREADBOARD_DEBUG_FAKE_PERMISSION ?? "").trim().toLowerCase())

  const debugFakeStream =
    ["1", "true", "yes", "on"].includes(String(process.env.BREADBOARD_DEBUG_FAKE_STREAM ?? "").trim().toLowerCase())

  const COMMAND_ITEMS: ReadonlyArray<PaletteItem> = [
    { id: "stop_run", title: "Stop run", detail: "Interrupt current run (stop)" },
    { id: "retry_last", title: "Retry", detail: "Restart stream (retry)" },
    { id: "status", title: "Status", detail: "Fetch session status summary" },
    { id: "help", title: "Help / keybinds", detail: "Show keybind reference" },
    { id: "save_transcript", title: "Save transcript", detail: "Write buffered transcript to artifacts" },
    ...(debugFakePermission
      ? ([{ id: "debug_permission", title: "Debug permission", detail: "Inject a fake permission request" }] satisfies ReadonlyArray<PaletteItem>)
      : []),
    ...(debugFakeStream
      ? ([{ id: "debug_stream", title: "Debug stream", detail: "Emit fake streaming output to stdout" }] satisfies ReadonlyArray<PaletteItem>)
      : []),
  ]

  const pendingPaletteRequests = new Map<PaletteKind, string>()

  let modelCatalogCache: { fetchedAtMs: number; catalog: ModelCatalogResponse } | null = null

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
    cachedDefaultModel = typeof catalog.default_model === "string" ? catalog.default_model.trim() : null
    const modelsAll = Array.isArray(catalog.models) ? catalog.models : []
    const providers = new Set<string>()
    for (const model of modelsAll) {
      const raw = String((model as any)?.provider ?? "").trim().toLowerCase()
      if (raw) providers.add(raw)
    }
    cachedModelProviders = Array.from(providers).sort((a, b) => a.localeCompare(b))
    if (!state.currentModel) {
      const defaultModel = cachedDefaultModel?.trim() || ""
      if (defaultModel.trim()) {
        state.currentModel = defaultModel.trim()
        sendState()
      }
    }
    return catalog
  }

  const filePickerMaxResultsRaw = Number(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_RESULTS ?? "")
  const filePickerMaxIndexFilesRaw = Number(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_INDEX_FILES ?? "")
  const filePickerIndexNodeModules =
    String(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_NODE_MODULES ?? "").trim().toLowerCase() === "true"
  const filePickerIndexHiddenDirs =
    String(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_HIDDEN_DIRS ?? "").trim().toLowerCase() === "true"
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
    if (!filePickerIndexHiddenDirs && name.startsWith(".") && name !== ".github") return true
    if (name === ".git") return true
    if (name === "__pycache__") return true
    if (name === ".venv") return true
    if (name === ".pytest_cache") return true
    if (name === ".mypy_cache") return true
    if (name === ".ruff_cache") return true
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
    await safeAppendLog(
      logPath,
      `[files] indexed count=${files.length} truncated=${truncated} root=${workspaceRoot} ms=${Date.now() - started}`,
    )
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

  const formatBytes = (bytes: number): string => {
    const value = Math.max(0, bytes)
    if (value < 1024) return `${value} B`
    const kb = value / 1024
    if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`
    const mb = kb / 1024
    if (mb < 1024) return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`
    const gb = mb / 1024
    return `${gb.toFixed(gb < 10 ? 1 : 0)} GB`
  }

  const mapLimit = async <T, R>(items: ReadonlyArray<T>, limit: number, fn: (item: T) => Promise<R>): Promise<R[]> => {
    const results: R[] = new Array(items.length)
    let index = 0
    const workers = new Array(Math.max(1, Math.min(limit, items.length))).fill(0).map(async () => {
      while (index < items.length) {
        const i = index
        index += 1
        results[i] = await fn(items[i] as T)
      }
    })
    await Promise.all(workers)
    return results
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
        const providerMatch = query.match(/\bprovider:([^\s]+)\b/i)
        const providerNeedle = providerMatch?.[1]?.trim().toLowerCase() || ""
        const pageMatch = query.match(/\bpage:(\d+)\b/i)
        const pageRaw = pageMatch?.[1]?.trim() ?? ""
        const pageParsed = pageRaw ? Number(pageRaw) : 1
        const page = Number.isFinite(pageParsed) && pageParsed > 0 ? Math.floor(pageParsed) : 1
        const pageSizeRaw = Number(process.env.BREADBOARD_MODEL_PICKER_PAGE_SIZE ?? "")
        const pageSize = Number.isFinite(pageSizeRaw) && pageSizeRaw > 0 ? Math.floor(pageSizeRaw) : 50
        const queryNoProvider = providerMatch ? query.replace(providerMatch[0], "").trim() : query
        const queryWithoutProvider = pageMatch ? queryNoProvider.replace(pageMatch[0], "").trim() : queryNoProvider

        const catalog = await loadModelCatalogCached()
        const defaultModel = typeof catalog.default_model === "string" ? catalog.default_model.trim() : ""
        const modelsAll = Array.isArray(catalog.models) ? catalog.models : []
        const models = providerNeedle
          ? modelsAll.filter((m) => String(m.provider ?? "").toLowerCase().includes(providerNeedle))
          : modelsAll
        const itemsUnfiltered: PaletteItem[] = models.map((model) => {
          const providerLabel = normalizeProviderLabel(model.provider ?? null)
          const displayName = (model.name ?? "").trim() || model.id
          const ctx = typeof model.context_length === "number" ? model.context_length : null
          const ctxK = ctx != null ? Math.max(1, Math.round(ctx / 1000)) : null
          const flags: string[] = []
          if (state.currentModel && model.id === state.currentModel) flags.push("current")
          if (defaultModel && model.id === defaultModel) flags.push("default")
          const prefix = flags.length > 0 ? `[${flags.join(",")}] ` : ""
          return {
            id: model.id,
            title: `${prefix}${providerLabel} · ${displayName}`,
            detail: ctxK != null ? `${ctxK}k ctx` : null,
            group: providerLabel,
            meta: { provider: model.provider ?? null },
          }
        })
        const scoredAll = queryWithoutProvider
          ? itemsUnfiltered
              .map((item) => {
                const score = scoreFuzzy(`${item.title} ${item.detail ?? ""} ${item.id}`, queryWithoutProvider)
                return score == null ? null : { item, score }
              })
              .filter((entry): entry is { item: PaletteItem; score: number } => entry != null)
              .sort((a, b) => (b.score !== a.score ? b.score - a.score : a.item.title.localeCompare(b.item.title)))
              .map((entry) => entry.item)
          : itemsUnfiltered

        const totalMatches = scoredAll.length
        const start = Math.min(totalMatches, Math.max(0, (page - 1) * pageSize))
        const end = Math.min(totalMatches, start + pageSize)
        const pageItems = scoredAll.slice(start, end)

        sendPaletteItems(kind, query, pageItems)
        const pageCount = Math.max(1, Math.ceil(totalMatches / pageSize))
        const rangeLabel = totalMatches ? `${start + 1}-${end}` : "0-0"
        const providerLabel = providerNeedle ? ` provider:${providerNeedle}` : ""
        sendPaletteStatus(
          kind,
          "ready",
          totalMatches
            ? `${rangeLabel} of ${totalMatches} (page ${Math.min(page, pageCount)}/${pageCount})${providerLabel}`
            : "No matches",
        )
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
        const warnBytesRaw = Number(process.env.BREADBOARD_FILE_PICKER_WARN_BYTES ?? "")
        const warnBytes =
          Number.isFinite(warnBytesRaw) && warnBytesRaw > 0 ? Math.floor(warnBytesRaw) : 1_000_000
        const stats = await mapLimit(filtered, 12, async (p) => {
          try {
            const absPath = path.join(workspaceRoot, p)
            const st = await fs.promises.stat(absPath)
            let isBinary: boolean | null = null
            try {
              const fh = await fs.promises.open(absPath, "r")
              try {
                const buf = Buffer.alloc(1024)
                const { bytesRead } = await fh.read(buf, 0, buf.length, 0)
                isBinary = buf.subarray(0, bytesRead).includes(0)
              } finally {
                await fh.close()
              }
            } catch {
              isBinary = null
            }
            return { size: st.size, isBinary }
          } catch {
            return null
          }
        })
        const items: PaletteItem[] = filtered.map((p, i) => {
          const info = stats[i]
          const size = info && typeof (info as any).size === "number" ? Number((info as any).size) : null
          const isBinary =
            info && typeof (info as any).isBinary === "boolean" ? Boolean((info as any).isBinary) : null
          const sizeLabel = size != null ? formatBytes(size) : null
          const parts: string[] = []
          if (sizeLabel) parts.push(sizeLabel)
          if (size != null && size >= warnBytes) parts.push("large")
          if (isBinary === true) parts.push("binary")
          if (isBinary === false) parts.push("text")
          const detail = parts.length > 0 ? parts.join(" · ") : null
          return {
            id: p,
            title: p,
            detail,
            meta:
              size != null
                ? { size_bytes: size, large: size >= warnBytes, is_binary: isBinary }
                : isBinary != null
                  ? { is_binary: isBinary }
                  : null,
          }
        })
        sendPaletteItems(kind, query, items)
        const suffix = index.truncated ? " (truncated)" : ""
        const warnLabel = formatBytes(warnBytes)
        sendPaletteStatus(kind, "ready", `${items.length} item(s) · indexed=${index.files.length}${suffix} · warn>=${warnLabel}`)
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

  const ipcServer = await createIpcServer({ host: "127.0.0.1" })
  await safeAppendLog(logPath, `[ipc] listening ${ipcServer.host}:${ipcServer.port}`)
  if (args.printIpc) {
    console.error(JSON.stringify({ ipc: { host: ipcServer.host, port: ipcServer.port }, run_dir: runDir }))
  }

  ipcServer.onConnection((conn) => {
    void safeAppendLog(logPath, `[ipc] ui connected ${conn.remote}`)
    void writeReadyMarker("ui_connected", { remote: conn.remote })
    if (activeUiConn) {
      activeUiConn.close()
    }
    activeUiConn = conn

    conn.send(
      nowEnvelope("ctrl.hello", {
        controller_version: "phaseB-0.2",
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
      setTimeout(() => {
        if (!shuttingDown) void startUi()
      }, 300)
    })
  }

  const startBridge = async () => {
    const baseUrlHint = args.baseUrl?.trim() || ""
    if (args.externalBridge) {
      if (!baseUrlHint) throw new Error("--external-bridge requires --base-url")
      const ok = await healthCheck(baseUrlHint)
      if (!ok) throw new Error(`External bridge not reachable at ${baseUrlHint}`)
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
    void writeReadyMarker("bridge_ready", { base_url: bridge.baseUrl })
    await flushPendingPalettes()
  }

  let sseAbort: AbortController | null = null
  let stopResolve: (() => void) | null = null

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
    sendToUi(nowEnvelope("ctrl.event", { event: evt as unknown as Record<string, unknown> }))
    const rendered = formatBridgeEventForStdout(evt as unknown as Record<string, unknown>)
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
      state.status = "idle"
      sendState()
    }
  }

  const handleUiMessage = async (msg: UIToControllerMessage) => {
    if (msg.type === "ui.ready") {
      sendState()
      return
    }
    if (msg.type === "ui.draft.update") {
      const text = String((msg.payload as any).text ?? "")
      if (text !== state.draftText) {
        state.draftText = text
        sendState()
      }
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
      if (state.draftText) {
        state.draftText = ""
        sendState()
      }

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
      let bridgeError: string | null = null
      try {
        if (bridge && state.activeSessionId && !requestId.startsWith("debug-")) {
          await postCommand(bridge.baseUrl, state.activeSessionId, {
            command: "permission_decision",
            payload: permissionPayload,
          })
        }
      } catch (err) {
        bridgeError = (err as Error).message
      }

      state.pendingPermissions = state.pendingPermissions.filter((p) => p.request_id !== requestId)
      sendState()

      const scopeLabel = typeof (permissionPayload as any).scope === "string" ? String((permissionPayload as any).scope) : ""
      const ruleLabel = typeof (permissionPayload as any).rule === "string" ? String((permissionPayload as any).rule) : ""
      const extras: string[] = []
      if (scopeLabel) extras.push(`scope=${scopeLabel}`)
      if (ruleLabel) extras.push(`rule=${ruleLabel}`)
      if ((permissionPayload as any).stop === true) extras.push("stop=true")
      const extrasLabel = extras.length ? ` (${extras.join(" ")})` : ""

      sendTranscript(
        `\n[permission] decision ${decision}${extrasLabel}${bridgeError ? ` (bridge error: ${bridgeError})` : ""}\n`,
      )
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
        if (!state.activeSessionId) {
          sendTranscript(`\n[command] stop (no active session)\n`)
          return
        }
        if (!bridge) {
          sendTranscript(`\n[command] stop (bridge missing)\n`)
          return
        }
        let bridgeError: string | null = null
        try {
          await postCommand(bridge.baseUrl, state.activeSessionId, { command: "stop" })
        } catch (err) {
          bridgeError = (err as Error).message
        }
        sendTranscript(`\n[command] stop${bridgeError ? ` (bridge error: ${bridgeError})` : ""}\n`)
        return
      }
      if (name === "retry_last") {
        if (!state.activeSessionId) {
          sendTranscript(`\n[command] retry (no active session)\n`)
          return
        }
        if (!bridge) {
          sendTranscript(`\n[command] retry (bridge missing)\n`)
          return
        }
        let bridgeError: string | null = null
        try {
          await postCommand(bridge.baseUrl, state.activeSessionId, { command: "retry" })
        } catch (err) {
          bridgeError = (err as Error).message
        }
        sendTranscript(`\n[command] retry${bridgeError ? ` (bridge error: ${bridgeError})` : ""}\n`)
        return
      }
      if (name === "status") {
        if (!state.activeSessionId) {
          sendTranscript(`\n[command] status (no active session)\n`)
          return
        }
        if (!bridge) {
          sendTranscript(`\n[command] status (bridge missing)\n`)
          return
        }
        let bridgeError: string | null = null
        try {
          await postCommand(bridge.baseUrl, state.activeSessionId, { command: "status" })
        } catch (err) {
          bridgeError = (err as Error).message
        }
        sendTranscript(`\n[command] status${bridgeError ? ` (bridge error: ${bridgeError})` : ""}\n`)
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
	            args: { command: "echo hello", cwd: workspaceRoot },
	            diff_preview: [
	              "--- a/demo.txt",
	              "+++ b/demo.txt",
	              "@@",
	              "-old line",
	              "+new line",
	              "",
	            ].join("\n"),
	            defaultScope: "session",
	            ruleSuggestion: "shell:*",
	            createdAt: Date.now(),
	          },
	        } as unknown as BridgeEvent)
	        sendTranscript(`\n[permission] debug request_id=${requestId}\n`)
	        return
	      }
      if (name === "debug_stream") {
        if (!debugFakeStream) return
        if (debugStreamTimer) {
          clearInterval(debugStreamTimer)
          debugStreamTimer = null
        }
        sendTranscript(`\n[debug_stream] start\n`)
        let i = 0
        debugStreamTimer = setInterval(() => {
          if (shuttingDown) {
            if (debugStreamTimer) clearInterval(debugStreamTimer)
            debugStreamTimer = null
            return
          }
          i += 1
          if (i === 1) {
            sendTranscript(`\n[assistant]\n`)
          }
          sendTranscript(`chunk ${i} lorem ipsum dolor sit amet\n`)
          if (i >= 80) {
            if (debugStreamTimer) clearInterval(debugStreamTimer)
            debugStreamTimer = null
            sendTranscript(`\n[debug_stream] done\n`)
          }
        }, 45)
        return
      }
      if (name === "set_model") {
        const model = String((msg.payload as any).args?.model ?? "").trim()
        if (!model) return
        state.currentModel = model
        sendState()
        if (!state.activeSessionId) {
          sendTranscript(`\n[command] set_model ${model} (no active session)\n`)
          return
        }
        if (!bridge) {
          sendTranscript(`\n[command] set_model ${model} (bridge missing)\n`)
          return
        }
        let bridgeError: string | null = null
        try {
          await postCommand(bridge.baseUrl, state.activeSessionId, { command: "set_model", payload: { model } })
        } catch (err) {
          bridgeError = (err as Error).message
        }
        sendTranscript(`\n[command] set_model ${model}${bridgeError ? ` (bridge error: ${bridgeError})` : ""}\n`)
      }
      return
    }
  }

  const shutdown = async (reason: string) => {
    shuttingDown = true
    restoreTerminalModes()
    void writeReadyMarker("shutdown", { reason })
    state.status = "stopped"
    sendToUi(nowEnvelope("ctrl.shutdown", { reason }))
    sendState()
    if (debugStreamTimer) {
      clearInterval(debugStreamTimer)
      debugStreamTimer = null
    }
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
  process.on("exit", () => restoreTerminalModes())

  await startBridge()
  await startUi()

  if (args.task && bridge) {
    await safeAppendLog(logPath, `[task] auto-submit enabled`)
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
