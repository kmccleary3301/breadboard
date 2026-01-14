// Recreated after repo reset; Phase C overlays included.
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
  height: splitHeight - 1,
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
type PermissionFocus = "note" | "scope" | "rule" | "choices"
let permissionFocus: PermissionFocus = "note"

type HelpOverlayState = {
  query: string
}

let helpOverlay: HelpOverlayState | null = null

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

const overlayBanner = new TextRenderable(renderer, {
  id: "overlay_banner",
  width: "100%",
  height: 1,
  content: "",
  fg: "#a1a1aa",
  visible: false,
})

const overlayQuery = new InputRenderable(renderer, {
  id: "overlay_query",
  width: "100%",
  height: 1,
  placeholder: "Type to filter…",
  focusedBackgroundColor: "#111827",
  focusedTextColor: "#f9fafb",
})

const permissionScopeInput = new InputRenderable(renderer, {
  id: "permission_scope",
  width: "100%",
  height: 1,
  placeholder: "Scope (for allow/deny always): session | workspace | global",
  focusedBackgroundColor: "#111827",
  focusedTextColor: "#f9fafb",
  visible: false,
})

const permissionRuleInput = new InputRenderable(renderer, {
  id: "permission_rule",
  width: "100%",
  height: 1,
  placeholder: "Rule (for allow/deny always): e.g. shell:*",
  focusedBackgroundColor: "#111827",
  focusedTextColor: "#f9fafb",
  visible: false,
})

