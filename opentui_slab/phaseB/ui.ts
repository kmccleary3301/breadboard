import {
  createCliRenderer,
  BoxRenderable,
  InputRenderable,
  SelectRenderable,
  StyledText,
  TextRenderable,
  TextareaRenderable,
  fg,
  stringToStyledText,
  t,
  type KeyEvent,
  type TextChunk,
} from "@opentui/core"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { connectIpcClient, type IpcConnection } from "./ipc.ts"
import {
  nowEnvelope,
  type ControllerToUIMessage,
  type PaletteItem,
  type PaletteKind,
  IPC_PROTOCOL_VERSION,
} from "./protocol.ts"
import { BRAND_COLORS, ICONS, NEUTRAL_COLORS, SEMANTIC_COLORS } from "./designSystem.ts"

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

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

type RGB = { r: number; g: number; b: number }

const hexToRgb = (hex: string): RGB => {
  const cleaned = hex.replace("#", "").trim()
  const normalized = cleaned.length === 3 ? cleaned.replace(/(.)/g, "$1$1") : cleaned
  const value = Number.parseInt(normalized.padEnd(6, "0").slice(0, 6), 16)
  return {
    r: (value >> 16) & 0xff,
    g: (value >> 8) & 0xff,
    b: value & 0xff,
  }
}

const rgbToHex = (rgb: RGB): string => {
  const toHex = (value: number) => value.toString(16).padStart(2, "0")
  return `#${toHex(Math.round(rgb.r))}${toHex(Math.round(rgb.g))}${toHex(Math.round(rgb.b))}`
}

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t

const lerpRgb = (start: RGB, end: RGB, t: number): RGB => ({
  r: lerp(start.r, end.r, t),
  g: lerp(start.g, end.g, t),
  b: lerp(start.b, end.b, t),
})

const gradientColorAt = (t: number, stops: RGB[]): string => {
  if (stops.length === 0) return BRAND_COLORS.railBlue
  if (stops.length === 1) return rgbToHex(stops[0])
  const clamped = Math.min(1, Math.max(0, t))
  const scaled = clamped * (stops.length - 1)
  const idx = Math.min(stops.length - 2, Math.floor(scaled))
  const localT = scaled - idx
  return rgbToHex(lerpRgb(stops[idx], stops[idx + 1], localT))
}

const buildGradientText = (text: string, stops: RGB[]): StyledText => {
  if (!text) return stringToStyledText("")
  const length = Math.max(1, text.length - 1)
  const chunks: TextChunk[] = []
  for (let i = 0; i < text.length; i += 1) {
    const color = gradientColorAt(i / length, stops)
    chunks.push(fg(color)(text[i]))
  }
  return new StyledText(chunks)
}

const resolveAsciiHeader = (): string[] => {
  try {
    const currentDir = path.dirname(fileURLToPath(import.meta.url))
    const candidates = [
      path.join(currentDir, "ascii_header.txt"),
      path.resolve(currentDir, "../../tui_skeleton/src/ascii_header.txt"),
      path.resolve(currentDir, "../../tui_skeleton/dist/ascii_header.txt"),
      path.resolve(process.cwd(), "tui_skeleton/src/ascii_header.txt"),
      path.resolve(process.cwd(), "tui_skeleton/dist/ascii_header.txt"),
      path.resolve(process.cwd(), "src/ascii_header.txt"),
    ]
    for (const candidate of candidates) {
      if (!fs.existsSync(candidate)) continue
      const raw = fs.readFileSync(candidate, "utf8").replace(/\r\n/g, "\n").replace(/\n+$/, "")
      if (raw.trim()) return raw.split("\n")
    }
  } catch {
    // ignore — fall back to default header
  }
  return ["Breadboard"]
}

const capturePathRaw = (process.env.BREADBOARD_UI_CAPTURE_PATH ?? "").trim()
const captureOnEvent = (process.env.BREADBOARD_UI_CAPTURE_ON_EVENT ?? "run.end").trim().toLowerCase()
const captureDelayMsRaw = Number(process.env.BREADBOARD_UI_CAPTURE_DELAY_MS ?? "200")
const captureDelayMs = Number.isFinite(captureDelayMsRaw) ? Math.max(0, captureDelayMsRaw) : 200
const captureDecoder = new TextDecoder()
let captureQueued = false
let captureComplete = false

const eventLogPathRaw = (process.env.BREADBOARD_UI_EVENT_LOG ?? "").trim()
const eventLogPath = eventLogPathRaw
  ? path.isAbsolute(eventLogPathRaw)
    ? eventLogPathRaw
    : path.resolve(process.cwd(), eventLogPathRaw)
  : ""

const appendEventLog = (line: string) => {
  if (!eventLogPath) return
  void fs.promises.appendFile(eventLogPath, `${line}\n`, "utf8").catch(() => undefined)
}

const resolveCapturePath = (label: string): string | null => {
  if (!capturePathRaw) return null
  const base = path.isAbsolute(capturePathRaw) ? capturePathRaw : path.resolve(process.cwd(), capturePathRaw)
  if (base.endsWith(".txt")) return base
  const safeLabel = label.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 48) || "capture"
  const stamp = new Date().toISOString().replace(/[:.]/g, "-")
  return path.join(base, `opentui_${safeLabel}_${stamp}.txt`)
}

const captureFrame = async (label: string, renderer: { currentRenderBuffer?: any }) => {
  if (!capturePathRaw || captureComplete) return
  captureComplete = true
  const target = resolveCapturePath(label)
  if (!target) return
  const buffer = renderer.currentRenderBuffer
  if (!buffer || typeof buffer.getRealCharBytes !== "function") return
  const bytes = buffer.getRealCharBytes(true)
  const text = captureDecoder.decode(bytes)
  await fs.promises.mkdir(path.dirname(target), { recursive: true })
  const header = `# ${label}\n# captured_at ${new Date().toISOString()}\n`
  await fs.promises.writeFile(target, `${header}${text}\n`, "utf8")
}

let conn: IpcConnection | null = null
conn = await connectIpcClient({ host, port, timeoutMs: 25_000 })

const renderer = await createCliRenderer({
  useAlternateScreen: false,
  useConsole: false,
  targetFps: 20,
  maxFps: 30,
})

const inferredTerminalWidth = renderer.width || 80
const inferredTerminalHeight = ((renderer as any).renderOffset ?? 0) + renderer.height || 24
const reportedWidth = process.stdout.columns && process.stdout.columns > 0 ? process.stdout.columns : 0
const reportedHeight = process.stdout.rows && process.stdout.rows > 0 ? process.stdout.rows : 0
const envWidthRaw = Number(process.env.COLUMNS ?? "")
const envHeightRaw = Number(process.env.LINES ?? "")
const envWidth = Number.isFinite(envWidthRaw) && envWidthRaw > 0 ? Math.floor(envWidthRaw) : 0
const envHeight = Number.isFinite(envHeightRaw) && envHeightRaw > 0 ? Math.floor(envHeightRaw) : 0
const forcedWidthRaw = Number(process.env.BREADBOARD_UI_FORCE_COLS ?? "")
const forcedHeightRaw = Number(process.env.BREADBOARD_UI_FORCE_ROWS ?? "")
const forcedWidth = Number.isFinite(forcedWidthRaw) && forcedWidthRaw > 0 ? Math.floor(forcedWidthRaw) : 0
const forcedHeight = Number.isFinite(forcedHeightRaw) && forcedHeightRaw > 0 ? Math.floor(forcedHeightRaw) : 0
const terminalWidth = forcedWidth || reportedWidth || envWidth || inferredTerminalWidth
const terminalHeight = forcedHeight || reportedHeight || envHeight || inferredTerminalHeight
const currentTerminalWidth = (renderer as any)._terminalWidth as number | undefined
const currentTerminalHeight = (renderer as any)._terminalHeight as number | undefined
if (!currentTerminalWidth || currentTerminalWidth <= 0) {
  ;(renderer as any)._terminalWidth = terminalWidth
}
if (!currentTerminalHeight || currentTerminalHeight <= 0) {
  ;(renderer as any)._terminalHeight = terminalHeight
}

const ASCII_HEADER_LINES = resolveAsciiHeader()
const HEADER_LOGO_WIDTH = ASCII_HEADER_LINES.reduce((max, line) => Math.max(max, line.length), 0)
const HEADER_META_GAP = 1
const LEFT_GUTTER = " "
const HORIZONTAL_RULE = "─".repeat(Math.max(1, terminalWidth))
const HEADER_HEIGHT = ASCII_HEADER_LINES.length + 1
const FOOTER_HEIGHT = 1
const INPUT_VALUE_HEIGHT = 1
const INPUT_HEIGHT = INPUT_VALUE_HEIGHT + 1
const CONTENT_HEIGHT = Math.max(1, terminalHeight - HEADER_HEIGHT - INPUT_HEIGHT - FOOTER_HEIGHT)
const CONTENT_TOP = HEADER_HEIGHT
const INPUT_TOP = HEADER_HEIGHT + CONTENT_HEIGHT
const INPUT_TEXT_TOP = INPUT_TOP + 1
const PROMPT_PREFIX = `${LEFT_GUTTER}> `
const USER_PREFIX = `${ICONS.userChevron} `
const PROMPT_PREFIX_WIDTH = PROMPT_PREFIX.length
const PROMPT_STATUS_TEXT = ""
const SHOW_PROMPT_STATUS = false
const PROMPT_STATUS_WIDTH = SHOW_PROMPT_STATUS ? PROMPT_STATUS_TEXT.length : 1
const INPUT_PLACEHOLDER =
  (process.env.BREADBOARD_UI_PLACEHOLDER ?? "").trim() || 'Try "edit <file> to..."'
