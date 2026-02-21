import {
  createCliRenderer,
  BoxRenderable,
  InputRenderable,
  SelectRenderable,
  TextRenderable,
  TextareaRenderable,
  type KeyEvent,
} from "@opentui/core"
import { connectIpcClient, type IpcConnection } from "./ipc.ts"
import {
  nowEnvelope,
  type ControllerToUIMessage,
  type PaletteItem,
  type PaletteKind,
  IPC_PROTOCOL_VERSION,
} from "./protocol.ts"
import { formatBridgeEventForStdout } from "./format.ts"
import { createOverlayAdapterState, reduceOverlayAdapterState } from "./overlay_adapter.ts"

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const isCtrlMsg = (value: unknown): value is ControllerToUIMessage =>
  isRecord(value) && typeof value.type === "string" && value.protocol_version === IPC_PROTOCOL_VERSION

const host = process.env.BREADBOARD_IPC_HOST?.trim() || "127.0.0.1"
const portRaw = Number(process.env.BREADBOARD_IPC_PORT ?? "")
if (!Number.isFinite(portRaw) || portRaw <= 0) {
  throw new Error("Missing/invalid BREADBOARD_IPC_PORT for UI process.")
}
const port = portRaw

let conn: IpcConnection | null = null
conn = await connectIpcClient({ host, port, timeoutMs: 25_000 })
conn.send(nowEnvelope("ui.ready", {}))

const renderer = await createCliRenderer({
  useAlternateScreen: false,
  useConsole: false,
  experimental_splitHeight: 12,
  targetFps: 20,
  maxFps: 30,
})

const inferredTerminalWidth = renderer.width || 80
const inferredTerminalHeight = ((renderer as any).renderOffset ?? 0) + renderer.height || 24
const terminalWidth = process.stdout.columns && process.stdout.columns > 0 ? process.stdout.columns : inferredTerminalWidth
const terminalHeight = process.stdout.rows && process.stdout.rows > 0 ? process.stdout.rows : inferredTerminalHeight
const currentTerminalWidth = (renderer as any)._terminalWidth as number | undefined
const currentTerminalHeight = (renderer as any)._terminalHeight as number | undefined
if (!currentTerminalWidth || currentTerminalWidth <= 0) {
  ;(renderer as any)._terminalWidth = terminalWidth
}
if (!currentTerminalHeight || currentTerminalHeight <= 0) {
  ;(renderer as any)._terminalHeight = terminalHeight
}

const splitHeight = 12
const input = new TextareaRenderable(renderer, {
  id: "composer",
  position: "absolute",
  left: 0,
  top: 0,
  width: "100%",
  height: splitHeight - 3,
  placeholder: "Enter submit · Shift+Enter newline · Ctrl+D exits",
  wrapMode: "word",
  cursorStyle: { style: "block", blinking: false },
})

type PaletteState = {
  kind: PaletteKind
  query: string
  items: ReadonlyArray<PaletteItem>
  status: "idle" | "loading" | "ready" | "error"
  statusMessage: string | null
}

let palette: PaletteState | null = null

type PermissionOverlayState = {
  requestId: string
  context: Record<string, unknown>
}

let permissionOverlay: PermissionOverlayState | null = null

type TaskOverlayState = {
  query: string
}

let taskOverlay: TaskOverlayState | null = null

const overlay = new BoxRenderable(renderer, {
  id: "overlay",
  position: "absolute",
  left: 0,
  top: 0,
  width: "100%",
  height: splitHeight - 1,
  zIndex: 10,
  border: true,
  borderColor: "#3f3f46",
  focusedBorderColor: "#7CF2FF",
  paddingLeft: 1,
  paddingRight: 1,
  paddingTop: 0,
  paddingBottom: 0,
  flexDirection: "column",
  visible: false,
})

const overlayTitle = new TextRenderable(renderer, {
  id: "overlay_title",
  width: "100%",
  height: 1,
  content: "",
  fg: "#d4d4d8",
})

const overlayQuery = new InputRenderable(renderer, {
  id: "overlay_query",
  width: "100%",
  height: 1,
  placeholder: "Type to filter…",
  focusedBackgroundColor: "#111827",
  focusedTextColor: "#f9fafb",
})