const permissionContext = new TextRenderable(renderer, {
  id: "permission_context",
  width: "100%",
  height: 5,
  content: "",
  fg: "#a1a1aa",
  visible: false,
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
overlay.add(overlayBanner)
overlay.add(overlayQuery)
overlay.add(permissionScopeInput)
overlay.add(permissionRuleInput)
overlay.add(permissionContext)
overlay.add(overlayList)
overlay.add(overlayStatus)

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

const footer = new TextRenderable(renderer, {
  id: "footer",
  position: "absolute",
  left: 0,
  top: splitHeight - 1,
  width: "100%",
  height: 1,
  content: "BreadBoard OpenTUI slab — connecting…",
  fg: "#999999",
})

renderer.root.add(input)
renderer.root.add(overlay)
renderer.root.add(footer)
input.focus()

let activeSessionId: string | null = null
let baseUrl: string | null = null
let pendingPermissionId: string | null = null
let pendingPermissionContext: Record<string, unknown> | null = null
let currentModelId: string | null = null
let defaultModelId: string | null = null
let modelProviders: string[] = []

const composerHistory: string[] = []
let composerHistoryIndex: number | null = null
let composerHistoryDraft = ""

let didHydrateDraft = false
let draftDebounce: ReturnType<typeof setTimeout> | null = null
let lastDraftSent = ""

const flushDraftUpdate = () => {
  const text = input.plainText
  if (text === lastDraftSent) return
  lastDraftSent = text
  conn?.send(nowEnvelope("ui.draft.update", { text }))
}

const scheduleDraftUpdate = () => {
  if (draftDebounce) clearTimeout(draftDebounce)
  draftDebounce = setTimeout(() => flushDraftUpdate(), 250)
}

const refreshFooter = () => {
  const perm =
    pendingPermissionId ? ` · perm=${pendingPermissionId} (a allow once, r deny)` : ""
  const model = currentModelId ? ` · model=${currentModelId}` : ""
  const overlayHint = overlay.visible
    ? " · esc close overlay"
    : " · / commands · ctrl+k commands · alt+p models · @ files · ctrl+o search"
  footer.content = `OpenTUI slab · session=${activeSessionId ?? "none"} · bridge=${baseUrl ?? "?"}${model}${perm}${overlayHint}`
}

const closeOverlay = () => {
  if (!overlay.visible) return
  overlay.visible = false
  palette = null
  permissionOverlay = null
  helpOverlay = null
  overlayTitle.content = ""
  overlayBanner.content = ""
  overlayBanner.visible = false
  overlayQuery.placeholder = "Type to filter…"
  overlayQuery.value = ""
  permissionScopeInput.value = ""
  permissionScopeInput.visible = false
  permissionRuleInput.value = ""
  permissionRuleInput.visible = false
  permissionContext.content = ""
  permissionContext.visible = false
  overlayList.options = []
  overlayStatus.content = ""
  input.focus()
  refreshFooter()
}

const openOverlay = (kind: PaletteKind, query = "") => {
  palette = { kind, query, items: [], status: "loading", statusMessage: null }
  permissionOverlay = null
  helpOverlay = null
  overlay.visible = true
  overlayTitle.content =
    kind === "commands"
      ? "Commands"
      : kind === "models"
        ? "Models"
        : kind === "files"
          ? "Files"
          : "Search"
  overlayBanner.visible = kind === "models" || kind === "files"
  overlayBanner.content =
    kind === "models"
      ? `provider:<needle> · page:<n> · ctrl+←/→ provider · ctrl+pgup/pgdn page`
      : kind === "files"
        ? `Select inserts @path · details show size/binary/large`
        : ""
  overlayQuery.placeholder = "Type to filter…"
  overlayQuery.value = query
  overlayStatus.content = "Loading…"
  overlayQuery.focus()
  permissionScopeInput.visible = false
  permissionRuleInput.visible = false
  permissionContext.visible = false
  overlayList.options = []
  conn?.send(nowEnvelope("ui.palette.open", { kind, query }))
  refreshFooter()
}

type PermissionChoice = {
  readonly title: string
  readonly detail: string
  readonly decision: string
  readonly stop?: boolean
}

const summarizeValue = (value: unknown, maxChars = 220): string => {
  try {
    if (value == null) return ""
    if (typeof value === "string") return value.length > maxChars ? `${value.slice(0, maxChars)}…` : value
    const json = JSON.stringify(value)
    if (!json) return ""
    return json.length > maxChars ? `${json.slice(0, maxChars)}…` : json
  } catch {
    return ""
  }
}

const renderPermissionContext = (context: Record<string, unknown>): string => {
  const tool =
    typeof (context as any).tool === "string"
      ? String((context as any).tool)
      : typeof (context as any).tool_name === "string"
        ? String((context as any).tool_name)
        : typeof (context as any).toolName === "string"
          ? String((context as any).toolName)
          : ""
  const summary = typeof (context as any).summary === "string" ? String((context as any).summary) : ""
  const args =
    (context as any).args && typeof (context as any).args === "object"
      ? (context as any).args
      : (context as any).tool_args && typeof (context as any).tool_args === "object"
        ? (context as any).tool_args
        : null
  const diff =
    typeof (context as any).diff_preview === "string"
      ? String((context as any).diff_preview)
      : typeof (context as any).diff === "string"
        ? String((context as any).diff)
        : typeof (context as any).patch === "string"
          ? String((context as any).patch)
          : ""

  const lines: string[] = []
  lines.push(`Tool: ${tool || "unknown"}`)
  if (summary) lines.push(`Summary: ${summary}`)
  const argsLabel = summarizeValue(args, 260)
  if (argsLabel) lines.push(`Args: ${argsLabel}`)
  if (diff) {
    const diffLines = diff.split(/\r?\n/).slice(0, 8)
    lines.push("Diff (preview):")
    for (const l of diffLines) lines.push(`  ${l}`)
    if (diff.split(/\r?\n/).length > diffLines.length) lines.push("  …")
  }
  return lines.join("\n")
}

const openPermissionOverlay = (requestId: string, context: Record<string, unknown>) => {
  permissionOverlay = { requestId, context }
  palette = null
  helpOverlay = null
  overlay.visible = true
  overlayTitle.content = "Permission request"
  overlayBanner.visible = true
  overlayBanner.content = "a allow-once · r deny-once · s deny-stop · tab cycles note/scope/rule/list"
  overlayQuery.placeholder = "Optional note (sent with decision)…"
  overlayQuery.value = ""

  permissionContext.visible = true
  permissionContext.content = renderPermissionContext(context)

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
    { title: "Allow always", detail: "allow-always (uses scope/rule inputs)", decision: "allow-always" },
    { title: "Deny once", detail: "deny-once", decision: "deny-once" },
    { title: "Deny always", detail: "deny-always (uses scope/rule inputs)", decision: "deny-always" },
    { title: "Deny + stop", detail: "deny-stop (stop run)", decision: "deny-stop", stop: true },
  ]

  permissionScopeInput.visible = true
  permissionScopeInput.value = defaultScope
  permissionRuleInput.visible = true
  permissionRuleInput.value = ruleSuggestion ?? ""

  overlayList.options = choices.map((c) => ({ name: c.title, description: c.detail, value: c }))
  overlayList.setSelectedIndex(0)
  permissionFocus = "note"
  overlayQuery.focus()
  overlayStatus.content = "Select a decision…"
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

const HELP_ITEMS: ReadonlyArray<PaletteItem> = [
  { id: "k:commands", title: "Ctrl+K", detail: "Open commands palette" },
  { id: "k:models", title: "Alt/Option+P", detail: "Open model picker" },
  { id: "q:models_filters", title: "Models query", detail: "Use `provider:<needle>` + `page:<n>`; Ctrl+←/→ cycles providers, Ctrl+PgUp/PgDn pages" },
  { id: "k:files", title: "@", detail: "Open file picker (inserts @path)" },
  { id: "k:search", title: "Ctrl+O", detail: "Open transcript search (best-effort)" },
  { id: "k:submit", title: "Enter", detail: "Submit composer" },
  { id: "k:newline", title: "Shift+Enter", detail: "Insert newline in composer" },
  { id: "k:history_prev", title: "Ctrl+P", detail: "Previous composer history item" },
  { id: "k:history_next", title: "Ctrl+N", detail: "Next composer history item" },
  { id: "k:permission_modal", title: "Ctrl+Y", detail: "Open pending permission modal (if any)" },
  { id: "k:restart_ui", title: "Ctrl+R", detail: "Restart UI (controller stays up)" },
  { id: "k:exit", title: "Ctrl+D", detail: "Exit UI + controller" },
]

const applyHelpItems = (query: string) => {
  const needle = query.trim().toLowerCase()
  const filtered = needle
    ? HELP_ITEMS.filter((item) => `${item.title} ${item.detail ?? ""}`.toLowerCase().includes(needle))
    : HELP_ITEMS
  applyPaletteItems(filtered)
  overlayStatus.content = `${filtered.length} item(s)`
}

const openHelpOverlay = (query = "") => {
  helpOverlay = { query }
  palette = null
  permissionOverlay = null
  overlay.visible = true
  overlayTitle.content = "Help / keybinds"
  overlayQuery.placeholder = "Filter keybinds…"
  overlayQuery.value = query
  applyHelpItems(query)
  overlayQuery.focus()
  refreshFooter()
}

const onOverlayQueryUpdate = () => {
  if (!overlay.visible) return
  const query = overlayQuery.value ?? ""
  if (helpOverlay) {
    helpOverlay = { query }
    applyHelpItems(query)
    return
  }
  if (!palette) return
  palette = { ...palette, query, status: "loading", statusMessage: null }
  overlayStatus.content = "Loading…"
  conn?.send(nowEnvelope("ui.palette.query", { kind: palette.kind, query }))
}

const parseModelsQuery = (query: string): { provider: string; page: number } => {
  const providerMatch = query.match(/\bprovider:([^\s]+)\b/i)
  const provider = providerMatch?.[1]?.trim().toLowerCase() ?? ""
  const pageMatch = query.match(/\bpage:(\d+)\b/i)
  const pageParsed = pageMatch?.[1] ? Number(pageMatch[1]) : 1
  const page = Number.isFinite(pageParsed) && pageParsed > 0 ? Math.floor(pageParsed) : 1
  return { provider, page }
}

const setModelsQuery = (next: { provider?: string; page?: number }) => {
  if (!palette || palette.kind !== "models") return
  const raw = overlayQuery.value ?? ""
  const withoutProvider = raw.replace(/\bprovider:[^\s]+\b/gi, "").trim()
  const withoutPage = withoutProvider.replace(/\bpage:\d+\b/gi, "").trim()
  const base = withoutPage
  const parts: string[] = []
  if (base) parts.push(base)
  const provider = (next.provider ?? parseModelsQuery(raw).provider).trim().toLowerCase()
  const page = next.page ?? parseModelsQuery(raw).page
  if (provider) parts.push(`provider:${provider}`)
  if (page && page > 1) parts.push(`page:${page}`)
  overlayQuery.value = parts.join(" ").trim()
  onOverlayQueryUpdate()
}

overlayQuery.on("input", onOverlayQueryUpdate)
overlayQuery.on("change", onOverlayQueryUpdate)

overlayList.on("itemSelected", (_index: number, opt: any) => {
  if (permissionOverlay) {
    const choice = opt?.value as PermissionChoice | undefined
    if (!choice) return
    const note = (overlayQuery.value ?? "").trim()
    const decision = choice.decision
    const always = decision === "allow-always" || decision === "deny-always"
    const scope = always ? (permissionScopeInput.value ?? "").trim() : ""
    const rule = always ? (permissionRuleInput.value ?? "").trim() : ""
    conn?.send(
      nowEnvelope("ui.permission.respond", {
        request_id: permissionOverlay.requestId,
        decision,
        note: note || null,
        scope: scope ? scope : null,
        rule: rule ? rule : null,
        stop: typeof choice.stop === "boolean" ? choice.stop : null,
      }),
    )
    pendingPermissionId = null
    pendingPermissionContext = null
    closeOverlay()
    return
  }
  if (helpOverlay) {
    closeOverlay()
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
    if (item.id === "help") {
      openHelpOverlay("")
      return
    }
    conn?.send(nowEnvelope("ui.command", { name: item.id }))
    closeOverlay()
    return
  }
  if (palette.kind === "transcript_search") {
    closeOverlay()
  }
})

overlayList.on("selectionChanged", (_index: number, opt: any) => {
  if (!overlay.visible) return
  if (!palette) return

  if (palette.kind === "files") {
    const item = opt?.value as PaletteItem | undefined
    const meta = item?.meta && typeof item.meta === "object" ? (item.meta as Record<string, unknown>) : null
    const sizeBytes = meta && typeof meta.size_bytes === "number" ? Number(meta.size_bytes) : null
    const large = meta && typeof meta.large === "boolean" ? Boolean(meta.large) : null
    const isBinary = meta && typeof meta.is_binary === "boolean" ? Boolean(meta.is_binary) : null
    const flags: string[] = []
    if (large === true) flags.push("large")
    if (isBinary === true) flags.push("binary")
    if (isBinary === false) flags.push("text")
    const sizeLabel = sizeBytes != null ? `${sizeBytes} B` : ""
    const extras = [sizeLabel, ...flags].filter(Boolean).join(" · ")
    overlayBanner.visible = true
    overlayBanner.content = extras ? `Selected: ${extras}` : "Select inserts @path · details show size/binary/large"
    return
  }

  if (palette.kind === "models") {
    const { provider, page } = parseModelsQuery(overlayQuery.value ?? "")
    const providerLabel = provider ? provider : "all"
    const current = currentModelId ? `current=${currentModelId}` : "current=?"
    const def = defaultModelId ? `default=${defaultModelId}` : ""
    overlayBanner.visible = true
    overlayBanner.content = `provider=${providerLabel} · page=${page} · ${current}${def ? ` · ${def}` : ""} · ctrl+←/→ provider · ctrl+pgup/pgdn page`
  }
})

const shutdown = () => {
  try {
    flushDraftUpdate()
    conn?.send(nowEnvelope("ui.shutdown", {}))
  } catch {
    // ignore
  }
  renderer.destroy()
  restoreTerminalModes()
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
    flushDraftUpdate()
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
        pendingPermissionContext = null
      }
      closeOverlay()
      return
    }
    if (permissionOverlay && key.name === "tab") {
      key.preventDefault()
      const order: PermissionFocus[] = ["note", "scope", "rule", "choices"]
      const currentIndex = Math.max(0, order.indexOf(permissionFocus))
      const delta = key.shift ? -1 : 1
      const nextIndex = (currentIndex + delta + order.length) % order.length
      permissionFocus = order[nextIndex] ?? "note"
      if (permissionFocus === "note") overlayQuery.focus()
      if (permissionFocus === "scope") permissionScopeInput.focus()
      if (permissionFocus === "rule") permissionRuleInput.focus()
      if (permissionFocus === "choices") overlayList.focus()
      return
    }
    if (permissionOverlay && !key.ctrl && !key.meta && !key.option) {
      const note = (overlayQuery.value ?? "").trim() || null
      if (key.name === "a") {
        key.preventDefault()
        conn?.send(
          nowEnvelope("ui.permission.respond", {
            request_id: permissionOverlay.requestId,
            decision: "allow-once",
            note,
          }),
        )
        pendingPermissionId = null
        pendingPermissionContext = null
        closeOverlay()
        return
      }
      if (key.name === "r") {
        key.preventDefault()
        conn?.send(
          nowEnvelope("ui.permission.respond", {
            request_id: permissionOverlay.requestId,
            decision: "deny-once",
            note,
          }),
        )
        pendingPermissionId = null
        pendingPermissionContext = null
        closeOverlay()
        return
      }
      if (key.name === "s") {
        key.preventDefault()
        conn?.send(
          nowEnvelope("ui.permission.respond", {
            request_id: permissionOverlay.requestId,
            decision: "deny-stop",
            stop: true,
            note,
          }),
        )
        pendingPermissionId = null
        pendingPermissionContext = null
        closeOverlay()
        return
      }
    }
    if (palette?.kind === "models" && key.ctrl && (key.name === "left" || key.name === "right")) {
      key.preventDefault()
      if (!modelProviders.length) return
      const { provider } = parseModelsQuery(overlayQuery.value ?? "")
      const idx = provider ? modelProviders.indexOf(provider) : -1
      const next =
        key.name === "right"
          ? idx === -1
            ? 0
            : (idx + 1) % modelProviders.length
          : idx === -1
            ? modelProviders.length - 1
            : (idx - 1 + modelProviders.length) % modelProviders.length
      const nextProvider = modelProviders[next] ?? ""
      setModelsQuery({ provider: nextProvider, page: 1 })
      return
    }
    if (palette?.kind === "models" && key.ctrl && (key.name === "pageup" || key.name === "pagedown")) {
      key.preventDefault()
      const { page } = parseModelsQuery(overlayQuery.value ?? "")
      const nextPage = key.name === "pagedown" ? page + 1 : Math.max(1, page - 1)
      setModelsQuery({ page: nextPage })
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
  if (key.ctrl && key.name === "y" && pendingPermissionId && pendingPermissionContext) {
    key.preventDefault()
    openPermissionOverlay(pendingPermissionId, pendingPermissionContext)
    return
  }
  if (key.sequence === "/" && input.plainText.trim().length === 0) {
    key.preventDefault()
    openOverlay("commands", "")
    return
  }
  if (key.ctrl && key.name === "o") {
    key.preventDefault()
    openOverlay("transcript_search", "")
    return
  }
  if ((key.option || key.meta) && !key.ctrl && key.name === "p") {
    key.preventDefault()
    openOverlay("models", "")
    return
  }
  if (key.sequence === "@") {
    key.preventDefault()
    openOverlay("files", "")
    return
  }

  if (key.ctrl && key.name === "p") {
    key.preventDefault()
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

  if (pendingPermissionId) {
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
      return
    }
  }

  if (!overlay.visible && !key.ctrl && !key.meta && !key.option) scheduleDraftUpdate()
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
  lastDraftSent = ""
  conn?.send(nowEnvelope("ui.draft.update", { text: "" }))
}

conn.onMessage((msg) => {
  if (!isCtrlMsg(msg)) return

  if (msg.type === "ctrl.state") {
    const payload = isRecord(msg.payload) ? msg.payload : {}
    activeSessionId = typeof payload.active_session_id === "string" ? payload.active_session_id : null
    baseUrl = typeof payload.base_url === "string" ? payload.base_url : null
    currentModelId = typeof payload.current_model === "string" ? payload.current_model : null
    defaultModelId = typeof (payload as any).model_default === "string" ? String((payload as any).model_default) : null
    modelProviders = Array.isArray((payload as any).model_providers)
      ? ((payload as any).model_providers as unknown[]).map((p) => String(p).trim().toLowerCase()).filter(Boolean)
      : []
    const draftText = typeof (payload as any).draft_text === "string" ? String((payload as any).draft_text) : ""
    if (!didHydrateDraft) {
      if (draftText && input.plainText.trim().length === 0) {
        input.clear()
        input.insertText(draftText)
        input.focus()
        lastDraftSent = draftText
      }
      didHydrateDraft = true
    }
    const pending = (payload as any).pending_permissions
    if (Array.isArray(pending) && pending.length > 0) {
      const last = pending[pending.length - 1]
      const requestId = isRecord(last) && typeof (last as any).request_id === "string" ? String((last as any).request_id) : ""
      pendingPermissionId = requestId.trim() ? requestId.trim() : null
      pendingPermissionContext = isRecord(last) ? (last as Record<string, unknown>) : null
    } else if (pendingPermissionId) {
      pendingPermissionId = null
      pendingPermissionContext = null
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
    const event = isRecord(msg.payload) && isRecord(msg.payload.event) ? (msg.payload.event as Record<string, unknown>) : null
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
      pendingPermissionContext = ctx
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
  renderer.destroy()
})

renderer.start()
refreshFooter()

process.on("SIGINT", () => shutdown())
process.on("SIGTERM", () => shutdown())
process.on("exit", () => restoreTerminalModes())