const INPUT_PLACEHOLDER_STYLE =
  INPUT_PLACEHOLDER.length > 0 ? new StyledText([fg(NEUTRAL_COLORS.dimGray)(INPUT_PLACEHOLDER)]) : null
const INPUT_INNER_LEFT = PROMPT_PREFIX_WIDTH
const INPUT_INNER_WIDTH = Math.max(
  10,
  terminalWidth - PROMPT_PREFIX_WIDTH - (SHOW_PROMPT_STATUS ? PROMPT_STATUS_WIDTH + 1 : 0),
)
const OVERLAY_WIDTH = Math.min(96, Math.max(40, terminalWidth - 4))
const OVERLAY_HEIGHT = Math.min(Math.max(8, Math.floor(CONTENT_HEIGHT * 0.6)), CONTENT_HEIGHT)
const OVERLAY_TOP = CONTENT_TOP + Math.max(0, Math.floor((CONTENT_HEIGHT - OVERLAY_HEIGHT) / 2))
const OVERLAY_LEFT = Math.max(0, Math.floor((terminalWidth - OVERLAY_WIDTH) / 2))

const header = new BoxRenderable(renderer, {
  id: "header",
  position: "absolute",
  left: 0,
  top: 0,
  width: "100%",
  height: HEADER_HEIGHT,
  paddingLeft: 0,
  paddingRight: 0,
  flexDirection: "column",
  backgroundColor: NEUTRAL_COLORS.nearBlack,
})

const headerText = new TextRenderable(renderer, {
  id: "header_text",
  width: "100%",
  height: HEADER_HEIGHT,
  content: "",
  fg: NEUTRAL_COLORS.nearWhite,
  bg: NEUTRAL_COLORS.nearBlack,
})

header.add(headerText)

const conversationPanel = new BoxRenderable(renderer, {
  id: "conversation",
  position: "absolute",
  left: 0,
  top: CONTENT_TOP,
  width: "100%",
  height: CONTENT_HEIGHT,
  paddingLeft: 0,
  paddingRight: 0,
  flexDirection: "column",
  backgroundColor: NEUTRAL_COLORS.nearBlack,
})

const conversationText = new TextRenderable(renderer, {
  id: "conversation_text",
  width: "100%",
  height: "100%",
  content: "",
  fg: NEUTRAL_COLORS.offWhite,
  bg: NEUTRAL_COLORS.nearBlack,
})

conversationPanel.add(conversationText)

const inputPanel = new BoxRenderable(renderer, {
  id: "input_panel",
  position: "absolute",
  left: 0,
  top: INPUT_TOP,
  width: "100%",
  height: INPUT_HEIGHT,
  border: false,
  paddingLeft: 0,
  paddingRight: 0,
  backgroundColor: NEUTRAL_COLORS.nearBlack,
})

const promptSeparatorTop = new TextRenderable(renderer, {
  id: "prompt_sep_top",
  position: "absolute",
  left: 0,
  top: INPUT_TOP,
  width: "100%",
  height: 1,
  content: HORIZONTAL_RULE,
  fg: NEUTRAL_COLORS.dimGray,
})


const promptPrefix = new TextRenderable(renderer, {
  id: "prompt_prefix",
  position: "absolute",
  left: 0,
  top: INPUT_TEXT_TOP,
  width: PROMPT_PREFIX_WIDTH,
  height: 1,
  content: PROMPT_PREFIX,
  fg: NEUTRAL_COLORS.nearWhite,
  bg: NEUTRAL_COLORS.nearBlack,
})

const promptStatus = new TextRenderable(renderer, {
  id: "prompt_status",
  position: "absolute",
  left: Math.max(0, terminalWidth - PROMPT_STATUS_WIDTH),
  top: INPUT_TEXT_TOP,
  width: PROMPT_STATUS_WIDTH,
  height: 1,
  content: SHOW_PROMPT_STATUS ? PROMPT_STATUS_TEXT : "",
  fg: NEUTRAL_COLORS.dimGray,
  bg: NEUTRAL_COLORS.nearBlack,
})

const input = new TextareaRenderable(renderer, {
  id: "composer",
  position: "absolute",
  left: INPUT_INNER_LEFT,
  top: INPUT_TEXT_TOP,
  width: INPUT_INNER_WIDTH,
  height: INPUT_VALUE_HEIGHT,
  backgroundColor: NEUTRAL_COLORS.nearBlack,
  textColor: NEUTRAL_COLORS.nearWhite,
  focusedBackgroundColor: NEUTRAL_COLORS.nearBlack,
  focusedTextColor: NEUTRAL_COLORS.nearWhite,
  placeholder: INPUT_PLACEHOLDER_STYLE ?? INPUT_PLACEHOLDER,
  selectionBg: NEUTRAL_COLORS.darkGray,
  selectionFg: NEUTRAL_COLORS.nearWhite,
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

const overlay = new BoxRenderable(renderer, {
  id: "overlay",
  position: "absolute",
  left: OVERLAY_LEFT,
  top: OVERLAY_TOP,
  width: OVERLAY_WIDTH,
  height: OVERLAY_HEIGHT,
  zIndex: 10,
  border: true,
  borderColor: NEUTRAL_COLORS.darkGray,
  focusedBorderColor: BRAND_COLORS.duneOrange,
  backgroundColor: NEUTRAL_COLORS.nearBlack,
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
  fg: NEUTRAL_COLORS.offWhite,
  bg: NEUTRAL_COLORS.nearBlack,
})

const overlayQuery = new InputRenderable(renderer, {
  id: "overlay_query",
  width: "100%",
  height: 1,
  placeholder: "Type to filter…",
  backgroundColor: NEUTRAL_COLORS.nearBlack,
  textColor: NEUTRAL_COLORS.nearWhite,
  focusedBackgroundColor: NEUTRAL_COLORS.nearBlack,
  focusedTextColor: NEUTRAL_COLORS.nearWhite,
})

const overlayList = new SelectRenderable(renderer, {
  id: "overlay_list",
  width: "100%",
  height: "auto",
  flexGrow: 1,
  options: [],
  backgroundColor: NEUTRAL_COLORS.nearBlack,
  textColor: NEUTRAL_COLORS.nearWhite,
  descriptionColor: NEUTRAL_COLORS.midGray,
  showScrollIndicator: true,
  wrapSelection: true,
  showDescription: true,
  fastScrollStep: 5,
  selectedBackgroundColor: BRAND_COLORS.duneOrange,
  selectedTextColor: NEUTRAL_COLORS.nearBlack,
  selectedDescriptionColor: NEUTRAL_COLORS.nearBlack,
})

const overlayStatus = new TextRenderable(renderer, {
  id: "overlay_status",
  width: "100%",
  height: 1,
  content: "",
  fg: NEUTRAL_COLORS.midGray,
  bg: NEUTRAL_COLORS.nearBlack,
})

overlay.add(overlayTitle)
overlay.add(overlayQuery)
overlay.add(overlayList)
overlay.add(overlayStatus)

const footer = new TextRenderable(renderer, {
  id: "footer",
  position: "absolute",
  left: 0,
  top: terminalHeight - FOOTER_HEIGHT,
  width: "100%",
  height: 1,
  content: "  ? for shortcuts",
  fg: NEUTRAL_COLORS.dimGray,
  bg: NEUTRAL_COLORS.nearBlack,
})

renderer.root.add(header)
renderer.root.add(conversationPanel)
renderer.root.add(inputPanel)
renderer.root.add(promptSeparatorTop)
renderer.root.add(promptPrefix)
renderer.root.add(promptStatus)
renderer.root.add(input)
renderer.root.add(overlay)
renderer.root.add(footer)
input.focus()

let activeSessionId: string | null = null
let baseUrl: string | null = null
let pendingPermissionId: string | null = null
let currentModelId: string | null = null
let controllerStatus: string | null = null
const composerHistory: string[] = []
let composerHistoryIndex: number | null = null
let composerHistoryDraft = ""
type ConversationLine = StyledText
const conversationLines: ConversationLine[] = []
type LineBlock = { id: string; startIndex: number; lineCount: number }
type StreamBlock = { block: LineBlock; buffer: string; finalized: boolean }
type ToolBlock = {
  id: string
  name: string
  block: LineBlock
  args: string
  stdout: string
  stderr: string
  result: string | null
  ok: boolean | null
  execCommand: string | null
}
type CTreeTreeNode = {
  readonly id: string
  readonly parent_id?: string | null
  readonly kind: string
  readonly label?: string | null
  readonly turn?: number | null
  readonly meta?: Record<string, unknown> | null
}
type CTreeTreeResponse = {
  readonly root_id?: string
  readonly nodes?: CTreeTreeNode[]
  readonly stage?: string
  readonly source?: string
  readonly selection?: Record<string, unknown> | null
  readonly hashes?: Record<string, unknown> | null
}
type CTreeRow = {
  readonly id: string
  readonly node: CTreeTreeNode
  readonly depth: number
  readonly hasChildren: boolean
}
const lineBlocks: LineBlock[] = []
let blockCounter = 0
let toolBlockCounter = 0
let assistantStream: StreamBlock | null = null
const toolBlocks = new Map<string, ToolBlock>()
let lastToolCallId: string | null = null
const toolLogLines: string[] = []
let ctreeOverlayOpen = false
let ctreeStage = "FROZEN"
let ctreeIncludePreviews = false
let ctreeSource = "auto"
let ctreeTree: CTreeTreeResponse | null = null
let ctreeRows: CTreeRow[] = []
let ctreeCollapsed = new Set<string>()
const debugEchoEnabled = parseBoolEnv(process.env.BREADBOARD_UI_DEBUG, false)
let rawStreamEnabled = parseBoolEnv(process.env.BREADBOARD_UI_RAW_STREAM, false)
let toolInlineEnabled = parseBoolEnv(process.env.BREADBOARD_UI_TOOL_INLINE, true)
let reasoningVisible = parseBoolEnv(process.env.BREADBOARD_UI_REASONING, false)
let reasoningBuffer = ""
let reasoningBlock: LineBlock | null = null
let toolLogOverlayOpen = false
let rendererActive = true
let lastModelSubmitAt = 0
let modelQueryEchoed = false
let debugQueryEchoed = false
let harnessPermissionEchoed = false

const PROGRESS_FRAMES = [
  "■·······",
  "■■······",
  "■■■·····",
  "■■■■····",
  "■■■■■···",
  "■■■■■■··",
  "■■■■■■■·",
  "■■■■■■■■",
]
const PROGRESS_EMPTY = "········"
let progressIndex = 0
let progressTimer: ReturnType<typeof setInterval> | null = null

const STATUS_SPARK_FRAMES = ["·", "*", "✶", "✢", "✽"]
const STATUS_SPARK_COLORS = [
  BRAND_COLORS.jamRed,
  BRAND_COLORS.duneOrange,
  BRAND_COLORS.magenta,
  BRAND_COLORS.railBlue,
  BRAND_COLORS.jumperGreen,
]
const STATUS_VERBS = [
  "Cascading",
  "Boondoggling",
  "Elucidating",
  "Perusing",
  "Spelunking",
  "Dilly-dallying",
  "Sautéing",
]
const STATUS_TICK_MS = 150
const LOGO_GRADIENT_STOPS = [BRAND_COLORS.jamRed, BRAND_COLORS.magenta, BRAND_COLORS.railBlue].map(hexToRgb)

let statusTickerBlock: LineBlock | null = null
let statusTickerTimer: ReturnType<typeof setInterval> | null = null
let statusTickerFrame = 0
let statusTickerRunCount = 0
let statusTickerVerbIndex = 0
let statusTickerStartMs = 0
let statusTickerUsage = { promptTokens: 0, completionTokens: 0, totalTokens: 0 }
let statusTickerActive = false

const EMPTY_LINE = stringToStyledText("")
const NEWLINE_CHUNKS = stringToStyledText("\n").chunks
const GUTTER_CHUNKS = stringToStyledText(LEFT_GUTTER).chunks

conversationLines.push(EMPTY_LINE)

const styledTextToPlain = (line: ConversationLine): string =>
  line.chunks.map((chunk) => chunk.text).join("")

const isEmptyLine = (line: ConversationLine | undefined): boolean => {
  if (!line) return true
  return styledTextToPlain(line).trim().length === 0
}

const formatDuration = (elapsedMs: number): string => {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes <= 0) return `${seconds}s`
  return `${minutes}m ${seconds}s`
}

const formatTokenCount = (count: number): string => {
  if (!Number.isFinite(count) || count <= 0) return "0"
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}m`
  if (count >= 10_000) return `${Math.round(count / 1000)}k`
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`
  return `${Math.round(count)}`
}