const overlayList = new SelectRenderable(renderer, {
  id: "overlay_list",
  width: "100%",
  height: "auto",
  flexGrow: 1,
  options: [],
  showScrollIndicator: true,
  wrapSelection: true,
  showDescription: true,
  fastScrollStep: 5,
})

const overlayStatus = new TextRenderable(renderer, {
  id: "overlay_status",
  width: "100%",
  height: 1,
  content: "",
  fg: "#a1a1aa",
})

overlay.add(overlayTitle)
overlay.add(overlayQuery)
overlay.add(overlayList)
overlay.add(overlayStatus)

const footer = new TextRenderable(renderer, {
  id: "footer",
  position: "absolute",
  left: 0,
  top: splitHeight - 1,
  width: "100%",
  height: 1,
  content: "BreadBoard OpenTUI slab (Phase B) — connecting…",
  fg: "#999999",
})

const subagentStrip = new TextRenderable(renderer, {
  id: "subagent_strip",
  position: "absolute",
  left: 0,
  top: splitHeight - 2,
  width: "100%",
  height: 1,
  content: "Subagents · none detected · ctrl+←/→ cycle · ctrl+↑ parent",
  fg: "#7dd3fc",
})

const contextStrip = new TextRenderable(renderer, {
  id: "context_strip",
  position: "absolute",
  left: 0,
  top: splitHeight - 3,
  width: "100%",
  height: 1,
  content: "Context burst · none · ctrl+g toggle",
  fg: "#93c5fd",
})

renderer.root.add(input)
renderer.root.add(overlay)
renderer.root.add(contextStrip)
renderer.root.add(subagentStrip)
renderer.root.add(footer)
input.focus()

let activeSessionId: string | null = null
let baseUrl: string | null = null
let pendingPermissionId: string | null = null
let currentModelId: string | null = null
let lastEventBadge: string | null = null
let lastEventSummary: string | null = null
let lastToolRenderMode: "compact" | "expanded" | null = null
let lastToolRenderReason: string | null = null
let overlayState = createOverlayAdapterState()
let selectedSubagentIndex = 0
let contextBurstExpanded = false
let contextBurstSummary: string | null = null
let contextBurstDetails: string | null = null
const composerHistory: string[] = []
let composerHistoryIndex: number | null = null
let composerHistoryDraft = ""