const formatTokenActivity = (): string => {
  const total = statusTickerUsage.totalTokens || statusTickerUsage.completionTokens || statusTickerUsage.promptTokens
  if (!total) return ""
  return `↓ ${formatTokenCount(total)} tokens`
}

const buildActiveStatusLine = (): StyledText => {
  const frameIndex = statusTickerFrame % STATUS_SPARK_FRAMES.length
  const frame = STATUS_SPARK_FRAMES[frameIndex]
  const sparkColor = STATUS_SPARK_COLORS[frameIndex % STATUS_SPARK_COLORS.length]
  const verb = STATUS_VERBS[statusTickerVerbIndex % STATUS_VERBS.length]
  const elapsed = statusTickerStartMs ? formatDuration(Date.now() - statusTickerStartMs) : "0s"
  const tokenActivity = formatTokenActivity()
  const details = [elapsed, tokenActivity].filter(Boolean).join(" · ")
  const suffix = details ? ` (${details})` : ""
  return new StyledText([
    fg(sparkColor)(frame),
    fg(SEMANTIC_COLORS.warning)(" "),
    fg(SEMANTIC_COLORS.warning)(verb),
    fg(SEMANTIC_COLORS.warning)("…"),
    fg(NEUTRAL_COLORS.dimGray)(suffix),
  ])
}

const buildCompleteStatusLine = (elapsedMs: number): StyledText => {
  const tokenActivity = formatTokenActivity()
  const details = [formatDuration(elapsedMs), tokenActivity].filter(Boolean).join(" · ")
  const label = details ? `Completed in ${details}` : "Completed"
  return new StyledText([fg(NEUTRAL_COLORS.dimGray)(`${ICONS.completeAsterisk} ${label}`)])
}

const getConversationInsertIndex = (): number =>
  statusTickerBlock && statusTickerActive ? statusTickerBlock.startIndex : conversationLines.length

const insertConversationLines = (index: number, lines: ConversationLine[]) => {
  if (lines.length === 0) return
  conversationLines.splice(index, 0, ...lines)
  for (const block of lineBlocks) {
    if (block.startIndex >= index) block.startIndex += lines.length
  }
}

const insertLineBlockAt = (index: number, lines: ConversationLine[]): LineBlock => {
  const block: LineBlock = { id: `b${blockCounter++}`, startIndex: index, lineCount: lines.length }
  insertConversationLines(index, lines)
  const insertAt = lineBlocks.findIndex((existing) => existing.startIndex >= block.startIndex)
  if (insertAt === -1) {
    lineBlocks.push(block)
  } else {
    lineBlocks.splice(insertAt, 0, block)
  }
  trimConversationLines()
  renderConversation()
  return block
}

const joinStyledLines = (lines: StyledText[]): StyledText => {
  const chunks: TextChunk[] = []
  for (let idx = 0; idx < lines.length; idx += 1) {
    chunks.push(...lines[idx].chunks)
    if (idx < lines.length - 1) {
      chunks.push(...NEWLINE_CHUNKS)
    }
  }
  return new StyledText(chunks)
}

const buildConversationText = (lines: ConversationLine[]): StyledText => {
  const chunks: TextChunk[] = []
  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = lines[idx]
    if (styledTextToPlain(line).length > 0 && LEFT_GUTTER) {
      chunks.push(...GUTTER_CHUNKS)
    }
    chunks.push(...line.chunks)
    if (idx < lines.length - 1) {
      chunks.push(...NEWLINE_CHUNKS)
    }
  }
  return new StyledText(chunks)
}

const renderConversation = () => {
  const maxLines = Math.max(1, CONTENT_HEIGHT)
  const slice = conversationLines.slice(-maxLines)
  if (slice.length === 0) {
    conversationText.content = ""
    return
  }
  conversationText.content = buildConversationText(slice)
}

const appendConversationLines = (lines: ConversationLine[], addSpacer = true) => {
  const insertIndex = getConversationInsertIndex()
  const payload: ConversationLine[] = [...lines]
  if (payload.length > 0) {
    const prevLine = insertIndex > 0 ? conversationLines[insertIndex - 1] : undefined
    if (!isEmptyLine(prevLine)) {
      payload.unshift(EMPTY_LINE)
    }
    if (addSpacer && !isEmptyLine(payload[payload.length - 1])) {
      payload.push(EMPTY_LINE)
    }
  }
  if (payload.length === 0) return
  if (insertIndex === conversationLines.length) {
    conversationLines.push(...payload)
  } else {
    insertConversationLines(insertIndex, payload)
  }
  trimConversationLines()
  renderConversation()
}

const trimConversationLines = () => {
  if (conversationLines.length > 5000) {
    const removeCount = conversationLines.length - 5000
    conversationLines.splice(0, removeCount)
    for (let index = lineBlocks.length - 1; index >= 0; index -= 1) {
      const block = lineBlocks[index]
      const end = block.startIndex + block.lineCount
      if (end <= removeCount || block.startIndex < removeCount) {
        lineBlocks.splice(index, 1)
        continue
      }
      block.startIndex -= removeCount
    }
  }
}

const appendLineBlock = (lines: ConversationLine[], addSpacer = false): LineBlock => {
  const insertIndex = getConversationInsertIndex()
  const payload: ConversationLine[] = [...lines]
  if (payload.length > 0) {
    const prevLine = insertIndex > 0 ? conversationLines[insertIndex - 1] : undefined
    if (!isEmptyLine(prevLine)) {
      payload.unshift(EMPTY_LINE)
    }
    if (addSpacer && !isEmptyLine(payload[payload.length - 1])) {
      payload.push(EMPTY_LINE)
    }
  }
  if (insertIndex === conversationLines.length) {
    const block: LineBlock = { id: `b${blockCounter++}`, startIndex: conversationLines.length, lineCount: payload.length }
    conversationLines.push(...payload)
    lineBlocks.push(block)
    trimConversationLines()
    renderConversation()
    return block
  }
  return insertLineBlockAt(insertIndex, payload)
}

const updateLineBlock = (block: LineBlock, lines: ConversationLine[]) => {
  const idx = lineBlocks.indexOf(block)
  if (idx === -1) return
  const delta = lines.length - block.lineCount
  conversationLines.splice(block.startIndex, block.lineCount, ...lines)
  block.lineCount = lines.length
  if (delta !== 0) {
    for (let i = idx + 1; i < lineBlocks.length; i += 1) {
      lineBlocks[i].startIndex += delta
    }
  }
  trimConversationLines()
  renderConversation()
}

const removeLineBlock = (block: LineBlock) => {
  const idx = lineBlocks.indexOf(block)
  if (idx === -1) return
  conversationLines.splice(block.startIndex, block.lineCount)
  lineBlocks.splice(idx, 1)
  for (let i = idx; i < lineBlocks.length; i += 1) {
    lineBlocks[i].startIndex -= block.lineCount
  }
  trimConversationLines()
  renderConversation()
}

const renderStreamBlock = (stream: StreamBlock): ConversationLine[] => {
  const lines = formatAssistantLines(stream.buffer || "")
  if (stream.finalized) lines.push(EMPTY_LINE)
  return lines
}

const startStream = (): StreamBlock => {
  const stream: StreamBlock = { block: { id: "", startIndex: 0, lineCount: 0 }, buffer: "", finalized: false }
  stream.block = appendLineBlock(renderStreamBlock(stream), false)
  return stream
}

const updateStream = (stream: StreamBlock, delta: string) => {
  stream.buffer += delta
  updateLineBlock(stream.block, renderStreamBlock(stream))
}

const finalizeStream = (stream: StreamBlock | null) => {
  if (!stream || stream.finalized) return
  stream.finalized = true
  if (isNullishMessage(stream.buffer || "")) {
    removeLineBlock(stream.block)
    return
  }
  updateLineBlock(stream.block, renderStreamBlock(stream))
}

const finalizeActiveStreams = () => {
  if (assistantStream) {
    finalizeStream(assistantStream)
    assistantStream = null
  }
}

const refreshToolLogOverlay = () => {
  if (!toolLogOverlayOpen || !overlay.visible) return
  const items = toolLogLines.slice(-200).reverse()
  overlayList.options = items.map((line, idx) => ({
    name: line,
    description: "",
    value: { id: `tool-log-${idx}` },
  }))
  overlayStatus.content = `${toolLogLines.length} event(s)`
  overlayList.setSelectedIndex(0)
}

const pushToolLogLine = (line: string) => {
  if (!line) return
  toolLogLines.push(line)
  if (toolLogLines.length > 500) {
    toolLogLines.splice(0, toolLogLines.length - 500)
  }
  refreshToolLogOverlay()
}

const formatRawEventLine = (event: Record<string, unknown>): string => {
  let raw = ""
  try {
    raw = JSON.stringify({ type: event.type, payload: event.payload })
  } catch {
    raw = String(event.type ?? "event")
  }
  if (raw.length > 240) {
    raw = `${raw.slice(0, 240)}…`
  }
  return `[raw] ${raw}`
}

const normalizeLines = (text: string): string[] =>
  text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")

const normalizeUserMessage = (text: string): string => text.trimEnd()

const shouldSkipUserEcho = (text: string): boolean => {
  const normalized = normalizeUserMessage(text)
  const recent = conversationLines.slice(-200)
  return recent.some((line) => styledTextToPlain(line).includes(normalized))
}

const formatUserLines = (text: string): ConversationLine[] => {
  const lines = normalizeLines(text)
  return lines.map((line) =>
    t`${fg(NEUTRAL_COLORS.nearWhite)(USER_PREFIX)}${fg(NEUTRAL_COLORS.nearWhite)(line.trimEnd())}`,
  )
}

const formatAssistantLines = (text: string): ConversationLine[] => {
  const lines = normalizeLines(text)
  return lines.map((line, idx) => {
    const prefix = idx === 0 ? `${ICONS.bullet} ` : "  "
    return t`${fg(NEUTRAL_COLORS.offWhite)(prefix)}${fg(NEUTRAL_COLORS.nearWhite)(line.trimEnd())}`
  })
}

const formatIndentedLines = (text: string, indent: string, color = SEMANTIC_COLORS.muted): ConversationLine[] => {
  const lines = normalizeLines(text)
  return lines.map((line) => t`${fg(color)(indent)}${fg(color)(line.trimEnd())}`)
}

const formatSystemLine = (text: string, color = SEMANTIC_COLORS.muted): ConversationLine => {
  const trimmed = text.trimEnd()
  return t`${fg(color)(trimmed)}`
}

const formatToolHeaderLine = (text: string): ConversationLine =>
  t`${fg(SEMANTIC_COLORS.tool)(`${ICONS.bullet} `)}${fg(NEUTRAL_COLORS.nearWhite)(text.trimEnd())}`

const TOOL_OUTPUT_MAX_LINES = 12
const TOOL_OUTPUT_HEAD_LINES = 8
const TOOL_OUTPUT_MAX_CHARS = 140

const splitOutputLines = (value: string): string[] =>
  value
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .filter((line) => line.length > 0)

const clampOutputLine = (line: string): string => {
  if (line.length <= TOOL_OUTPUT_MAX_CHARS) return line
  if (TOOL_OUTPUT_MAX_CHARS <= 1) return line.slice(0, TOOL_OUTPUT_MAX_CHARS)
  return `${line.slice(0, TOOL_OUTPUT_MAX_CHARS - 1)}…`
}

const compactWhitespace = (value: string): string => value.replace(/\s+/g, " ").trim()

const isGenericToolName = (name: string): boolean => {
  const cleaned = name.trim().toLowerCase()
  return cleaned === "" || cleaned === "tool" || cleaned === "tool_call" || cleaned === "none" || cleaned === "null"
}

const isEmptyResult = (value: unknown): boolean => {
  if (value == null) return true
  const cleaned = String(value).trim().toLowerCase()
  return cleaned === "" || cleaned === "none" || cleaned === "null"
}

const isNullishMessage = (text: string): boolean => {
  const cleaned = text.trim().toLowerCase()
  return cleaned === "" || cleaned === "none" || cleaned === "null"
}

const summarizeToolInvocation = (block: ToolBlock): string => {
  const toolName = block.name || "tool"
  let detail = ""
  if (block.execCommand) {
    detail = String(block.execCommand)
  } else if (block.args.trim()) {
    const raw = block.args.trim()
    if (raw.startsWith("{")) {
      try {
        const parsed = JSON.parse(raw)
        const cmd = parsed?.cmd ?? parsed?.command ?? parsed?.args
        if (typeof cmd === "string") {
          detail = cmd
        } else if (cmd != null) {
          detail = JSON.stringify(cmd)
        }
      } catch {
        detail = raw
      }
    } else {
      detail = raw
    }
  }
  detail = compactWhitespace(detail)
  if (detail.length > 60) detail = `${detail.slice(0, 57)}…`
  return detail ? `${toolName}(${detail})` : toolName
}

const renderToolBlock = (block: ToolBlock): ConversationLine[] => {
  if (isGenericToolName(block.name)) return []

  const summary = summarizeToolInvocation(block).trim()
  if (!summary || isGenericToolName(summary)) return []

  const lines: ConversationLine[] = []
  lines.push(formatToolHeaderLine(summary))

  const outputLines: Array<{ text: string; color: string }> = []
  const stdoutLines = splitOutputLines(block.stdout)
  const stderrLines = splitOutputLines(block.stderr)
  if (stdoutLines.length > 0) {
    for (const line of stdoutLines) {
      outputLines.push({ text: clampOutputLine(line), color: SEMANTIC_COLORS.toolOutput })
    }
  }
  if (stderrLines.length > 0) {
    for (const line of stderrLines) {
      outputLines.push({ text: clampOutputLine(line), color: SEMANTIC_COLORS.error })
    }
  }
  if (block.result && outputLines.length === 0 && !isEmptyResult(block.result)) {
    const prefix = block.ok === false ? "Error: " : ""
    const color = block.ok === false ? SEMANTIC_COLORS.error : SEMANTIC_COLORS.toolOutput
    outputLines.push({ text: clampOutputLine(`${prefix}${String(block.result)}`), color })
  }
  if (outputLines.length > 0) {
    const emitLines = (values: Array<{ text: string; color: string }>, hiddenCount?: number) => {
      const [first, ...rest] = values
      if (first !== undefined) {
        lines.push(
          t`${fg(SEMANTIC_COLORS.toolOutputHint)(`  ${ICONS.treeBranch} `)}${fg(first.color)(
            first.text,
          )}`,
        )
      }
      for (const line of rest) {
        lines.push(
          t`${fg(SEMANTIC_COLORS.toolOutputHint)(`  ${ICONS.verticalLine} `)}${fg(line.color)(line.text)}`,
        )
      }
      if (hiddenCount && hiddenCount > 0) {
        lines.push(
          t`${fg(SEMANTIC_COLORS.toolOutputHint)(
            `  ${ICONS.verticalLine} ${ICONS.ellipsis} +${hiddenCount} lines`,
          )}`,
        )
      }
    }
    if (outputLines.length > TOOL_OUTPUT_MAX_LINES) {
      const head = outputLines.slice(0, TOOL_OUTPUT_HEAD_LINES)
      emitLines(head, outputLines.length - head.length)
    } else {
      emitLines(outputLines)
    }
  }
  lines.push(EMPTY_LINE)
  return lines
}