const truncateInline = (text: string, max = 52): string => {
  const compact = text.replace(/\s+/g, " ").trim()
  if (compact.length <= max) return compact
  return `${compact.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

const refreshFooter = () => {
  const perm = pendingPermissionId
    ? `perm=${pendingPermissionId} (a allow once, r reject)`
    : ""
  const model = currentModelId ? `model=${currentModelId}` : ""
  const overlayHint = overlay.visible
    ? "esc close overlay"
    : "ctrl+k commands · alt+p models · @ files · ctrl+o search · ctrl+t tasks · ctrl+g context · ctrl+←/→ child · ctrl+↑ parent"
  const liveHint =
    lastEventSummary && lastEventSummary.trim()
      ? `${lastEventBadge ?? "event"}: ${truncateInline(lastEventSummary)}`
      : ""
  const taskHint =
    overlayState.taskTotalCount > 0
      ? `tasks ${overlayState.taskCompletedCount}/${overlayState.taskTotalCount} done` +
        (overlayState.taskRunningCount > 0 ? ` (${overlayState.taskRunningCount} running)` : "")
      : ""
  const laneHint =
    overlayState.subagentOrder.length > 0
      ? `subagents ${overlayState.subagentOrder.length}`
      : ""
  const toolHint = lastToolRenderMode
    ? `tool=${lastToolRenderMode}${lastToolRenderReason ? `(${truncateInline(lastToolRenderReason, 24)})` : ""}`
    : ""
  const primaryBits = [toolHint, taskHint, laneHint, liveHint].filter(Boolean)
  const contextBits = [
    `session=${activeSessionId ?? "none"}`,
    model,
    perm,
    baseUrl ? `bridge=${baseUrl}` : "",
  ].filter(Boolean)
  footer.content = `OpenTUI slab (Phase C) · ${primaryBits.join(" · ") || "idle"} · ${contextBits.join(" · ")} · ${overlayHint}`

  const subagentIds = overlayState.subagentOrder
  if (subagentIds.length > 0) {
    if (selectedSubagentIndex >= subagentIds.length) selectedSubagentIndex = subagentIds.length - 1
    if (selectedSubagentIndex < 0) selectedSubagentIndex = 0
    const activeId = subagentIds[selectedSubagentIndex]!
    const active = overlayState.subagentById[activeId]
    const preview = subagentIds.slice(0, 4).map((id, idx) => {
      const item = overlayState.subagentById[id]
      const label = item?.label ?? id
      const running = item?.runningCount ?? 0
      const taskCount = item?.taskCount ?? 0
      const bit = `${label} ${running}/${taskCount}`
      return idx === selectedSubagentIndex ? `[${bit}]` : bit
    })
    const activeLabel = active?.label ?? activeId
    const activeRunning = active?.runningCount ?? 0
    const activeTaskCount = active?.taskCount ?? 0
    subagentStrip.content = `Subagents [${selectedSubagentIndex + 1}/${subagentIds.length}] active=${activeLabel} ${activeRunning}/${activeTaskCount} · ${preview.join(" · ")} · ctrl+←/→ cycle · ctrl+↑ parent`
  } else {
    selectedSubagentIndex = 0
    subagentStrip.content = "Subagents · none detected · ctrl+←/→ cycle · ctrl+↑ parent"
  }

  if (!contextBurstSummary) {
    contextStrip.content = "Context burst · none · ctrl+g toggle"
  } else if (contextBurstExpanded && contextBurstDetails) {
    contextStrip.content = `Context burst [expanded] · ${truncateInline(contextBurstDetails, 180)} · ctrl+g collapse`
  } else {
    contextStrip.content = `Context burst [collapsed] · ${truncateInline(contextBurstSummary, 180)} · ctrl+g expand`
  }
}

const buildTaskOverlayOptions = (query: string) => {
  const needle = query.trim().toLowerCase()
  const entries = Object.entries(overlayState.taskById)
  const rows = entries
    .map(([taskId, row]) => ({
      taskId,
      status: row?.status ?? "unknown",
      description: row?.description ?? "",
      subagentLabel: row?.subagentLabel ?? row?.laneLabel ?? "",
      subagentId: row?.subagentId ?? row?.laneId ?? "",
    }))
    .filter((row) => {
      if (!needle) return true
      return `${row.taskId} ${row.status} ${row.description} ${row.subagentLabel} ${row.subagentId}`
        .toLowerCase()
        .includes(needle)
    })
    .sort((a, b) => `${a.subagentLabel}:${a.taskId}`.localeCompare(`${b.subagentLabel}:${b.taskId}`))

  return rows.map((row) => ({
    name: `${row.status.toUpperCase()} · ${row.subagentLabel || "main"} · ${row.taskId}`,
    description: row.description || row.subagentLabel || row.subagentId || "",
    value: row,
  }))
}

const closeOverlay = () => {
  if (!overlay.visible) return
  if (taskOverlay) {
    overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.task.close" })
  } else {
    overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.palette.close" })
  }
  overlay.visible = false
  palette = null
  permissionOverlay = null
  taskOverlay = null
  overlayTitle.content = ""
  overlayQuery.placeholder = "Type to filter…"
  overlayQuery.value = ""
  overlayList.options = []
  overlayStatus.content = ""
  input.focus()
  refreshFooter()
}

const openOverlay = (kind: PaletteKind, query = "") => {
  overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.palette.open", kind })
  palette = { kind, query, items: [], status: "loading", statusMessage: null }
  permissionOverlay = null
  taskOverlay = null
  overlay.visible = true
  overlayTitle.content =
    kind === "commands"
      ? "Commands"
      : kind === "models"
        ? "Models"
        : kind === "files"
          ? "Files"
          : "Search"
  overlayQuery.placeholder = "Type to filter…"
  overlayQuery.value = query
  overlayStatus.content = "Loading…"
  overlayQuery.focus()
  overlayList.options = []
  conn?.send(nowEnvelope("ui.palette.open", { kind, query }))
  refreshFooter()
}

const openTaskOverlay = (query = "") => {
  overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.task.open" })
  palette = null
  permissionOverlay = null
  taskOverlay = { query }
  overlay.visible = true
  overlayTitle.content = "Tasks"
  overlayQuery.placeholder = "Filter tasks…"
  overlayQuery.value = query
  const options = buildTaskOverlayOptions(query)
  overlayList.options = options
  overlayList.setSelectedIndex(0)
  overlayStatus.content =
    options.length > 0
      ? `${overlayState.taskCompletedCount}/${overlayState.taskTotalCount} complete · ${overlayState.taskRunningCount} running`
      : "No tasks"
  overlayQuery.focus()
  refreshFooter()
}

type PermissionChoice = {
  readonly title: string
  readonly detail: string
  readonly decision: string
  readonly scope?: string
  readonly rule?: string | null
  readonly stop?: boolean
}

const openPermissionOverlay = (requestId: string, context: Record<string, unknown>) => {
  overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.permission.open", requestId })
  permissionOverlay = { requestId, context }
  palette = null
  taskOverlay = null
  overlay.visible = true
  overlayTitle.content = "Permission request"
  overlayQuery.placeholder = "Optional note (sent with decision)…"
  overlayQuery.value = ""

  const tool = typeof (context as any).tool === "string" ? String((context as any).tool) : ""
  const summary = typeof (context as any).summary === "string" ? String((context as any).summary) : ""
  overlayStatus.content = `${tool || "tool"}${summary ? ` · ${summary}` : ""}`

  const defaultScope =
    typeof (context as any).defaultScope === "string"
      ? String((context as any).defaultScope)
      : typeof (context as any).default_scope === "string"
        ? String((context as any).default_scope)
        : "session"
  const ruleSuggestion =
    typeof (context as any).ruleSuggestion === "string"
      ? String((context as any).ruleSuggestion)
      : typeof (context as any).rule_suggestion === "string"
        ? String((context as any).rule_suggestion)
        : null

  const choices: PermissionChoice[] = [
    { title: "Allow once", detail: "allow-once", decision: "allow-once" },
    { title: "Allow always", detail: `allow-always (${defaultScope})`, decision: "allow-always", scope: defaultScope, rule: ruleSuggestion },
    { title: "Deny once", detail: "deny-once", decision: "deny-once" },
    { title: "Deny always", detail: `deny-always (${defaultScope})`, decision: "deny-always", scope: defaultScope, rule: ruleSuggestion },
    { title: "Deny + stop", detail: "deny-stop (stop run)", decision: "deny-stop", stop: true },
  ]

  overlayList.options = choices.map((c) => ({ name: c.title, description: c.detail, value: c }))
  overlayList.setSelectedIndex(0)
  overlayQuery.focus()
  refreshFooter()
}

const applyPaletteItems = (items: ReadonlyArray<PaletteItem>) => {
  overlayList.options = items.map((item) => ({
    name: item.title,
    description: item.detail ? String(item.detail) : "",
    value: item,
  }))
  overlayList.setSelectedIndex(0)
}

const onOverlayQueryUpdate = () => {
  if (!overlay.visible) return
  if (taskOverlay) {
    const query = overlayQuery.value ?? ""
    taskOverlay = { query }
    const options = buildTaskOverlayOptions(query)
    overlayList.options = options
    overlayList.setSelectedIndex(0)
    overlayStatus.content =
      options.length > 0
        ? `${overlayState.taskCompletedCount}/${overlayState.taskTotalCount} complete · ${overlayState.taskRunningCount} running`
        : "No matches"
    return
  }
  if (!palette) return
  const query = overlayQuery.value ?? ""
  palette = { ...palette, query, status: "loading", statusMessage: null }
  overlayStatus.content = "Loading…"
  conn?.send(nowEnvelope("ui.palette.query", { kind: palette.kind, query }))
}

overlayQuery.on("input", onOverlayQueryUpdate)
overlayQuery.on("change", onOverlayQueryUpdate)

overlayList.on("itemSelected", (_index: number, opt: any) => {
  if (permissionOverlay) {
    const choice = opt?.value as PermissionChoice | undefined
    if (!choice) return
    const note = (overlayQuery.value ?? "").trim()
    conn?.send(
      nowEnvelope("ui.permission.respond", {
        request_id: permissionOverlay.requestId,
        decision: choice.decision,
        note: note || null,
        scope: choice.scope ?? null,
        rule: choice.rule ?? null,
        stop: typeof choice.stop === "boolean" ? choice.stop : null,
      }),
    )
    pendingPermissionId = null
    overlayState = reduceOverlayAdapterState(overlayState, {
      type: "ui.permission.close",
      requestId: permissionOverlay.requestId,
    })
    closeOverlay()
    return
  }

  if (taskOverlay) {
    const row = opt?.value as { taskId: string; status: string; description?: string } | undefined
    overlayStatus.content = row
      ? `Selected ${row.taskId} · ${row.status}${row.description ? ` · ${truncateInline(row.description, 42)}` : ""}`
      : overlayStatus.content
    return
  }

  const item = opt?.value as PaletteItem | undefined
  if (!item || !palette) return
  if (palette.kind === "files") {
    input.insertText(`@${item.id} `)
    closeOverlay()
    return
  }
  if (palette.kind === "models") {
    conn?.send(nowEnvelope("ui.command", { name: "set_model", args: { model: item.id } }))
    closeOverlay()
    return
  }
  if (palette.kind === "commands") {
    conn?.send(nowEnvelope("ui.command", { name: item.id }))
    closeOverlay()
    return
  }
  if (palette.kind === "transcript_search") {
    closeOverlay()
    return
  }
})

const shutdown = () => {
  try {
    conn?.send(nowEnvelope("ui.shutdown", {}))
  } catch {
    // ignore
  }
  renderer.destroy()
  try {
    conn?.close()
  } catch {
    // ignore
  }
}

renderer.keyInput.on("keypress", (key: KeyEvent) => {
  if (key.ctrl && key.name === "d") {
    shutdown()
    return
  }
  if (key.ctrl && key.name === "r") {
    conn?.send(nowEnvelope("ui.command", { name: "restart_ui" }))
    renderer.destroy()
    try {
      conn?.close()
    } catch {}
    process.exit(0)
  }

  if (overlay.visible) {
    if (key.name === "escape" || key.name === "esc") {
      key.preventDefault()
      if (permissionOverlay) {
        conn?.send(
          nowEnvelope("ui.permission.respond", {
            request_id: permissionOverlay.requestId,
            decision: "deny-stop",
            stop: true,
            note: (overlayQuery.value ?? "").trim() || null,
          }),
        )
        pendingPermissionId = null
        overlayState = reduceOverlayAdapterState(overlayState, {
          type: "ui.permission.close",
          requestId: permissionOverlay.requestId,
        })
      }
      closeOverlay()
      return
    }
    if (key.name === "down") {
      key.preventDefault()
      overlayList.moveDown(1)
      return
    }
    if (key.name === "up") {
      key.preventDefault()
      overlayList.moveUp(1)
      return
    }
    if (key.name === "pagedown") {
      key.preventDefault()
      overlayList.moveDown(5)
      return
    }
    if (key.name === "pageup") {
      key.preventDefault()
      overlayList.moveUp(5)
      return
    }
    if (key.name === "return" || key.name === "enter") {
      key.preventDefault()
      overlayList.selectCurrent()
      return
    }
  }

  if (key.ctrl && key.name === "k") {
    key.preventDefault()
    openOverlay("commands", "")
    return
  }
  if (key.ctrl && key.name === "p") {
    key.preventDefault()
    if (overlay.visible) return
    if (composerHistory.length === 0) return
    if (composerHistoryIndex === null) {
      composerHistoryDraft = input.plainText
      composerHistoryIndex = composerHistory.length - 1
    } else {
      composerHistoryIndex = Math.max(0, composerHistoryIndex - 1)
    }
    input.clear()
    input.insertText(composerHistory[composerHistoryIndex] ?? "")
    input.focus()
    return
  }
  if (key.ctrl && key.name === "n") {
    key.preventDefault()
    if (overlay.visible) return
    if (composerHistoryIndex === null) return
    if (composerHistoryIndex >= composerHistory.length - 1) {
      composerHistoryIndex = null
      input.clear()
      input.insertText(composerHistoryDraft)
      input.focus()
      return
    }
    composerHistoryIndex = composerHistoryIndex + 1
    input.clear()
    input.insertText(composerHistory[composerHistoryIndex] ?? "")
    input.focus()
    return
  }
  if (key.ctrl && key.name === "o") {
    key.preventDefault()
    openOverlay("transcript_search", "")
    return
  }
  if (key.ctrl && key.name === "t") {
    key.preventDefault()
    openTaskOverlay("")
    return
  }
  if (key.ctrl && key.name === "g") {
    key.preventDefault()
    if (contextBurstSummary) {
      contextBurstExpanded = !contextBurstExpanded
      refreshFooter()
    }
    return
  }
  if (key.ctrl && key.name === "left") {
    key.preventDefault()
    const subagentCount = overlayState.subagentOrder.length
    if (subagentCount > 0) {
      selectedSubagentIndex = (selectedSubagentIndex - 1 + subagentCount) % subagentCount
      refreshFooter()
    }
    const activeId = overlayState.subagentOrder[selectedSubagentIndex] ?? null
    const active = activeId ? overlayState.subagentById[activeId] : null
    conn?.send(
      nowEnvelope("ui.command", {
        name: "session_child_previous",
        args: {
          child_session_id: active?.childSessionId ?? activeId,
          parent_session_id: active?.parentSessionId ?? null,
        },
      }),
    )
    return
  }
  if (key.ctrl && key.name === "right") {
    key.preventDefault()
    const subagentCount = overlayState.subagentOrder.length
    if (subagentCount > 0) {
      selectedSubagentIndex = (selectedSubagentIndex + 1) % subagentCount
      refreshFooter()
    }
    const activeId = overlayState.subagentOrder[selectedSubagentIndex] ?? null
    const active = activeId ? overlayState.subagentById[activeId] : null
    conn?.send(
      nowEnvelope("ui.command", {
        name: "session_child_next",
        args: {
          child_session_id: active?.childSessionId ?? activeId,
          parent_session_id: active?.parentSessionId ?? null,
        },
      }),
    )
    return
  }
  if (key.ctrl && key.name === "up") {
    key.preventDefault()
    const activeId = overlayState.subagentOrder[selectedSubagentIndex] ?? null
    const active = activeId ? overlayState.subagentById[activeId] : null
    conn?.send(
      nowEnvelope("ui.command", {
        name: "session_parent",
        args: {
          parent_session_id: active?.parentSessionId ?? null,
          child_session_id: active?.childSessionId ?? activeId,
        },
      }),
    )
    return
  }
  if (key.option && key.name === "p") {
    key.preventDefault()
    openOverlay("models", "")
    return
  }
  if (key.sequence === "@") {
    key.preventDefault()
    openOverlay("files", "")
    return
  }

  if ((key.name === "return" || key.name === "enter") && !key.shift && !key.meta && !key.ctrl) {
    key.preventDefault()
    input.submit()
    return
  }
  if ((key.name === "return" || key.name === "enter") && key.shift && !key.meta && !key.ctrl) {
    key.preventDefault()
    input.newLine()
    return
  }

  if (!pendingPermissionId) return
  if (key.name === "a") {
    conn?.send(nowEnvelope("ui.permission.respond", { request_id: pendingPermissionId, decision: "allow-once" }))
    pendingPermissionId = null
    refreshFooter()
    return
  }
  if (key.name === "r") {
    conn?.send(nowEnvelope("ui.permission.respond", { request_id: pendingPermissionId, decision: "deny-once" }))
    pendingPermissionId = null
    refreshFooter()
  }
})

input.onSubmit = () => {
  const content = input.plainText.trimEnd()
  if (!content.trim()) return

  if (composerHistory.length === 0 || composerHistory[composerHistory.length - 1] !== content) {
    composerHistory.push(content)
    if (composerHistory.length > 200) composerHistory.shift()
  }
  composerHistoryIndex = null
  composerHistoryDraft = ""

  input.clear()
  input.focus()

  process.stdout.write(`\n[user]\n${content}\n`)
  conn?.send(nowEnvelope("ui.submit", { text: content }))
}

conn.onMessage((msg) => {
  if (!isCtrlMsg(msg)) return

  if (msg.type === "ctrl.state") {
    const payload = isRecord(msg.payload) ? msg.payload : {}
    activeSessionId = typeof payload.active_session_id === "string" ? payload.active_session_id : null
    baseUrl = typeof payload.base_url === "string" ? payload.base_url : null
    currentModelId = typeof payload.current_model === "string" ? payload.current_model : null
    const pending = (payload as any).pending_permissions
    if (Array.isArray(pending) && pending.length > 0) {
      const last = pending[pending.length - 1]
      const requestId = isRecord(last) && typeof (last as any).request_id === "string" ? String((last as any).request_id) : ""
      pendingPermissionId = requestId.trim() ? requestId.trim() : null
    } else if (pendingPermissionId) {
      pendingPermissionId = null
      overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.permission.close" })
    }
    refreshFooter()
    return
  }

  if (msg.type === "ctrl.transcript.append") {
    const text = isRecord(msg.payload) && typeof msg.payload.text === "string" ? msg.payload.text : ""
    if (text) process.stdout.write(text)
    return
  }

  if (msg.type === "ctrl.event") {
    const payload = isRecord(msg.payload) ? msg.payload : {}
    const adapterOutput = isRecord((payload as any).adapter_output)
      ? ((payload as any).adapter_output as Record<string, unknown>)
      : null
    const hints = adapterOutput && isRecord(adapterOutput.hints) ? (adapterOutput.hints as Record<string, unknown>) : null
    const adapterText = adapterOutput && typeof adapterOutput.stdout_text === "string" ? adapterOutput.stdout_text : ""
    const summaryText = adapterOutput && typeof adapterOutput.summary_text === "string" ? adapterOutput.summary_text : ""
    const toolRender = adapterOutput && isRecord(adapterOutput.tool_render) ? (adapterOutput.tool_render as Record<string, unknown>) : null
    const contextBlock =
      adapterOutput && isRecord(adapterOutput.context_block)
        ? (adapterOutput.context_block as Record<string, unknown>)
        : null
    const overlayIntent = adapterOutput && isRecord(adapterOutput.overlay_intent) ? (adapterOutput.overlay_intent as Record<string, unknown>) : null
    const normalizedEvent = adapterOutput && isRecord(adapterOutput.normalized_event)
      ? (adapterOutput.normalized_event as Record<string, unknown>)
      : null
    const badgeText = hints && typeof hints.badge === "string" ? hints.badge.trim() : ""
    const normalizedType = normalizedEvent && typeof normalizedEvent.type === "string" ? normalizedEvent.type : ""
    if (normalizedType === "context.burst") {
      const summary =
        (contextBlock && typeof contextBlock.summary === "string" ? contextBlock.summary : "") ||
        (normalizedEvent && typeof normalizedEvent.summary === "string" ? normalizedEvent.summary : "") ||
        summaryText ||
        "context burst"
      const detail =
        (contextBlock && typeof contextBlock.detail === "string" ? contextBlock.detail : "") ||
        normalizedEvent && typeof normalizedEvent.detail === "string" ? normalizedEvent.detail : summary
      contextBurstSummary = summary
      contextBurstDetails = detail
      contextBurstExpanded = false
      lastEventSummary = summary
      lastEventBadge = badgeText || "context"
      refreshFooter()
      if (adapterText) {
        process.stdout.write(adapterText)
      }
      return
    }
    if (toolRender) {
      const mode = typeof toolRender.mode === "string" ? toolRender.mode : ""
      const reason = typeof toolRender.reason === "string" ? toolRender.reason : ""
      lastToolRenderMode = mode === "expanded" ? "expanded" : mode === "compact" ? "compact" : null
      lastToolRenderReason = reason || null
    }
    overlayState = reduceOverlayAdapterState(overlayState, {
      type: "event.normalized",
      normalizedEvent,
      overlayIntent,
      summaryText: summaryText || null,
    })
    if (taskOverlay && overlay.visible) {
      const query = overlayQuery.value ?? taskOverlay.query
      taskOverlay = { query }
      const options = buildTaskOverlayOptions(query)
      overlayList.options = options
      overlayList.setSelectedIndex(0)
      overlayStatus.content =
        options.length > 0
          ? `${overlayState.taskCompletedCount}/${overlayState.taskTotalCount} complete · ${overlayState.taskRunningCount} running`
          : "No tasks"
    }
    if (summaryText) {
      lastEventSummary = summaryText
      lastEventBadge = badgeText || null
      refreshFooter()
    } else if (toolRender || overlayIntent || normalizedEvent) {
      refreshFooter()
    }
    const event = isRecord(payload.event) ? (payload.event as Record<string, unknown>) : null
    if (adapterText) {
      process.stdout.write(adapterText)
      return
    }
    if (!event) return
    const rendered = formatBridgeEventForStdout(event)
    if (rendered) process.stdout.write(rendered)
    return
  }

  if (msg.type === "ctrl.palette.status") {
    if (!palette) return
    const payload = isRecord(msg.payload) ? msg.payload : {}
    const kind = typeof payload.kind === "string" ? (payload.kind as PaletteKind) : null
    if (!kind || kind !== palette.kind) return
    const status = typeof payload.status === "string" ? payload.status : "idle"
    const message = typeof payload.message === "string" ? payload.message : ""
    palette = { ...palette, status: status as any, statusMessage: message || null }
    overlayStatus.content =
      status === "loading" ? "Loading…" : status === "error" ? `Error: ${message || "unknown"}` : message || ""
    return
  }

  if (msg.type === "ctrl.palette.items") {
    if (!palette) return
    const payload = isRecord(msg.payload) ? msg.payload : {}
    const kind = typeof payload.kind === "string" ? (payload.kind as PaletteKind) : null
    if (!kind || kind !== palette.kind) return
    const query = typeof payload.query === "string" ? payload.query : ""
    const items = Array.isArray((payload as any).items) ? ((payload as any).items as PaletteItem[]) : []
    palette = { ...palette, query, items, status: "ready", statusMessage: null }
    applyPaletteItems(items)
    overlayStatus.content = items.length > 0 ? `${items.length} item(s)` : "No matches"
    return
  }

  if (msg.type === "ctrl.permission.request") {
    const requestId =
      isRecord(msg.payload) && typeof msg.payload.request_id === "string" ? msg.payload.request_id : null
    const ctx = isRecord(msg.payload) && isRecord((msg.payload as any).context) ? ((msg.payload as any).context as Record<string, unknown>) : {}
    if (requestId) {
      pendingPermissionId = requestId
      overlayState = reduceOverlayAdapterState(overlayState, { type: "ui.permission.open", requestId })
      refreshFooter()
      process.stdout.write(`\n[permission] request_id=${requestId}\n`)
      if (!overlay.visible) {
        openPermissionOverlay(requestId, ctx)
      }
    }
    return
  }

  if (msg.type === "ctrl.error") {
    const message = isRecord(msg.payload) && typeof msg.payload.message === "string" ? msg.payload.message : "unknown"
    process.stdout.write(`\n[controller_error] ${message}\n`)
    return
  }

  if (msg.type === "ctrl.shutdown") {
    renderer.destroy()
    try {
      conn?.close()
    } catch {}
  }
})

conn.onClose(() => {
  // If controller disappears, we stop the UI rather than leaving the terminal in a weird state.
  renderer.destroy()
})

renderer.start()
refreshFooter()

process.on("SIGINT", () => shutdown())
process.on("SIGTERM", () => shutdown())