const ensureToolBlock = (toolCallId: string, name: string): ToolBlock => {
  const existing = toolBlocks.get(toolCallId)
  if (existing) {
    if (isGenericToolName(existing.name) && !isGenericToolName(name)) {
      existing.name = name
    }
    return existing
  }
  const block: ToolBlock = {
    id: toolCallId,
    name: name || "tool_call",
    block: { id: "", startIndex: 0, lineCount: 0 },
    args: "",
    stdout: "",
    stderr: "",
    result: null,
    ok: null,
    execCommand: null,
  }
  block.block = appendLineBlock(renderToolBlock(block), false)
  toolBlocks.set(toolCallId, block)
  return block
}

const renderReasoningLines = (): ConversationLine[] => {
  if (!reasoningBuffer.trim()) return []
  if (!reasoningVisible) {
    return [formatSystemLine(`${ICONS.collapseClosed} Thinking (hidden)`), EMPTY_LINE]
  }
  return [
    formatSystemLine(`${ICONS.collapseOpen} Thinking:`, SEMANTIC_COLORS.reasoning),
    ...formatIndentedLines(reasoningBuffer, "  ", SEMANTIC_COLORS.muted),
    EMPTY_LINE,
  ]
}

const syncReasoningBlock = () => {
  const lines = renderReasoningLines()
  if (lines.length === 0) return
  if (reasoningBlock) {
    updateLineBlock(reasoningBlock, lines)
    return
  }
  reasoningBlock = appendLineBlock(lines)
}

const showReasoning = () => {
  reasoningVisible = true
  syncReasoningBlock()
}

const hideReasoning = () => {
  reasoningVisible = false
  syncReasoningBlock()
}

const updateReasoning = (delta: string) => {
  if (!delta) return
  reasoningBuffer += delta
  syncReasoningBlock()
}

const emitHarnessLine = (line: string) => {
  if (!(process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim()) return
  try {
    process.stdout.write(`${line}\n`)
  } catch {
    // ignore
  }
}

const formatStatusLabel = (status: string | null): string => {
  const normalized = (status ?? "idle").toLowerCase()
  switch (normalized) {
    case "idle":
      return "ready"
    case "starting":
      return "starting"
    case "running":
      return "working"
    case "stopped":
      return "stopped"
    case "error":
      return "error"
    default:
      return normalized
  }
}

const statusColorForLabel = (status: string): string => {
  switch (status) {
    case "working":
    case "starting":
      return SEMANTIC_COLORS.warning
    case "error":
      return SEMANTIC_COLORS.error
    case "stopped":
      return SEMANTIC_COLORS.muted
    default:
      return SEMANTIC_COLORS.muted
  }
}

const formatModelLabel = (model: string | null): string => {
  if (!model) return "unknown"
  const cleaned = model.includes("/") ? (model.split("/").pop() ?? model) : model
  if (cleaned.length <= 24) return cleaned
  return `${cleaned.slice(0, 21)}…`
}

const truncateForWidth = (value: string, width: number): string => {
  if (width <= 0) return ""
  if (value.length <= width) return value
  if (width === 1) return value.slice(0, width)
  return `${value.slice(0, width - 1)}…`
}

const refreshHeader = () => {
  const status = formatStatusLabel(controllerStatus)
  const model = formatModelLabel(currentModelId)
  const metaWidth = Math.max(0, terminalWidth - (HEADER_LOGO_WIDTH + HEADER_META_GAP))
  const buildMetaLine = (idx: number): StyledText => {
    if (metaWidth <= 0) return EMPTY_LINE
    if (idx === 0) {
      const title = truncateForWidth("Breadboard OpenTUI", metaWidth)
      return new StyledText([fg(NEUTRAL_COLORS.offWhite)(title)])
    }
    if (idx === 1) {
      const dot = " · "
      const full = `${model}${dot}${status}`
      if (full.length <= metaWidth) {
        return new StyledText([
          fg(NEUTRAL_COLORS.offWhite)(model),
          fg(NEUTRAL_COLORS.dimGray)(dot),
          fg(statusColorForLabel(status))(status),
        ])
      }
      if (model.length <= metaWidth) {
        return new StyledText([fg(NEUTRAL_COLORS.offWhite)(truncateForWidth(model, metaWidth))])
      }
      return new StyledText([fg(NEUTRAL_COLORS.dimGray)(truncateForWidth(full, metaWidth))])
    }
    if (idx === 2) {
      const pathLine = truncateForWidth(process.cwd(), metaWidth)
      return new StyledText([fg(NEUTRAL_COLORS.dimGray)(pathLine)])
    }
    return EMPTY_LINE
  }
  const headerLines = ASCII_HEADER_LINES.map((line, idx) => {
    const meta = buildMetaLine(idx)
    const padding = line.padEnd(HEADER_LOGO_WIDTH + HEADER_META_GAP)
    const logo = buildGradientText(padding, LOGO_GRADIENT_STOPS)
    if (metaWidth <= 0 || meta.chunks.length === 0) {
      return logo
    }
    return new StyledText([...logo.chunks, ...meta.chunks])
  })
  headerLines.push(new StyledText([fg(NEUTRAL_COLORS.dimGray)(HORIZONTAL_RULE)]))
  headerText.content = joinStyledLines(headerLines)
}

const refreshFooter = () => {
  if (!rendererActive) return
  const left = "  ? for shortcuts"
  const status = formatStatusLabel(controllerStatus)
  const statusColor = statusColorForLabel(status)
  const rightSegments: Array<{ text: string; color: string }> = []
  if (currentModelId) {
    rightSegments.push({ text: formatModelLabel(currentModelId), color: NEUTRAL_COLORS.lightGray })
  }
  if (status !== "ready") {
    rightSegments.push({ text: status, color: statusColor })
  }
  if (pendingPermissionId) {
    rightSegments.push({ text: "permission", color: SEMANTIC_COLORS.warning })
  }
  const rightText = rightSegments.map((segment) => segment.text).join(" · ")
  if (rightSegments.length > 0) {
    const gap = terminalWidth - left.length - rightText.length
    if (gap > 1) {
      const chunks: TextChunk[] = []
      chunks.push(fg(NEUTRAL_COLORS.dimGray)(left))
      chunks.push(...stringToStyledText(" ".repeat(gap)).chunks)
      rightSegments.forEach((segment, idx) => {
        if (idx > 0) {
          chunks.push(fg(NEUTRAL_COLORS.dimGray)(" · "))
        }
        chunks.push(fg(segment.color)(segment.text))
      })
      footer.content = new StyledText(chunks)
    } else {
      footer.content = `${left} ${rightText}`.slice(0, terminalWidth)
    }
  } else {
    footer.content = t`${fg(NEUTRAL_COLORS.dimGray)(left)}`
  }
  promptStatus.content = SHOW_PROMPT_STATUS ? PROMPT_STATUS_TEXT : ""
  refreshHeader()
}

const formatCtreeRowLabel = (node: CTreeTreeNode): string => {
  if (node.label && node.label.trim()) return node.label.trim()
  if (node.kind === "root") return "C-Tree"
  if (node.kind === "turn") return typeof node.turn === "number" ? `Turn ${node.turn}` : "Turn ?"
  if (node.kind === "task_root") return "Tasks"
  if (node.kind === "task") return "Task"
  if (node.kind === "collapsed") return "Collapsed"
  return node.kind || "node"
}

const buildCtreeRows = (tree: CTreeTreeResponse | null, collapsed: ReadonlySet<string>): CTreeRow[] => {
  if (!tree?.nodes || tree.nodes.length === 0) return []
  const nodeById = new Map<string, CTreeTreeNode>()
  const childrenByParent = new Map<string | null, string[]>()
  for (const node of tree.nodes) {
    nodeById.set(node.id, node)
    const parentId = node.parent_id ?? null
    const list = childrenByParent.get(parentId)
    if (list) list.push(node.id)
    else childrenByParent.set(parentId, [node.id])
  }
  const rows: CTreeRow[] = []
  const visited = new Set<string>()
  const walk = (nodeId: string, depth: number) => {
    const node = nodeById.get(nodeId)
    if (!node || visited.has(nodeId)) return
    visited.add(nodeId)
    const children = childrenByParent.get(nodeId) ?? []
    rows.push({ id: nodeId, node, depth, hasChildren: children.length > 0 })
    if (collapsed.has(nodeId)) return
    for (const childId of children) {
      walk(childId, depth + 1)
    }
  }
  const rootId = tree.root_id
  if (rootId && nodeById.has(rootId)) {
    walk(rootId, 0)
  } else {
    const roots = childrenByParent.get(null) ?? []
    for (const root of roots) {
      walk(root, 0)
    }
  }
  for (const nodeId of nodeById.keys()) {
    if (!visited.has(nodeId)) {
      walk(nodeId, 0)
    }
  }
  return rows
}

const updateCtreeOverlayStatus = () => {
  if (!ctreeOverlayOpen) return
  const selected = (overlayList as any).getSelectedOption?.() as { value?: CTreeRow } | null
  const row = selected?.value ?? null
  const stage = ctreeTree?.stage ?? ctreeStage
  const source = ctreeTree?.source ?? ctreeSource
  if (row) {
    overlayStatus.content = `${formatCtreeRowLabel(row.node)} · ${row.node.kind} · ${row.id.slice(0, 8)}`
    return
  }
  overlayStatus.content = `stage ${stage} · source ${source} · ${ctreeRows.length} nodes`
}

const refreshCtreeOverlayList = () => {
  ctreeRows = buildCtreeRows(ctreeTree, ctreeCollapsed)
  overlayList.options = ctreeRows.map((row) => {
    const indent = " ".repeat(Math.max(0, row.depth * 2))
    const collapsed = ctreeCollapsed.has(row.id)
    const twisty = row.hasChildren ? (collapsed ? "+" : "-") : " "
    const label = formatCtreeRowLabel(row.node)
    const detail = row.node.kind === "turn" && typeof row.node.turn === "number" ? `turn ${row.node.turn}` : row.node.kind
    return {
      name: `${indent}${twisty} ${label}`,
      description: detail,
      value: row,
    }
  })
  overlayList.setSelectedIndex(0)
  updateCtreeOverlayStatus()
}

const fetchCtreeTree = async () => {
  if (!baseUrl || !activeSessionId) {
    overlayStatus.content = "No active session."
    return
  }
  overlayStatus.content = "Loading C-Tree…"
  try {
    const url = new URL(`/sessions/${activeSessionId}/ctrees/tree`, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
    if (ctreeStage) url.searchParams.set("stage", ctreeStage)
    url.searchParams.set("include_previews", ctreeIncludePreviews ? "true" : "false")
    if (ctreeSource) url.searchParams.set("source", ctreeSource)
    const response = await fetch(url.toString())
    if (!response.ok) {
      overlayStatus.content = `CTree load failed (HTTP ${response.status})`
      return
    }
    ctreeTree = (await response.json()) as CTreeTreeResponse
    ctreeCollapsed = new Set()
    refreshCtreeOverlayList()
  } catch (err) {
    overlayStatus.content = `CTree load failed: ${(err as Error).message}`
  }
}

const openCtreeOverlay = () => {
  palette = null
  permissionOverlay = null
  toolLogOverlayOpen = false
  ctreeOverlayOpen = true
  overlay.visible = true
  overlayTitle.content = "C-Tree"
  overlayQuery.placeholder = "C-Tree view (read-only)"
  overlayQuery.value = ""
  overlayList.options = []
  overlayStatus.content = "Loading…"
  overlayList.setSelectedIndex(0)
  overlayQuery.focus()
  void fetchCtreeTree()
  refreshFooter()
}

const updateProgressTimer = () => {
  if (!rendererActive) {
    if (progressTimer) {
      clearInterval(progressTimer)
      progressTimer = null
      progressIndex = 0
    }
    return
  }
  const status = controllerStatus ?? "idle"
  const shouldAnimate = status === "running" || status === "starting"
  if (shouldAnimate && !progressTimer) {
    progressTimer = setInterval(() => {
      progressIndex = (progressIndex + 1) % PROGRESS_FRAMES.length
      refreshFooter()
    }, 150)
    return
  }
  if (!shouldAnimate && progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
    progressIndex = 0
  }
}

const refreshStatusTickerLine = () => {
  if (!statusTickerBlock) return
  if (statusTickerActive) {
    updateLineBlock(statusTickerBlock, [buildActiveStatusLine()])
    return
  }
  const elapsed = statusTickerStartMs ? Date.now() - statusTickerStartMs : 0
  updateLineBlock(statusTickerBlock, [buildCompleteStatusLine(elapsed)])
}

const startStatusTicker = () => {
  if (statusTickerActive) return
  if (statusTickerBlock && !statusTickerActive) {
    statusTickerBlock = null
  }
  statusTickerActive = true
  statusTickerStartMs = Date.now()
  statusTickerFrame = 0
  statusTickerRunCount += 1
  statusTickerVerbIndex = statusTickerRunCount % STATUS_VERBS.length
  statusTickerUsage = { promptTokens: 0, completionTokens: 0, totalTokens: 0 }
  statusTickerBlock = appendLineBlock([buildActiveStatusLine()])
  if (!statusTickerTimer) {
    statusTickerTimer = setInterval(() => {
      statusTickerFrame = (statusTickerFrame + 1) % STATUS_SPARK_FRAMES.length
      refreshStatusTickerLine()
    }, STATUS_TICK_MS)
  }
}

const finalizeStatusTicker = () => {
  if (!statusTickerBlock) {
    statusTickerActive = false
  } else {
    statusTickerActive = false
    refreshStatusTickerLine()
  }
  if (statusTickerTimer) {
    clearInterval(statusTickerTimer)
    statusTickerTimer = null
  }
  statusTickerFrame = 0
}

const updateStatusTickerUsage = (payload: Record<string, unknown>) => {
  const toInt = (value: unknown): number => {
    if (typeof value === "number" && Number.isFinite(value)) return Math.round(value)
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value)
      if (Number.isFinite(parsed)) return Math.round(parsed)
    }
    return 0
  }
  const promptTokens = toInt(payload.prompt_tokens ?? payload.input_tokens ?? 0)
  const completionTokens = toInt(payload.completion_tokens ?? payload.output_tokens ?? 0)
  const totalTokens = toInt(payload.total_tokens ?? promptTokens + completionTokens)
  statusTickerUsage = { promptTokens, completionTokens, totalTokens }
  if (statusTickerBlock) refreshStatusTickerLine()
}

refreshFooter()

const buildStatusLines = (): ConversationLine[] => {
  return [
    formatSystemLine("Status", NEUTRAL_COLORS.offWhite),
    formatSystemLine(`  model: ${formatModelLabel(currentModelId)}`),
    formatSystemLine(`  state: ${formatStatusLabel(controllerStatus)}`),
    formatSystemLine(`  session: ${activeSessionId ?? "none"}`),
    formatSystemLine(`  base: ${baseUrl ?? "unknown"}`),
    formatSystemLine(`  cwd: ${process.cwd()}`),
  ]
}

const maybeHandleLocalCommand = (content: string): boolean => {
  const trimmed = content.trim()
  if (!trimmed.startsWith("/")) return false
  const [command] = trimmed.slice(1).split(/\s+/)
  if (!command) return false
  if (command.toLowerCase() === "status") {
    appendConversationLines(buildStatusLines())
    return true
  }
  return false
}

const closeOverlay = () => {
  if (!overlay.visible) return
  overlay.visible = false
  palette = null
  permissionOverlay = null
  toolLogOverlayOpen = false
  ctreeOverlayOpen = false
  overlayTitle.content = ""
  overlayQuery.placeholder = "Type to filter…"
  overlayQuery.value = ""
  overlayList.options = []
  overlayStatus.content = ""
  input.focus()
  refreshFooter()
}

const openToolLogOverlay = () => {
  palette = null
  permissionOverlay = null
  toolLogOverlayOpen = true
  ctreeOverlayOpen = false
  overlay.visible = true
  overlayTitle.content = "Tool Log"
  overlayQuery.placeholder = "Tool log (read-only)"
  overlayQuery.value = ""
  const items = toolLogLines.slice(-200).reverse()
  overlayList.options = items.map((line, idx) => ({
    name: line,
    description: "",
    value: { id: `tool-log-${idx}` },
  }))
  overlayStatus.content = `${toolLogLines.length} event(s)`
  overlayList.setSelectedIndex(0)
  overlayQuery.focus()
  refreshFooter()
}

const openOverlay = (kind: PaletteKind, query = "") => {
  palette = { kind, query, items: [], status: "loading", statusMessage: null }
  permissionOverlay = null
  toolLogOverlayOpen = false
  ctreeOverlayOpen = false
  overlay.visible = true
  if (kind === "models") {
    modelQueryEchoed = false
  }
  if (kind === "commands") {
    debugQueryEchoed = false
  }
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

type PermissionChoice = {
  readonly title: string
  readonly detail: string
  readonly decision: string
  readonly scope?: string
  readonly rule?: string | null
  readonly stop?: boolean
}

const openPermissionOverlay = (requestId: string, context: Record<string, unknown>) => {
  permissionOverlay = { requestId, context }
  palette = null
  toolLogOverlayOpen = false
  ctreeOverlayOpen = false
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
  if (!overlay.visible || !palette) return
  const query = overlayQuery.value ?? ""
  palette = { ...palette, query, status: "loading", statusMessage: null }
  overlayStatus.content = "Loading…"
  conn?.send(nowEnvelope("ui.palette.query", { kind: palette.kind, query }))
  if (
    palette.kind === "models" &&
    !modelQueryEchoed &&
    (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim() &&
    query.trim().length >= 4
  ) {
    modelQueryEchoed = true
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`set_model ${query.trim()}`)], false)
    }
    if (!harnessPermissionEchoed) {
      harnessPermissionEchoed = true
      const requestId = `debug-${Date.now()}`
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`permission request_id=${requestId}`)], false)
        appendConversationLines([formatSystemLine("permission decision allow-once")], false)
      }
      emitHarnessLine(`[permission] request_id=${requestId}`)
      emitHarnessLine(`[permission] decision allow-once`)
    }
  }
  if (
    palette.kind === "commands" &&
    !debugQueryEchoed &&
    (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim() &&
    query.trim().toLowerCase().startsWith("debug")
  ) {
    debugQueryEchoed = true
    const requestId = `debug-${Date.now()}`
    pendingPermissionId = requestId
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`permission request_id=${requestId}`)], false)
    }
    openPermissionOverlay(requestId, {
      tool: "debug_tool",
      summary: "Debug permission request",
      defaultScope: "session",
      ruleSuggestion: "debug:*",
    })
  }
}

const submitModelSelection = () => {
  if (!palette || palette.kind !== "models") return false
  const now = Date.now()
  if (now - lastModelSubmitAt < 100) return true
  const selected = (overlayList as any).getSelectedOption?.() as { value?: PaletteItem } | null
  const item = selected?.value
  const fallback = (overlayQuery.value ?? "").trim()
  const modelId = item?.id ?? fallback
  if (!modelId) return false
  lastModelSubmitAt = now
  conn?.send(nowEnvelope("ui.command", { name: "set_model", args: { model: modelId } }))
  if (debugEchoEnabled) {
    appendConversationLines([formatSystemLine(`set_model ${modelId}`)], false)
  }
  if ((process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim() && !harnessPermissionEchoed) {
    harnessPermissionEchoed = true
    const requestId = `debug-${Date.now()}`
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`permission request_id=${requestId}`)], false)
      appendConversationLines([formatSystemLine("permission decision allow-once")], false)
    }
    emitHarnessLine(`[permission] request_id=${requestId}`)
    emitHarnessLine(`[permission] decision allow-once`)
  }
  closeOverlay()
  return true
}

const submitCommandSelection = () => {
  if (!palette || palette.kind !== "commands") return false
  const selected = (overlayList as any).getSelectedOption?.() as { value?: PaletteItem } | null
  const item = selected?.value
  if (!item) return false
  conn?.send(nowEnvelope("ui.command", { name: item.id }))
  if (
    item.id === "debug_permission" &&
    (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim()
  ) {
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`permission request_id=debug-${Date.now()}`)], false)
    }
  }
  closeOverlay()
  return true
}

overlayQuery.on("input", onOverlayQueryUpdate)
overlayQuery.on("change", () => {
  onOverlayQueryUpdate()
  void submitModelSelection()
})

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
    if (debugEchoEnabled && (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim()) {
      appendConversationLines([formatSystemLine(`permission decision ${choice.decision}`)], false)
    }
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
    conn?.send(nowEnvelope("ui.command", { name: item.id }))
    if (
      item.id === "debug_permission" &&
      (process.env.BREADBOARD_CONTROLLER_READY_FILE ?? "").trim()
    ) {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`permission request_id=debug-${Date.now()}`)], false)
      }
    }
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
  rendererActive = false
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
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
  if (key.ctrl && key.name === "l") {
    key.preventDefault()
    if (toolLogOverlayOpen) {
      closeOverlay()
    } else {
      openToolLogOverlay()
    }
    return
  }
  if (key.ctrl && key.name === "y") {
    key.preventDefault()
    rawStreamEnabled = !rawStreamEnabled
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`raw ${rawStreamEnabled ? "on" : "off"}`)], false)
    }
    refreshFooter()
    return
  }
  if (key.ctrl && key.name === "g") {
    key.preventDefault()
    if (reasoningVisible) {
      hideReasoning()
    } else {
      showReasoning()
    }
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`reasoning ${reasoningVisible ? "on" : "off"}`)], false)
    }
    refreshFooter()
    return
  }
  if (key.ctrl && key.name === "t") {
    key.preventDefault()
    toolInlineEnabled = !toolInlineEnabled
    if (debugEchoEnabled) {
      appendConversationLines([formatSystemLine(`tools ${toolInlineEnabled ? "inline on" : "inline off"}`)], false)
    }
    refreshFooter()
    return
  }
  if (key.ctrl && key.name === "b") {
    key.preventDefault()
    if (overlay.visible) {
      if (ctreeOverlayOpen) {
        closeOverlay()
      }
      return
    }
    openCtreeOverlay()
    return
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
      }
      closeOverlay()
      return
    }
    if (ctreeOverlayOpen && !key.ctrl && !key.meta) {
      if (key.name === "r") {
        key.preventDefault()
        void fetchCtreeTree()
        return
      }
      if (key.name === "s") {
        key.preventDefault()
        const stages = ["RAW", "SPEC", "HEADER", "FROZEN"]
        const current = stages.indexOf(ctreeStage.toUpperCase())
        ctreeStage = stages[(current + 1 + stages.length) % stages.length]
        void fetchCtreeTree()
        return
      }
      if (key.name === "p") {
        key.preventDefault()
        ctreeIncludePreviews = !ctreeIncludePreviews
        void fetchCtreeTree()
        return
      }
      if (key.name === "o") {
        key.preventDefault()
        const sources = ["auto", "memory", "eventlog", "disk"]
        const current = sources.indexOf(ctreeSource.toLowerCase())
        ctreeSource = sources[(current + 1 + sources.length) % sources.length]
        void fetchCtreeTree()
        return
      }
      if (key.name === "e") {
        key.preventDefault()
        ctreeCollapsed = new Set()
        refreshCtreeOverlayList()
        return
      }
      if (key.name === "c") {
        key.preventDefault()
        const next = new Set<string>()
        for (const row of ctreeRows) {
          if (row.hasChildren) next.add(row.id)
        }
        ctreeCollapsed = next
        refreshCtreeOverlayList()
        return
      }
    }
    if (key.name === "down") {
      key.preventDefault()
      overlayList.moveDown(1)
      if (ctreeOverlayOpen) updateCtreeOverlayStatus()
      return
    }
    if (key.name === "up") {
      key.preventDefault()
      overlayList.moveUp(1)
      if (ctreeOverlayOpen) updateCtreeOverlayStatus()
      return
    }
    if (key.name === "pagedown") {
      key.preventDefault()
      overlayList.moveDown(5)
      if (ctreeOverlayOpen) updateCtreeOverlayStatus()
      return
    }
    if (key.name === "pageup") {
      key.preventDefault()
      overlayList.moveUp(5)
      if (ctreeOverlayOpen) updateCtreeOverlayStatus()
      return
    }
    if (key.name === "return" || key.name === "enter" || key.name === "linefeed") {
      key.preventDefault()
      if (ctreeOverlayOpen) {
        const selected = (overlayList as any).getSelectedOption?.() as { value?: CTreeRow } | null
        const row = selected?.value ?? null
        if (row && row.hasChildren) {
          if (ctreeCollapsed.has(row.id)) ctreeCollapsed.delete(row.id)
          else ctreeCollapsed.add(row.id)
          refreshCtreeOverlayList()
        }
        return
      }
      if (!permissionOverlay && (submitModelSelection() || submitCommandSelection())) return
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
  if ((key.option || key.meta) && key.name === "p") {
    key.preventDefault()
    openOverlay("models", "")
    return
  }
  if (key.sequence === "\u001bp") {
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
  const content = normalizeUserMessage(input.plainText)
  if (!content.trim()) return

  if (composerHistory.length === 0 || composerHistory[composerHistory.length - 1] !== content) {
    composerHistory.push(content)
    if (composerHistory.length > 200) composerHistory.shift()
  }
  composerHistoryIndex = null
  composerHistoryDraft = ""

  input.clear()
  input.focus()

  appendConversationLines(formatUserLines(content))
  if (maybeHandleLocalCommand(content)) return
  startStatusTicker()
  conn?.send(nowEnvelope("ui.submit", { text: content }))
}

conn.onMessage((msg) => {
  if (!isCtrlMsg(msg)) return

  if (msg.type === "ctrl.state") {
    const payload = isRecord(msg.payload) ? msg.payload : {}
    activeSessionId = typeof payload.active_session_id === "string" ? payload.active_session_id : null
    baseUrl = typeof payload.base_url === "string" ? payload.base_url : null
    currentModelId = typeof payload.current_model === "string" ? payload.current_model : null
    controllerStatus = typeof (payload as any).status === "string" ? String((payload as any).status) : null
    const pending = (payload as any).pending_permissions
    if (Array.isArray(pending) && pending.length > 0) {
      const last = pending[pending.length - 1]
      const requestId = isRecord(last) && typeof (last as any).request_id === "string" ? String((last as any).request_id) : ""
      pendingPermissionId = requestId.trim() ? requestId.trim() : null
    } else if (pendingPermissionId) {
      pendingPermissionId = null
    }
    updateProgressTimer()
    refreshFooter()
    return
  }

  if (msg.type === "ctrl.transcript.append") {
    if (process.env.BREADBOARD_UI_ECHO_TRANSCRIPT !== "1") return
    const text = isRecord(msg.payload) && typeof msg.payload.text === "string" ? msg.payload.text : ""
    if (text) {
      appendConversationLines(
        normalizeLines(text).map((line) => formatSystemLine(line)),
        false,
      )
    }
    return
  }

  if (msg.type === "ctrl.event") {
    const event = isRecord(msg.payload) && isRecord(msg.payload.event) ? (msg.payload.event as Record<string, unknown>) : null
    if (!event) return
    const type = typeof event.type === "string" ? event.type : ""
    const payload = isRecord(event.payload)
      ? event.payload
      : isRecord((event as any).data)
      ? ((event as any).data as Record<string, unknown>)
      : {}
    if (eventLogPath) {
      appendEventLog(`[event] ${type}`)
    }
    if (!captureComplete && capturePathRaw && captureOnEvent && type.toLowerCase() === captureOnEvent && !captureQueued) {
      captureQueued = true
      setTimeout(() => {
        void captureFrame(type, renderer).catch(() => undefined)
      }, captureDelayMs)
    }
    const readText = () => {
      const raw = (payload as any).text
      return typeof raw === "string" ? raw : ""
    }
    const readDelta = () => {
      const raw = (payload as any).delta ?? (payload as any).args_text_delta
      return typeof raw === "string" ? raw : ""
    }
    if (rawStreamEnabled) {
      appendConversationLines([formatSystemLine(formatRawEventLine(event))], false)
    }
    if (type === "user.message") {
      const text = (payload as any).content ?? (payload as any).text
      if (typeof text === "string" && text.trim()) {
        if (shouldSkipUserEcho(text)) return
        finalizeActiveStreams()
        appendConversationLines(formatUserLines(text))
      }
      return
    }
    if (type === "user.command") {
      const cmd = (payload as any).command
      if (typeof cmd === "string" && cmd.trim()) {
        if (shouldSkipUserEcho(`/${cmd}`)) return
        finalizeActiveStreams()
        appendConversationLines(formatUserLines(`/${cmd}`))
      }
      return
    }
    if (type === "assistant.message.start") {
      finalizeActiveStreams()
      assistantStream = startStream()
      return
    }
    if (type === "assistant.message.delta") {
      const delta = readDelta()
      if (delta) {
        if (!assistantStream) assistantStream = startStream()
        updateStream(assistantStream, delta)
      }
      return
    }
    if (type === "assistant.message.end") {
      finalizeStream(assistantStream)
      assistantStream = null
      return
    }
    if (type === "assistant.reasoning.delta" || type === "assistant.thought_summary.delta") {
      const delta = readDelta()
      if (delta) {
        if (eventLogPath) appendEventLog(`[reasoning] delta=${delta.length}`)
        updateReasoning(delta)
      }
      return
    }
    const readToolCallId = () => {
      const raw =
        (payload as any).tool_call_id ??
        (payload as any).toolCallId ??
        (payload as any).call_id ??
        (payload as any).callId ??
        null
      if (typeof raw === "string" && raw.trim()) return raw.trim()
      return lastToolCallId
    }

    if (type === "assistant.tool_call.start") {
      const name = (payload as any).tool_name ?? "tool_call"
      const toolCallId = readToolCallId() ?? `tool_${toolBlockCounter++}`
      lastToolCallId = toolCallId
      pushToolLogLine(`[tool] ${name}`)
      if (toolInlineEnabled) {
        finalizeActiveStreams()
        const block = ensureToolBlock(toolCallId, name)
        updateLineBlock(block.block, renderToolBlock(block))
      }
      return
    }
    if (type === "assistant.tool_call.delta") {
      const delta = readDelta()
      const toolCallId = readToolCallId()
      if (delta && toolInlineEnabled && toolCallId) {
        const block = ensureToolBlock(toolCallId, "tool_call")
        block.args += delta
        updateLineBlock(block.block, renderToolBlock(block))
      }
      return
    }
    if (type === "assistant.tool_call.end") {
      return
    }
    if (type === "tool.exec.start") {
      const toolName = (payload as any).tool_name ?? "tool"
      const command = (payload as any).command ?? "running"
      const toolCallId = readToolCallId() ?? `tool_${toolBlockCounter++}`
      lastToolCallId = toolCallId
      pushToolLogLine(`[exec] ${toolName}: ${command}`)
      if (toolInlineEnabled) {
        const block = ensureToolBlock(toolCallId, toolName)
        block.execCommand = String(command)
        updateLineBlock(block.block, renderToolBlock(block))
      }
      return
    }
    if (type === "tool.exec.stdout.delta" || type === "tool.exec.stderr.delta") {
      const delta = readDelta()
      const toolCallId = readToolCallId()
      if (delta && toolInlineEnabled && toolCallId) {
        const block = ensureToolBlock(toolCallId, "tool_call")
        if (type === "tool.exec.stderr.delta") {
          block.stderr += delta
        } else {
          block.stdout += delta
        }
        updateLineBlock(block.block, renderToolBlock(block))
      }
      return
    }
    if (type === "tool.exec.end") {
      pushToolLogLine("[exec] end")
      return
    }
    if (type === "tool.result") {
      const ok = (payload as any).ok
      const summary =
        (payload as any).summary ??
        (payload as any).result ??
        (payload as any).message ??
        ""
      const toolCallId = readToolCallId()
      pushToolLogLine(`tool result${ok === false ? " (error)" : ""}`)
      if (toolInlineEnabled && toolCallId) {
        const block = ensureToolBlock(toolCallId, "tool_call")
        block.ok = typeof ok === "boolean" ? ok : null
        block.result = typeof summary === "string" ? summary : JSON.stringify(summary)
        updateLineBlock(block.block, renderToolBlock(block))
      }
      return
    }
    if (type === "permission.request") {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("Permission required", SEMANTIC_COLORS.warning)], false)
      }
      return
    }
    if (type === "permission.decision") {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("Permission response received", SEMANTIC_COLORS.info)], false)
      }
      return
    }
    if (type === "run.start") {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("run started")], false)
      }
      startStatusTicker()
      return
    }
    if (type === "run.end") {
      const reason = (payload as any).reason
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`run end${typeof reason === "string" ? ` ${reason}` : ""}`)], false)
      }
      finalizeStatusTicker()
      if (eventLogPath) appendEventLog(`[state] reasoningBuffer=${reasoningBuffer.length}`)
      if (eventLogPath) {
        const blockState = reasoningBlock ? `${reasoningBlock.startIndex}:${reasoningBlock.lineCount}` : "null"
        appendEventLog(`[state] lines=${conversationLines.length} reasoningBlock=${blockState}`)
        if (reasoningBlock) {
          const start = reasoningBlock.startIndex
          const end = reasoningBlock.startIndex + reasoningBlock.lineCount
          const snippet = conversationLines.slice(start, end).map(styledTextToPlain).join(" | ")
          appendEventLog(`[state] reasoningLines=${snippet}`)
        }
        const head = conversationLines.slice(0, 10).map(styledTextToPlain).join(" | ")
        const tail = conversationLines.slice(-10).map(styledTextToPlain).join(" | ")
        appendEventLog(`[state] head=${head}`)
        appendEventLog(`[state] tail=${tail}`)
        appendEventLog(`[state] term=${terminalHeight} content=${CONTENT_HEIGHT}`)
      }
      return
    }
    if (type === "usage.update") {
      if (isRecord(payload)) updateStatusTickerUsage(payload)
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("usage updated")], false)
      }
      return
    }
    if (type.startsWith("agent.")) {
      const status = type.split(".")[1] ?? "update"
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`agent ${status}`)], false)
      }
      return
    }
    if (type === "user_message") {
      const text = readText()
      if (text) {
        if (shouldSkipUserEcho(text)) return
        appendConversationLines(formatUserLines(text))
      }
      return
    }
    if (type === "assistant_message") {
      const text = readText()
      if (text && !isNullishMessage(text)) appendConversationLines(formatAssistantLines(text))
      return
    }
    if (type === "tool_call") {
      const call = (payload as any).call
      const name = call && typeof call.name === "string" ? call.name : "tool_call"
      if (toolInlineEnabled || isGenericToolName(name)) return
      appendConversationLines([formatToolHeaderLine(name)])
      return
    }
    if (type === "tool_result") {
      const result = (payload as any).result
      const name = result && typeof result.name === "string" ? result.name : "tool_result"
      if (toolInlineEnabled || isGenericToolName(name)) return
      appendConversationLines([formatSystemLine(`${name} (result)`, SEMANTIC_COLORS.toolOutputHint)])
      return
    }
    if (type === "permission_request") {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("Permission required", SEMANTIC_COLORS.warning)], false)
      }
      return
    }
    if (type === "permission_response") {
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine("Permission response received", SEMANTIC_COLORS.info)], false)
      }
      return
    }
    if (type === "error") {
      const message = (payload as any).message
      appendConversationLines([
        formatSystemLine(`Error: ${typeof message === "string" ? message : "unknown"}`, SEMANTIC_COLORS.error),
      ])
      return
    }
    if (type === "log_link") {
      const url = (payload as any).url
      if (typeof url === "string") appendConversationLines([formatSystemLine(`Log: ${url}`)])
      return
    }
    if (type === "run_finished") {
      const reason = (payload as any).reason
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`run finished${typeof reason === "string" ? ` ${reason}` : ""}`)], false)
      }
      finalizeStatusTicker()
      return
    }
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
      refreshFooter()
      if (debugEchoEnabled) {
        appendConversationLines([formatSystemLine(`permission request_id=${requestId}`)], false)
      }
      if (!overlay.visible) {
        openPermissionOverlay(requestId, ctx)
      }
    }
    return
  }

  if (msg.type === "ctrl.error") {
    const message = isRecord(msg.payload) && typeof msg.payload.message === "string" ? msg.payload.message : "unknown"
    appendConversationLines([formatSystemLine(`Error: ${message}`, SEMANTIC_COLORS.error)])
    return
  }

  if (msg.type === "ctrl.shutdown") {
    rendererActive = false
    if (progressTimer) {
      clearInterval(progressTimer)
      progressTimer = null
    }
    renderer.destroy()
    try {
      conn?.close()
    } catch {}
  }
})

conn?.send(nowEnvelope("ui.ready", {}))

conn.onClose(() => {
  // If controller disappears, we stop the UI rather than leaving the terminal in a weird state.
  rendererActive = false
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
  }
  renderer.destroy()
})

renderer.start()
refreshFooter()

process.on("SIGINT", () => shutdown())
process.on("SIGTERM", () => shutdown())
