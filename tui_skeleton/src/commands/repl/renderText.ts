import chalk from "chalk"
import path from "node:path"
import type { ConversationEntry, LiveSlotEntry, GuardrailNotice, ToolLogEntry, ToolLogKind } from "../../repl/types.js"
import type { ReplState } from "./controller.js"
import { ASCII_HEADER, HEADER_COLOR, speakerColor, TOOL_EVENT_COLOR } from "../../repl/viewUtils.js"
import type { Block, InlineNode } from "@stream-mdx/core/types"
import {
  BRAND_COLORS,
  NEUTRAL_COLORS,
  SEMANTIC_COLORS,
  resolveAsciiOnly,
  resolveColorMode,
  resolveColorToken,
  resolveIcons,
  type ColorMode,
} from "../../repl/designSystem.js"
import { maybeHighlightCode } from "../../repl/shikiHighlighter.js"
import type { MarkdownCodeLine, TuiDiffLineKind, TuiTokenLine } from "../../repl/types.js"
import { renderTokenLine, tokenLineFromThemed, tokenLineFromV1 } from "../../repl/markdown/tokenRender.js"
import type { DiffBlock as StreamDiffBlock, DiffKind, ThemedLine, TokenLineV1 } from "../../repl/markdown/streamMdxCompatTypes.js"
import { computeInlineDiffSpans, shiftSpans, type InlineSpan } from "../../repl/diff/inlineDiff.js"
import {
  buildTranscriptWindow,
  computeDiffPreview,
  ENTRY_COLLAPSE_HEAD,
  ENTRY_COLLAPSE_TAIL,
  MAX_TRANSCRIPT_ENTRIES,
  MIN_TRANSCRIPT_ROWS,
  shouldAutoCollapseEntry,
} from "../../repl/transcriptUtils.js"
import { buildTranscript } from "../../repl/transcriptBuilder.js"
import type { TranscriptItem, TranscriptSystemItem, TranscriptToolItem } from "../../repl/transcriptModel.js"

export interface RenderTextOptions {
  readonly colors?: boolean
  readonly colorMode?: ColorMode
  readonly asciiOnly?: boolean
  readonly renderProfile?: "default" | "claude"
  readonly includeHints?: boolean
  readonly includeModelMenu?: boolean
  readonly includeHeader?: boolean
  readonly includeStatus?: boolean
  readonly includeStreamingTail?: boolean
  readonly maxWidth?: number
}

const formatHeader = (useColors: boolean, colorMode: ColorMode): string[] => {
  if (!useColors || colorMode !== "truecolor") {
    return [...ASCII_HEADER]
  }
  return ASCII_HEADER.map((line: string) => chalk.bold.hex(HEADER_COLOR)(line))
}

const applyColor = (text: string, hex: string, useColors: boolean, colorMode: ColorMode): string => {
  if (!useColors || colorMode === "none") return text
  const token = resolveColorToken(hex, colorMode)
  if (token.mode === "none") return text
  if (token.mode === "truecolor") return chalk.hex(token.value)(text)
  if (token.mode === "ansi256") return chalk.ansi256(token.value)(text)
  const ansi16 = (chalk as unknown as Record<string, (input: string) => string>)[token.value]
  return ansi16 ? ansi16(text) : text
}

const applyBackgroundColor = (text: string, hex: string, useColors: boolean, colorMode: ColorMode): string => {
  if (!useColors || colorMode === "none") return text
  const token = resolveColorToken(hex, colorMode)
  if (token.mode === "none") return text
  if (token.mode === "truecolor") return chalk.bgHex(token.value)(text)
  if (token.mode === "ansi256") return chalk.bgAnsi256(token.value)(text)
  if (token.mode === "ansi16") {
    const bgName = `bg${token.value[0].toUpperCase()}${token.value.slice(1)}`
    const fn = (chalk as unknown as Record<string, (value: string) => string>)[bgName]
    return fn ? fn(text) : text
  }
  return text
}

const applyDim = (text: string, useColors: boolean, colorMode: ColorMode): string =>
  useColors && colorMode !== "none" ? chalk.dim(text) : text

const applyBold = (text: string, useColors: boolean, colorMode: ColorMode): string =>
  useColors && colorMode !== "none" ? chalk.bold(text) : text

const applyItalic = (text: string, useColors: boolean, colorMode: ColorMode): string =>
  useColors && colorMode !== "none" ? chalk.italic(text) : text

const applyUnderline = (text: string, useColors: boolean, colorMode: ColorMode): string =>
  useColors && colorMode !== "none" ? chalk.underline(text) : text

const formatCostUsd = (value: number): string => {
  if (value >= 1) return `$${value.toFixed(2)}`
  if (value >= 0.1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(4)}`
}

const formatLatency = (ms: number): string => {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)}s`
  return `${(ms / 1000).toFixed(1)}s`
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

const isHorizontalRuleLine = (line: string): boolean => {
  const trimmed = line.trim()
  return /^([-*_])(?:\s*\1){2,}$/.test(trimmed)
}

const renderHorizontalRule = (
  maxWidth: number,
  useColors: boolean,
  colorMode: ColorMode,
  asciiOnly: boolean,
): string => {
  const glyph = asciiOnly ? "-" : "─"
  const line = glyph.repeat(Math.max(1, maxWidth))
  return useColors ? applyColor(line, NEUTRAL_COLORS.dimGray, useColors, colorMode) : line
}

const formatStatusLine = (
  state: ReplState,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  hollowDot: string,
): string => {
  const { sessionId, status, pendingResponse, stats } = state
  const filledDot = icons.bullet
  const glyph = pendingResponse
    ? applyColor(filledDot, SEMANTIC_COLORS.tool, useColors, colorMode)
    : applyColor(filledDot, SEMANTIC_COLORS.info, useColors, colorMode)
  const modelGlyph = applyColor(filledDot, SEMANTIC_COLORS.info, useColors, colorMode)
  const remoteGlyph = stats.remote
    ? applyColor(filledDot, SEMANTIC_COLORS.info, useColors, colorMode)
    : applyColor(hollowDot, NEUTRAL_COLORS.dimGray, useColors, colorMode)
  const toolsGlyph =
    stats.toolCount > 0
      ? applyColor(filledDot, SEMANTIC_COLORS.tool, useColors, colorMode)
      : applyColor(hollowDot, NEUTRAL_COLORS.dimGray, useColors, colorMode)
  const eventsGlyph =
    stats.eventCount > 0
      ? applyColor(filledDot, SEMANTIC_COLORS.info, useColors, colorMode)
      : applyColor(hollowDot, NEUTRAL_COLORS.dimGray, useColors, colorMode)
  const turnGlyph =
    stats.lastTurn != null
      ? applyColor(filledDot, SEMANTIC_COLORS.success, useColors, colorMode)
      : applyColor(hollowDot, NEUTRAL_COLORS.dimGray, useColors, colorMode)

  const parts = [
    sessionId ? sessionId.slice(0, 12) : "(pending)",
    glyph,
    status,
    modelGlyph,
    `model ${stats.model}`,
    remoteGlyph,
    stats.remote ? "remote" : "local",
    eventsGlyph,
    `events ${stats.eventCount}`,
    toolsGlyph,
    `tools ${stats.toolCount}`,
  ]
  if (stats.lastTurn != null) {
    parts.push(turnGlyph, `turn ${stats.lastTurn}`)
  }
  const usage = stats.usage
  if (usage) {
    const usageParts: string[] = []
    const hasTokenInputs =
      usage.totalTokens != null || usage.promptTokens != null || usage.completionTokens != null
    if (hasTokenInputs) {
      const total =
        usage.totalTokens ??
        (usage.promptTokens != null || usage.completionTokens != null
          ? (usage.promptTokens ?? 0) + (usage.completionTokens ?? 0)
          : undefined)
      if (total != null && Number.isFinite(total)) {
        usageParts.push(`tok ${Math.round(total)}`)
      }
    }
    if (usage.costUsd != null && Number.isFinite(usage.costUsd)) {
      usageParts.push(`cost ${formatCostUsd(usage.costUsd)}`)
    }
    if (usage.latencyMs != null && Number.isFinite(usage.latencyMs)) {
      usageParts.push(`lat ${formatLatency(usage.latencyMs)}`)
    }
    if (usageParts.length > 0) {
      const usageText = usageParts.join(" · ")
      parts.push(applyDim(usageText, useColors, colorMode))
    }
  }
  return parts.join(" ")
}

const colorizeDiffLine = (line: string, useColors: boolean, colorMode: ColorMode): string => {
  if (!useColors || colorMode === "none") return line
  if (line.includes("\u001b")) return line
  if (line.startsWith("diff --git") || line.startsWith("index ")) {
    return applyColor(line, SEMANTIC_COLORS.info, useColors, colorMode)
  }
  if (line.startsWith("@@")) return applyColor(line, BRAND_COLORS.duneOrange, useColors, colorMode)
  if (line.startsWith("---") || line.startsWith("+++")) {
    return applyColor(line, SEMANTIC_COLORS.info, useColors, colorMode)
  }
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return applyColor(line, SEMANTIC_COLORS.success, useColors, colorMode)
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return applyColor(line, SEMANTIC_COLORS.error, useColors, colorMode)
  }
  return line
}

const ANSI_PATTERN = /\u001b\[[0-9;]*m/g
const stripAnsi = (value: string): string => value.replace(ANSI_PATTERN, "")

const sliceAnsi = (value: string, width: number): string => {
  let visible = 0
  let index = 0
  let out = ""
  while (index < value.length && visible < width) {
    if (value[index] === "\u001b") {
      const match = value.slice(index).match(ANSI_PATTERN)
      if (match && match.index === 0) {
        const token = match[0]
        out += token
        index += token.length
        continue
      }
    }
    out += value[index]
    visible += 1
    index += 1
  }
  if (value.includes("\u001b")) {
    out += "\u001b[0m"
  }
  return out
}

const clipLineToWidth = (line: string, width: number, ellipsis: string): string => {
  const plain = stripAnsi(line)
  if (width <= 0 || plain.length <= width) return line
  if (width <= ellipsis.length) return ellipsis.slice(0, width)
  const clipped = plain.slice(0, width - ellipsis.length)
  if (line.includes("\u001b")) {
    const sliced = sliceAnsi(line, width - ellipsis.length)
    return `${sliced}${ellipsis}`
  }
  return `${clipped}${ellipsis}`
}

const wrapLineToWidth = (line: string, width: number): string[] => {
  const plain = stripAnsi(line)
  if (width <= 0 || plain.length <= width) return [plain]
  const segments: string[] = []
  for (let index = 0; index < plain.length; index += width) {
    segments.push(plain.slice(index, index + width))
  }
  return segments.length > 0 ? segments : [plain]
}

const DIFF_BG_COLORS = {
  add: "#123225",
  del: "#3a1426",
  hunk: "#2c2436",
} as const

type DiffLineKind = "meta" | "hunk" | "add" | "del" | "context"
type AnnotatedDiffLine = { line: string; oldNo: number | null; newNo: number | null; kind: DiffLineKind }

const DIFF_HUNK_PATTERN = /@@\s*-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@/

const DIFF_INLINE_BG_COLORS = {
  add: "#1a4d33",
  del: "#5a2740",
  hunk: "#3b2f4d",
} as const

const TOOL_PREVIEW_MAX_LINES = 24
const TOOL_WRITE_PREVIEW_MAX_LINES_CLAUDE = 10
const TOOL_DIFF_PREVIEW_MAX_LINES_CLAUDE = 80

const normalizeDiffLanguage = (value: unknown): string | null => {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed) return null
  const normalized = trimmed.toLowerCase()
  if (normalized === "diff") return null
  return normalized
}

const inferLanguageFromPath = (value: unknown): string | null => {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed) return null
  const pathValue = trimmed.split(/[?#]/)[0] ?? trimmed
  const ext = path.extname(pathValue).replace(/^\./, "").toLowerCase()
  if (!ext) return null
  if (ext === "mjs" || ext === "cjs") return "js"
  if (ext === "mts" || ext === "cts") return "ts"
  if (ext === "mdx") return "md"
  return ext
}

const resolveDiffLanguage = (block: Record<string, unknown>): string | null => {
  const direct = normalizeDiffLanguage(block.language)
  if (direct) return direct
  return inferLanguageFromPath(block.filePath)
}

const hexToRgb = (hex: string): [number, number, number] | null => {
  const normalized = hex.replace("#", "")
  if (normalized.length !== 6) return null
  const r = Number.parseInt(normalized.slice(0, 2), 16)
  const g = Number.parseInt(normalized.slice(2, 4), 16)
  const b = Number.parseInt(normalized.slice(4, 6), 16)
  if ([r, g, b].some((value) => Number.isNaN(value))) return null
  return [r, g, b]
}

const ANSI_BG_RESET = "\u001b[49m"
const ANSI_BG_CODES: Record<string, string> = {
  black: "\u001b[40m",
  red: "\u001b[41m",
  green: "\u001b[42m",
  yellow: "\u001b[43m",
  blue: "\u001b[44m",
  magenta: "\u001b[45m",
  cyan: "\u001b[46m",
  white: "\u001b[47m",
  gray: "\u001b[100m",
  grey: "\u001b[100m",
  redBright: "\u001b[101m",
  greenBright: "\u001b[102m",
  yellowBright: "\u001b[103m",
  blueBright: "\u001b[104m",
  magentaBright: "\u001b[105m",
  cyanBright: "\u001b[106m",
  whiteBright: "\u001b[107m",
}

const readAnsiToken = (value: string, start: number): string | null => {
  if (value[start] !== "\u001b" || value[start + 1] !== "[") return null
  const end = value.indexOf("m", start + 2)
  if (end === -1) return null
  return value.slice(start, end + 1)
}

const backgroundAnsiCode = (hex: string, colorMode: ColorMode, useColors: boolean): string => {
  if (!useColors || colorMode === "none") return ""
  const token = resolveColorToken(hex, colorMode)
  if (token.mode === "none") return ""
  if (token.mode === "truecolor") {
    const rgb = hexToRgb(token.value)
    return rgb ? `\u001b[48;2;${rgb[0]};${rgb[1]};${rgb[2]}m` : ""
  }
  if (token.mode === "ansi256") {
    return `\u001b[48;5;${token.value}m`
  }
  if (token.mode === "ansi16") {
    return ANSI_BG_CODES[token.value] ?? ""
  }
  return ""
}

const applyInlineBackground = (line: string, spans: InlineSpan[], baseBg: string, inlineBg: string): string => {
  if (!spans.length || !baseBg || !inlineBg) return line
  const boundaries: Array<{ pos: number; code: string; order: number }> = []
  spans.forEach((span) => {
    boundaries.push({ pos: span.start, code: inlineBg, order: 1 })
    boundaries.push({ pos: span.end, code: baseBg, order: 0 })
  })
  boundaries.sort((a, b) => (a.pos === b.pos ? a.order - b.order : a.pos - b.pos))
  let boundaryIndex = 0
  let visibleIndex = 0
  let out = ""
  let index = 0
  while (index < line.length) {
    while (boundaryIndex < boundaries.length && boundaries[boundaryIndex].pos === visibleIndex) {
      out += boundaries[boundaryIndex].code
      boundaryIndex += 1
    }
    const token = readAnsiToken(line, index)
    if (token) {
      out += token
      index += token.length
      continue
    }
    out += line[index]
    visibleIndex += 1
    index += 1
  }
  while (boundaryIndex < boundaries.length && boundaries[boundaryIndex].pos === visibleIndex) {
    out += boundaries[boundaryIndex].code
    boundaryIndex += 1
  }
  return out
}

const classifyDiffLine = (line: string): DiffLineKind => {
  if (line.startsWith("diff --git") || line.startsWith("index ")) return "meta"
  if (line.startsWith("---") || line.startsWith("+++")) return "meta"
  if (line.startsWith("@@")) return "hunk"
  if (line.startsWith("+") && !line.startsWith("+++")) return "add"
  if (line.startsWith("-") && !line.startsWith("---")) return "del"
  return "context"
}

const annotateUnifiedDiffLines = (lines: string[]): AnnotatedDiffLine[] => {
  let oldNo: number | null = null
  let newNo: number | null = null
  return lines.map((line) => {
    const kind = classifyDiffLine(line)
    if (kind === "hunk") {
      const match = line.match(DIFF_HUNK_PATTERN)
      if (match) {
        oldNo = Number.parseInt(match[1], 10)
        newNo = Number.parseInt(match[2], 10)
      }
      return { line, oldNo: null, newNo: null, kind }
    }
    if (kind === "add") {
      const currentNew = newNo
      if (newNo != null) newNo += 1
      return { line, oldNo: null, newNo: currentNew, kind }
    }
    if (kind === "del") {
      const currentOld = oldNo
      if (oldNo != null) oldNo += 1
      return { line, oldNo: currentOld, newNo: null, kind }
    }
    if (kind === "context") {
      const currentOld = oldNo
      const currentNew = newNo
      if (oldNo != null) oldNo += 1
      if (newNo != null) newNo += 1
      return { line, oldNo: currentOld, newNo: currentNew, kind }
    }
    return { line, oldNo: null, newNo: null, kind }
  })
}

const applyDiffBackground = (
  line: string,
  kind: DiffLineKind,
  useColors: boolean,
  colorMode: ColorMode,
): string => {
  if (!useColors || colorMode === "none") return line
  if (kind === "add") return applyBackgroundColor(line, DIFF_BG_COLORS.add, useColors, colorMode)
  if (kind === "del") return applyBackgroundColor(line, DIFF_BG_COLORS.del, useColors, colorMode)
  if (kind === "hunk") return applyBackgroundColor(line, DIFF_BG_COLORS.hunk, useColors, colorMode)
  return line
}

const styleDiffLine = (
  line: string,
  kind: DiffLineKind,
  useColors: boolean,
  colorMode: ColorMode,
  withBackground = true,
): string => {
  if (!useColors || colorMode === "none") return line
  const hasAnsi = line.includes("\u001b")
  let styled = line
  if (!hasAnsi) {
    if (kind === "add") styled = applyColor(line, SEMANTIC_COLORS.success, useColors, colorMode)
    else if (kind === "del") styled = applyColor(line, SEMANTIC_COLORS.error, useColors, colorMode)
    else if (kind === "hunk") styled = applyColor(line, BRAND_COLORS.duneOrange, useColors, colorMode)
    else if (kind === "meta") styled = applyColor(line, SEMANTIC_COLORS.info, useColors, colorMode)
  }
  if (!withBackground) return styled
  return applyDiffBackground(styled, kind, useColors, colorMode)
}

const renderInlineNodes = (
  nodes: ReadonlyArray<InlineNode> | undefined,
  useColors: boolean,
  colorMode: ColorMode,
): string => {
  if (!nodes) return ""
  const render = (node: InlineNode): string => {
    switch (node.kind) {
      case "text":
        return node.text
      case "strong":
        return applyBold(renderInlineNodes(node.children, useColors, colorMode), useColors, colorMode)
      case "em":
        return applyItalic(renderInlineNodes(node.children, useColors, colorMode), useColors, colorMode)
      case "strike":
        return useColors && colorMode !== "none"
          ? chalk.strikethrough(renderInlineNodes(node.children, useColors, colorMode))
          : renderInlineNodes(node.children, useColors, colorMode)
      case "code":
        return useColors && colorMode !== "none"
          ? chalk.bgHex(NEUTRAL_COLORS.darkGray).hex(NEUTRAL_COLORS.nearWhite)(` ${node.text} `)
          : node.text
      case "link":
        return `${renderInlineNodes(node.children, useColors, colorMode)}${
          node.href ? (useColors ? applyUnderline(node.href, useColors, colorMode) : ` (${node.href})`) : ""
        }`
      case "mention":
        return useColors
          ? applyColor(`@${node.handle}`, SEMANTIC_COLORS.info, useColors, colorMode)
          : `@${node.handle}`
      case "citation":
        return useColors
          ? applyColor(`[${node.id}]`, SEMANTIC_COLORS.info, useColors, colorMode)
          : `[${node.id}]`
      case "math-inline":
      case "math-display":
        return useColors ? applyColor(node.tex, SEMANTIC_COLORS.info, useColors, colorMode) : node.tex
      case "footnote-ref":
        return useColors
          ? applyColor(`[^${node.label}]`, BRAND_COLORS.duneOrange, useColors, colorMode)
          : `[^${node.label}]`
      case "image":
        return node.alt ? `![${node.alt}]` : "[image]"
      case "br":
        return "\n"
      default:
        return "children" in node
          ? renderInlineNodes((node as { children?: InlineNode[] }).children, useColors, colorMode)
          : ""
    }
  }
  return nodes.map(render).join("")
}

const stripFence = (raw: string): { code: string; langHint?: string } => {
  const normalized = raw.replace(/\r\n?/g, "\n")
  const lines = normalized.split("\n")
  if (lines.length === 0) return { code: raw }
  const first = lines[0].trim()
  if (!first.startsWith("```")) return { code: normalized }
  const langHint = first.slice(3).trim() || undefined
  let end = lines.length - 1
  while (end > 0 && lines[end].trim().length === 0) end -= 1
  if (end > 0 && lines[end].trim().startsWith("```")) end -= 1
  return { code: lines.slice(1, end + 1).join("\n"), langHint }
}

const renderCodeLines = (raw: string, lang: string | undefined, useColors: boolean, colorMode: ColorMode): string[] => {
  const { code, langHint } = stripFence(raw)
  const finalLang = (lang ?? langHint ?? "").toLowerCase()
  const lines = (code || raw).replace(/\r\n?/g, "\n").split("\n")
  const isDiff = finalLang.includes("diff")
  return lines.map((line: string) => {
    if (!useColors || colorMode === "none") return line
    if (isDiff) return colorizeDiffLine(line, true, colorMode)
    if (line.startsWith("+")) return applyColor(line, SEMANTIC_COLORS.success, useColors, colorMode)
    if (line.startsWith("-")) return applyColor(line, SEMANTIC_COLORS.error, useColors, colorMode)
    if (line.startsWith("@@")) return applyColor(line, SEMANTIC_COLORS.info, useColors, colorMode)
    return applyColor(line, NEUTRAL_COLORS.offWhite, useColors, colorMode)
  })
}

const MAX_TOKENIZED_DIFF_LINES = 400

const normalizeDiffKind = (kind: DiffKind | null | undefined): TuiDiffLineKind => {
  if (kind === "add") return "add"
  if (kind === "remove") return "del"
  if (kind === "hunk") return "hunk"
  if (kind === "meta") return "meta"
  return "context"
}

const buildTokenStyleFns = (useColors: boolean, colorMode: ColorMode) => ({
  color: (text: string, color: string) => applyColor(text, color, useColors, colorMode),
  bold: (text: string) => applyBold(text, useColors, colorMode),
  italic: (text: string) => applyItalic(text, useColors, colorMode),
  underline: (text: string) => applyUnderline(text, useColors, colorMode),
})

const styleMarkdownDiffLine = (
  line: string,
  kind: TuiDiffLineKind,
  useColors: boolean,
  colorMode: ColorMode,
): string => {
  if (!useColors || colorMode === "none") return line
  const hasAnsi = line.includes("\u001b")
  if (kind === "add") return applyBackgroundColor(line, DIFF_BG_COLORS.add, useColors, colorMode)
  if (kind === "del") return applyBackgroundColor(line, DIFF_BG_COLORS.del, useColors, colorMode)
  if (kind === "hunk" && !hasAnsi) return applyColor(line, BRAND_COLORS.duneOrange, useColors, colorMode)
  if (kind === "meta" && !hasAnsi) return applyColor(line, SEMANTIC_COLORS.info, useColors, colorMode)
  return line
}

const renderMarkdownDiffLine = (
  raw: string,
  kind: TuiDiffLineKind,
  tokens: TuiTokenLine | null | undefined,
  useColors: boolean,
  colorMode: ColorMode,
): string => {
  if (!tokens || tokens.length === 0) {
    const colored = colorizeDiffLine(raw, useColors, colorMode)
    return styleMarkdownDiffLine(colored, kind, useColors, colorMode)
  }
  const marker = raw.startsWith("+") || raw.startsWith("-") || raw.startsWith(" ") ? raw[0] : ""
  const rendered = renderTokenLine(tokens, buildTokenStyleFns(useColors, colorMode))
  const hasMarker = marker && tokens[0]?.content?.startsWith(marker)
  const combined = marker && !hasMarker ? `${marker}${rendered}` : rendered
  return styleMarkdownDiffLine(combined, kind, useColors, colorMode)
}

const renderDiffBlocks = (
  diffBlocks: ReadonlyArray<StreamDiffBlock>,
  useColors: boolean,
  colorMode: ColorMode,
): string[] => {
  const lines: string[] = []
  const totalLines = diffBlocks.reduce((acc, block) => acc + (block.lines?.length ?? 0), 0)
  const allowTokens = totalLines <= MAX_TOKENIZED_DIFF_LINES
  for (const block of diffBlocks) {
    for (const line of block.lines ?? []) {
      const kind = line.kind as TuiDiffLineKind
      const tokens = allowTokens ? tokenLineFromThemed((line.tokens as ThemedLine | null) ?? null) : null
      const raw = typeof line.raw === "string" ? line.raw : ""
      lines.push(renderMarkdownDiffLine(raw, kind, tokens, useColors, colorMode))
    }
  }
  return lines
}

const renderDiffFromCodeLines = (
  codeLines: ReadonlyArray<MarkdownCodeLine>,
  useColors: boolean,
  colorMode: ColorMode,
): string[] => {
  const isDiff = codeLines.some((line) => line.diffKind != null)
  const allowTokens = !isDiff || codeLines.length <= MAX_TOKENIZED_DIFF_LINES
  return codeLines.map((line) => {
    const kind = normalizeDiffKind(line.diffKind ?? null)
    const raw = line.text ?? ""
    const tokens = allowTokens ? tokenLineFromV1(line.tokens as TokenLineV1 | null, "dark") : null
    return renderMarkdownDiffLine(raw, kind, tokens, useColors, colorMode)
  })
}

const blockToLines = (
  block: Block,
  useColors: boolean,
  colorMode: ColorMode,
  asciiOnly: boolean,
  maxWidth: number,
): string[] => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  switch (block.type) {
    case "thematic-break":
    case "thematicBreak":
    case "horizontal-rule":
    case "hr":
      return [renderHorizontalRule(maxWidth, useColors, colorMode, asciiOnly)]
    case "heading": {
      const levelRaw = typeof meta.level === "number" ? meta.level : typeof meta.depth === "number" ? meta.depth : 1
      const level = Math.min(6, Math.max(1, levelRaw || 1))
      const prefix = "#".repeat(level)
      const text = block.payload.inline ? renderInlineNodes(block.payload.inline, useColors, colorMode) : block.payload.raw ?? ""
      const line = `${prefix} ${text}`
      return [useColors ? applyColor(applyBold(line, useColors, colorMode), BRAND_COLORS.duneOrange, useColors, colorMode) : line]
    }
    case "blockquote": {
      const content = block.payload.inline
        ? renderInlineNodes(block.payload.inline, useColors, colorMode)
        : block.payload.raw ?? ""
      return content
        .split(/\r?\n/)
        .map((line: string) =>
          useColors
            ? applyColor(`> ${line}`, NEUTRAL_COLORS.midGray, useColors, colorMode)
            : `> ${line}`,
        )
    }
    case "code": {
      const lang =
        typeof meta.lang === "string"
          ? meta.lang
          : typeof meta.language === "string"
            ? meta.language
            : typeof meta.info === "string"
              ? meta.info
              : undefined
      const diffBlocks = Array.isArray(meta.diffBlocks) ? (meta.diffBlocks as StreamDiffBlock[]) : null
      if (diffBlocks && diffBlocks.length > 0) {
        return renderDiffBlocks(diffBlocks, useColors, colorMode)
      }
      const codeLines = Array.isArray(meta.codeLines) ? (meta.codeLines as MarkdownCodeLine[]) : null
      if (codeLines && codeLines.length > 0) {
        return renderDiffFromCodeLines(codeLines, useColors, colorMode)
      }
      const raw = block.payload.raw ?? ""
      return renderCodeLines(raw, lang, useColors, colorMode)
    }
    case "paragraph": {
      const value = block.payload.inline
        ? renderInlineNodes(block.payload.inline, useColors, colorMode)
        : block.payload.raw ?? ""
      if (!block.payload.inline && isHorizontalRuleLine(value)) {
        return [renderHorizontalRule(maxWidth, useColors, colorMode, asciiOnly)]
      }
      return value.split(/\r?\n/)
    }
    default: {
      const raw = block.payload.raw ?? ""
      return raw.split(/\r?\n/)
    }
  }
}

const blocksToLines = (
  blocks: ReadonlyArray<Block> | undefined,
  useColors: boolean,
  colorMode: ColorMode,
  asciiOnly: boolean,
  maxWidth: number,
): { lines: string[]; plain: string[] } => {
  if (!blocks || blocks.length === 0) return { lines: [], plain: [] }
  const lines: string[] = []
  for (const block of blocks) {
    const rendered = blockToLines(block, useColors, colorMode, asciiOnly, maxWidth)
    if (rendered.length > 0) lines.push(...rendered)
  }
  return { lines, plain: lines.map(stripAnsi) }
}

const formatConversationEntry = (
  entry: ConversationEntry,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  asciiOnly: boolean,
  maxWidth: number,
  collapsePredicate?: (entry: ConversationEntry) => boolean,
): string[] => {
  const hasRichBlocks = Boolean(entry.richBlocks && entry.richBlocks.length > 0)
  const rawText = entry.text.trim()
  if (!hasRichBlocks && !entry.markdownError && (!rawText || rawText.toLowerCase() === "none")) return []
  const speakerGlyph =
    entry.speaker === "user"
      ? icons.userChevron
      : entry.speaker === "assistant"
        ? icons.assistantDot
        : icons.systemDot
  const coloredGlyph = useColors ? applyColor(speakerGlyph, speakerColor(entry.speaker), useColors, colorMode) : speakerGlyph
  const pad = "  "
  const useRich = Boolean(hasRichBlocks && !entry.markdownError)
  const rich = useRich
    ? blocksToLines(entry.richBlocks, useColors, colorMode, asciiOnly, maxWidth)
    : { lines: entry.text.split(/\r?\n/), plain: entry.text.split(/\r?\n/) }
  const lines = rich.lines
  const plain = rich.plain
  const errorLine =
    entry.markdownError && entry.speaker === "assistant"
      ? `${coloredGlyph} rich markdown disabled: ${entry.markdownError}`
      : null
  const asLine = (text: string, index: number) =>
    `${index === 0 ? `${coloredGlyph} ` : pad}${colorizeDiffLine(text, useColors, colorMode)}`
  const shouldCollapse = collapsePredicate ? collapsePredicate(entry) : shouldAutoCollapseEntry(entry)
  if (!shouldCollapse) {
    const rendered = lines.map((line: string, index: number) => asLine(line, index))
    return errorLine ? [errorLine, ...rendered] : rendered
  }
  const head = lines.slice(0, ENTRY_COLLAPSE_HEAD)
  const tail = lines.slice(-ENTRY_COLLAPSE_TAIL)
  const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
  const diffPreview = computeDiffPreview(plain)
  const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
  const summaryParts = [
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden (ctrl+o to expand)`,
    diffPreview ? `${deltaGlyph} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
  ].filter(Boolean)
  const summaryLine = summaryParts.length > 0 ? summaryParts.join(" — ") : `${hiddenCount} lines hidden`
  const collapseLabel = useColors ? applyDim(icons.ellipsis, useColors, colorMode) : icons.ellipsis
  return [
    ...(errorLine ? [errorLine] : []),
    ...head.map((line: string, index: number) => asLine(line, index)),
    `${pad} ${collapseLabel} ${summaryLine}`,
    ...tail.map((line: string, index: number) => asLine(line, head.length + index + 1)),
  ]
}

const partitionConversation = (entries: ReadonlyArray<ConversationEntry>) => {
  const finals: ConversationEntry[] = []
  let streaming: ConversationEntry | undefined
  for (const entry of entries) {
    if (entry.phase === "streaming") {
      streaming = entry
    } else {
      finals.push(entry)
    }
  }
  return { finals, streaming }
}

const TOOL_COLLAPSE_THRESHOLD = 24
const TOOL_COLLAPSE_HEAD = 6
const TOOL_COLLAPSE_TAIL = 6

const formatStatusLines = (text: string): string[] => {
  const rawLines = text.split(/\r?\n/)
  if (rawLines.length === 0) return [text]
  let header = rawLines[0] ?? ""
  const rest = rawLines.slice(1)
  const match = header.match(/^\[([^\]]+)\]\s*(.*)$/)
  if (match) {
    const rawTag = match[1]
    const remainder = match[2] ?? ""
    const tagLabel = rawTag.replace(/[_-]+/g, " ").trim()
    const tagLower = tagLabel.toLowerCase()
    const remainderTrimmed = remainder.trim()
    const titleTag = tagLabel.replace(/\b\w/g, (char) => char.toUpperCase())
    if (tagLower === "status") {
      header = remainderTrimmed || rest.shift() || ""
    } else if (!remainderTrimmed) {
      header = titleTag
    } else if (remainderTrimmed.toLowerCase().startsWith(tagLower)) {
      header = remainderTrimmed
    } else {
      header = `${titleTag} · ${remainderTrimmed}`
    }
  }
  const lines = [header, ...rest]
  return lines.filter((line, index) => index === 0 || line.trim().length > 0)
}

const formatStatusEntry = (
  entry: ToolLogEntry,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  maxWidth: number,
): string[] => {
  const lines = formatStatusLines(entry.text)
  const safeLines = lines.length > 0 ? lines : [entry.text]
  const isToolHeader = (line: string): boolean => {
    const plain = stripAnsi(line).trim().replace(/^[●•o]\s*/, "")
    if (plain.startsWith("[")) return false
    // Broad match: tool headers look like Name(args) with parentheses.
    return plain.includes("(") && plain.includes(")") && /[A-Za-z]/.test(plain)
  }
  const forceBright = (text: string): string => {
    if (!useColors || colorMode === "none") return text
    return `\u001b[1m\u001b[97m${stripAnsi(text)}\u001b[0m`
  }
  const bullet = icons.bullet
  const glyph =
    entry.status === "error"
      ? useColors
        ? applyColor(bullet, SEMANTIC_COLORS.error, useColors, colorMode)
        : icons.error
      : entry.status === "success"
        ? useColors
          ? applyColor(bullet, SEMANTIC_COLORS.success, useColors, colorMode)
          : bullet
        : entry.status === "pending"
          ? useColors
            ? applyColor(bullet, SEMANTIC_COLORS.warning, useColors, colorMode)
            : bullet
          : useColors
            ? applyColor(bullet, NEUTRAL_COLORS.midGray, useColors, colorMode)
            : bullet
  const colorizeStatusLine = (line: string): string => {
    if (!useColors || colorMode === "none") return line
    if (line.includes("\u001b")) return line
    if (isToolHeader(line)) return applyColor(line, "#ffffff", useColors, colorMode)
    if (entry.status === "error") return applyColor(line, SEMANTIC_COLORS.error, useColors, colorMode)
    if (entry.status === "pending") return applyColor(line, SEMANTIC_COLORS.warning, useColors, colorMode)
    if (entry.status === "success") return applyDim(line, useColors, colorMode)
    return applyDim(line, useColors, colorMode)
  }
  const prefixFirst = `${glyph} `
  const prefixNext = "  "
  const headerIndex = safeLines.findIndex((line) => line.trim().length > 0)
  const renderLine = (line: string, index: number) => {
    const prefix = index === 0 ? prefixFirst : prefixNext
    const available = Math.max(8, maxWidth - prefix.length)
    if (index === headerIndex && isToolHeader(line)) {
      const segments = wrapLineToWidth(line, available)
      return segments
        .map((segment, segmentIndex) => {
          const linePrefix = segmentIndex === 0 ? prefix : prefixNext
          return `${linePrefix}${forceBright(segment)}`
        })
        .join("\n")
    }
    const clipped = clipLineToWidth(line, available, icons.ellipsis)
    const colored = colorizeStatusLine(clipped)
    return `${prefix}${colored}`
  }
  if (safeLines.length <= TOOL_COLLAPSE_THRESHOLD) {
    return safeLines.flatMap((line, index) => renderLine(line, index).split("\n"))
  }
  const head = safeLines.slice(0, TOOL_COLLAPSE_HEAD)
  const tail = safeLines.slice(-TOOL_COLLAPSE_TAIL)
  const hiddenCount = safeLines.length - head.length - tail.length
  const diffPreview = computeDiffPreview(safeLines)
  const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
  const summaryParts = [
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
    diffPreview ? `${deltaGlyph} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
  ].filter(Boolean)
  const collapseLabel = useColors ? applyDim(icons.ellipsis, useColors, colorMode) : icons.ellipsis
  return [
    ...head.flatMap((line, index) => renderLine(line, index).split("\n")),
    `${prefixNext}${collapseLabel} ${summaryParts.join(" — ")}`,
    ...tail.flatMap((line, index) => renderLine(line, head.length + index + 1).split("\n")),
  ]
}

const normalizeDisplayLines = (value: unknown, allowBlank = false): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((line) => (typeof line === "string" ? line.replace(/\r?\n/g, "").trimEnd() : ""))
      .filter((line) => (allowBlank ? line.length >= 0 : line.trim().length > 0))
  }
  if (typeof value === "string") {
    return value
      .split(/\r?\n/)
      .map((line) => line.trimEnd())
      .filter((line) => (allowBlank ? line.length >= 0 : line.trim().length > 0))
  }
  return []
}

const stripDiffPrefix = (line: string): string => {
  if (!line) return line
  const first = line[0]
  if (first === "+" || first === "-" || first === " ") {
    return line.slice(1)
  }
  return line
}

const buildWriteBlockLines = (
  lines: string[],
  useColors: boolean,
  colorMode: ColorMode,
  diffLineNumbers: boolean,
  maxLines = TOOL_PREVIEW_MAX_LINES,
  renderProfile: "default" | "claude" = "default",
  icons: ReturnType<typeof resolveIcons>,
): string[] => {
  const trimmed = lines.map((line) => line.replace(/\r?\n/g, ""))
  const limit = renderProfile === "claude" ? TOOL_WRITE_PREVIEW_MAX_LINES_CLAUDE : maxLines
  const visible = trimmed.slice(0, limit)
  const hidden = Math.max(0, trimmed.length - visible.length)
  if (renderProfile === "claude") {
    const output = visible.map((line) => line.trimEnd())
    if (hidden > 0) {
      output.push(`${icons.ellipsis} +${hidden} lines (ctrl+o to expand)`)
    }
    return output
  }
  const width = diffLineNumbers ? Math.max(2, String(visible.length).length) : 0
  const output = visible.map((line, index) => {
    const number = diffLineNumbers ? String(index + 1).padStart(width, " ") : ""
    const prefix = diffLineNumbers ? `${number} + ` : "+ "
    const combined = `${prefix}${line}`.trimEnd()
    return styleDiffLine(combined, "add", useColors, colorMode, true)
  })
  if (hidden > 0) {
    const spacer = diffLineNumbers ? " ".repeat(width) : ""
    const prefix = diffLineNumbers ? `${spacer}   ` : "  "
    const summary = `${prefix}… (${hidden} lines hidden)`.trimEnd()
    output.push(applyDim(summary, useColors, colorMode))
  }
  return output
}

const buildDiffBlockLines = (
  block: Record<string, unknown>,
  useColors: boolean,
  colorMode: ColorMode,
  diffLineNumbers: boolean,
  renderProfile: "default" | "claude" = "default",
): string[] => {
  const unified = typeof block.unified === "string" ? block.unified : ""
  if (!unified.trim()) return []
  const rawLines = unified.replace(/\r\n?/g, "\n").split("\n")
  const annotated = annotateUnifiedDiffLines(rawLines)
  const allVisible = annotated.filter((entry) => entry.kind !== "meta" && entry.kind !== "hunk")
  const maxLines = renderProfile === "claude" ? TOOL_DIFF_PREVIEW_MAX_LINES_CLAUDE : TOOL_PREVIEW_MAX_LINES
  const hidden = Math.max(0, allVisible.length - maxLines)
  const visible = hidden > 0 ? allVisible.slice(0, maxLines) : allVisible
  const baseLines = visible.map((entry) => stripDiffPrefix(entry.line))
  const diffLang = resolveDiffLanguage(block)
  const highlightedLines =
    useColors && colorMode !== "none" && diffLang && baseLines.length > 0 && baseLines.length <= MAX_TOKENIZED_DIFF_LINES
      ? maybeHighlightCode(baseLines.join("\n"), diffLang)
      : null
  const inlineMap = new Map<number, InlineSpan[]>()
  const pendingDeletes: number[] = []
  visible.forEach((entry, idx) => {
    if (entry.kind === "context") {
      pendingDeletes.length = 0
      return
    }
    if (entry.kind === "del") {
      pendingDeletes.push(idx)
      return
    }
    if (entry.kind === "add" && pendingDeletes.length > 0) {
      const delIndex = pendingDeletes.shift() as number
      const delLine = stripDiffPrefix(visible[delIndex]?.line ?? "")
      const addLine = stripDiffPrefix(entry.line)
      const spans = computeInlineDiffSpans(delLine, addLine)
      if (spans.del.length > 0) inlineMap.set(delIndex, spans.del)
      if (spans.add.length > 0) inlineMap.set(idx, spans.add)
    }
  })
  let width = 2
  let maxNum = 0
  if (diffLineNumbers) {
    maxNum = Math.max(0, ...visible.map((line) => line.newNo ?? line.oldNo ?? 0))
    width = Math.max(2, String(maxNum).length)
  }
  const needsClaudePad = renderProfile === "claude" && diffLineNumbers && maxNum >= 10
  const output = visible.map((entry, index) => {
    const baseLine = highlightedLines?.[index] ?? baseLines[index] ?? stripDiffPrefix(entry.line)
    const marker = entry.kind === "add" ? "+" : entry.kind === "del" ? "-" : " "
    const rawNumber =
      diffLineNumbers && (entry.newNo != null || entry.oldNo != null)
        ? String((entry.newNo ?? entry.oldNo) as number).padStart(width, " ")
        : diffLineNumbers
          ? " ".repeat(width)
          : ""
    const number = needsClaudePad ? ` ${rawNumber}` : rawNumber
    const prefix =
      renderProfile === "claude"
        ? entry.kind === "context"
          ? diffLineNumbers
            ? `${number}  `
            : "  "
          : diffLineNumbers
            ? `${number} ${marker}`
            : `${marker}`
        : diffLineNumbers
          ? `${number} ${marker} `
          : `${marker} `
    const combined = `${prefix}${baseLine}`.trimEnd()
    const spans = inlineMap.get(index) ?? []
    if (spans.length > 0 && (entry.kind === "add" || entry.kind === "del")) {
      const baseFg = styleDiffLine(combined, entry.kind, useColors, colorMode, false)
      const baseBg = backgroundAnsiCode(DIFF_BG_COLORS[entry.kind], colorMode, useColors)
      const inlineBg = backgroundAnsiCode(DIFF_INLINE_BG_COLORS[entry.kind], colorMode, useColors)
      if (baseBg && inlineBg) {
        const shifted = shiftSpans(spans, prefix.length)
        const withInline = applyInlineBackground(baseFg, shifted, baseBg, inlineBg)
        return `${baseBg}${withInline}${ANSI_BG_RESET}`
      }
    }
    return styleDiffLine(combined, entry.kind, useColors, colorMode, true)
  })
  if (hidden > 0) {
    const spacer = diffLineNumbers ? " ".repeat(width) : ""
    const prefix = diffLineNumbers ? `${spacer}   ` : "  "
    const summary = `${prefix}… (${hidden} lines hidden)`.trimEnd()
    output.push(applyDim(summary, useColors, colorMode))
  }
  return output
}

const buildToolDisplayLines = (
  entry: ToolLogEntry,
  display: Record<string, unknown>,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  diffLineNumbers: boolean,
  renderProfile: "default" | "claude" = "default",
): string[] => {
  const title =
    (typeof display.title === "string" && display.title.trim().length > 0 ? display.title : null) ??
    entry.text.split(/\r?\n/)[0] ??
    "Tool"
  const debugRule = typeof display.debug_rule_id === "string" ? display.debug_rule_id : null
  const titleWithDebug = debugRule ? `${title} ⟪${debugRule}⟫` : title
  const allowBlank = renderProfile === "claude"
  let summaryLines = normalizeDisplayLines(display.summary)
  const detailLines = normalizeDisplayLines(display.detail, allowBlank)
  const truncated = isRecord(display.detail_truncated) ? display.detail_truncated : null
  const hiddenCount = truncated && typeof truncated.hidden === "number" ? truncated.hidden : null
  const hint = truncated && typeof truncated.hint === "string" ? truncated.hint : null
  const mode = truncated && typeof truncated.mode === "string" ? truncated.mode : null
  const tailCount = truncated && typeof truncated.tail === "number" ? truncated.tail : null
  const diffBlocks = Array.isArray(display.diff_blocks)
    ? (display.diff_blocks as Array<Record<string, unknown>>)
    : []
  const titleLower = title.toLowerCase()
  const isWrite = titleLower.startsWith("write(")
  const isPatch = titleLower.startsWith("patch(")
  let contentLines = detailLines.length > 0 ? detailLines.slice() : summaryLines.slice()
  if (isPatch && diffBlocks.length > 0 && renderProfile !== "claude") {
    summaryLines = []
    contentLines = detailLines.length > 0 ? detailLines.slice() : []
  }
  if (isWrite && diffBlocks.length === 0 && detailLines.length > 0) {
    contentLines = summaryLines.slice()
    contentLines.push(
      ...buildWriteBlockLines(
        detailLines,
        useColors,
        colorMode,
        diffLineNumbers,
        TOOL_PREVIEW_MAX_LINES,
        renderProfile,
        icons,
      ),
    )
  }
  if (detailLines.length > 0 && hiddenCount && hiddenCount > 0) {
    const summaryLine =
      renderProfile === "claude"
        ? `${icons.ellipsis} +${hiddenCount} lines${hint ? ` ${hint}` : ""}`
        : `${icons.ellipsis} ${hiddenCount} lines hidden${hint ? ` — ${hint}` : ""}`
    if (mode === "head_tail" && tailCount && tailCount > 0 && tailCount < contentLines.length) {
      const head = contentLines.slice(0, contentLines.length - tailCount)
      const tail = contentLines.slice(-tailCount)
      contentLines.length = 0
      contentLines.push(...head, summaryLine, ...tail)
    } else {
      contentLines.push(summaryLine)
    }
  }
  if (diffBlocks.length > 0) {
    diffBlocks.forEach((block) => {
      contentLines.push(...buildDiffBlockLines(block, useColors, colorMode, diffLineNumbers, renderProfile))
    })
  }
  const lines: string[] = [titleWithDebug]
  if (contentLines.length > 0) {
    contentLines.forEach((line, index) => {
      let prefix: string = index === contentLines.length - 1 ? icons.treeBranch : icons.verticalLine
      if (renderProfile === "claude") {
        if (index === 0) {
          lines.push(`⎿ \u00a0${line}`)
          return
        }
        prefix = "  "
      }
      lines.push(`${prefix} ${line}`)
    })
  }
  return lines
}

const formatToolEntry = (
  entry: ToolLogEntry,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  maxWidth: number,
  diffLineNumbers: boolean,
  renderProfile: "default" | "claude" = "default",
): string[] => {
  if (entry.kind === "status") {
    return formatStatusEntry(entry, useColors, colorMode, icons, deltaGlyph, maxWidth)
  }
  let lines = entry.text.split(/\r?\n/)
  if (entry.display && (entry.kind === "call" || entry.kind === "result") && isRecord(entry.display)) {
    lines = buildToolDisplayLines(
      entry,
      entry.display,
      useColors,
      colorMode,
      icons,
      deltaGlyph,
      diffLineNumbers,
      renderProfile,
    )
  }
  const bullet = icons.bullet
  const glyph =
    entry.status === "success"
      ? useColors
        ? applyColor(bullet, SEMANTIC_COLORS.success, useColors, colorMode)
        : bullet
      : entry.status === "error"
        ? useColors
          ? applyColor(bullet, SEMANTIC_COLORS.error, useColors, colorMode)
          : icons.error
        : entry.kind === "call"
          ? useColors
            ? applyColor(bullet, SEMANTIC_COLORS.info, useColors, colorMode)
            : bullet
          : useColors
            ? applyColor(bullet, TOOL_EVENT_COLOR, useColors, colorMode)
            : bullet
  const prefixFirst = `${glyph} `
  const prefixNext = "  "
  const forceBright = (text: string): string => {
    if (!useColors || colorMode === "none") return text
    // Force bright white + bold via explicit ANSI so capture renderer preserves it.
    return `\u001b[1m\u001b[97m${stripAnsi(text)}\u001b[0m`
  }
  const headerIndex = lines.findIndex((line) => line.trim().length > 0)
  const renderLine = (line: string, index: number) => {
    const prefix = index === 0 ? prefixFirst : prefixNext
    const available = Math.max(8, maxWidth - prefix.length)
    const headerBright = index === headerIndex && entry.kind !== "status"
    if (headerBright) {
      const segments = wrapLineToWidth(line, available)
      return segments
        .map((segment, segmentIndex) => {
          const linePrefix = segmentIndex === 0 ? prefix : prefixNext
          return `${linePrefix}${forceBright(segment)}`
        })
        .join("\n")
    }
    const clipped = clipLineToWidth(line, available, icons.ellipsis)
    const colored = colorizeDiffLine(clipped, useColors, colorMode)
    return `${prefix}${colored}`
  }
  if (renderProfile === "claude" || lines.length <= TOOL_COLLAPSE_THRESHOLD) {
    return lines.flatMap((line: string, index: number) => renderLine(line, index).split("\n"))
  }
  const head = lines.slice(0, TOOL_COLLAPSE_HEAD)
  const tail = lines.slice(-TOOL_COLLAPSE_TAIL)
  const hiddenCount = lines.length - head.length - tail.length
  const diffPreview = computeDiffPreview(lines)
  const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
  const summaryParts = [
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
    diffPreview ? `${deltaGlyph} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
  ].filter(Boolean)
  const collapseLabel = useColors ? applyDim(icons.ellipsis, useColors, colorMode) : icons.ellipsis
  return [
    ...head.flatMap((line: string, index: number) => renderLine(line, index).split("\n")),
    `${prefixNext} ${collapseLabel} ${summaryParts.join(" — ")}`,
    ...tail.flatMap((line: string, index: number) => renderLine(line, head.length + index + 1).split("\n")),
  ]
}

const formatTools = (
  state: ReplState,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  maxWidth: number,
  diffLineNumbers: boolean,
  renderProfile: "default" | "claude" = "default",
): string[] => {
  const legacyAwareList = state.toolEvents as Array<ToolLogEntry | string>
  const includeLegacy = state.viewPrefs?.rawStream === true
  const toolHeaderRegex = /^[A-Za-z].*\(.*\)/
  const forceHeaderBright = (line: string): string => {
    if (!useColors || colorMode === "none") return line
    const plain = stripAnsi(line).trim().replace(/^[●•o]\s*/, "")
    if (!toolHeaderRegex.test(plain)) return line
    const firstSpace = line.indexOf(" ")
    if (firstSpace === -1) return `\u001b[1m\u001b[97m${stripAnsi(line)}\u001b[0m`
    const prefix = line.slice(0, firstSpace + 1)
    const body = line.slice(firstSpace + 1)
    return `${prefix}\u001b[1m\u001b[97m${stripAnsi(body)}\u001b[0m`
  }
  return legacyAwareList.flatMap((entry, index) => {
    if (typeof entry === "string") {
      if (!includeLegacy) return []
      const normalized: ToolLogEntry = {
        id: `legacy-${index}`,
        kind: "status",
        text: entry,
        createdAt: index,
      }
      const lines = formatToolEntry(
        normalized,
        useColors,
        colorMode,
        icons,
        deltaGlyph,
        maxWidth,
        diffLineNumbers,
        renderProfile,
      )
      return lines.map(forceHeaderBright)
    }
    const normalized = entry
    const lines = formatToolEntry(
      normalized,
      useColors,
      colorMode,
      icons,
      deltaGlyph,
      maxWidth,
      diffLineNumbers,
      renderProfile,
    )
    return lines.map(forceHeaderBright)
  })
}

const formatTranscriptItem = (
  item: TranscriptItem,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  deltaGlyph: string,
  asciiOnly: boolean,
  collapsePredicate: (entry: ConversationEntry) => boolean,
  richMarkdownEnabled: boolean,
  maxWidth: number,
  diffLineNumbers: boolean,
  renderProfile: "default" | "claude" = "default",
): string[] => {
  if (item.kind === "message") {
    const entry: ConversationEntry = {
      id: item.id,
      speaker: item.speaker,
      text: item.text,
      phase: item.phase,
      createdAt: item.createdAt,
      richBlocks: item.richBlocks,
      markdownStreaming: item.markdownStreaming,
      markdownError: item.markdownError ?? null,
    }
    return formatConversationEntry(
      richMarkdownEnabled ? entry : { ...entry, richBlocks: undefined },
      useColors,
      colorMode,
      icons,
      deltaGlyph,
      asciiOnly,
      maxWidth,
      collapsePredicate,
    )
  }
  const asToolKind = (system: TranscriptSystemItem): ToolLogKind => {
    if (system.systemKind === "error") return "error"
    if (system.systemKind === "reward") return "reward"
    if (system.systemKind === "completion") return "completion"
    return "status"
  }
  const toolEntry: ToolLogEntry =
    item.kind === "tool"
      ? {
          id: item.id,
          kind: (item as TranscriptToolItem).toolKind,
          text: item.text,
          status: item.status,
          callId: item.callId ?? null,
          display: item.display ?? null,
          createdAt: item.createdAt,
        }
      : {
          id: item.id,
          kind: asToolKind(item as TranscriptSystemItem),
          text: item.text,
          status: item.status,
          createdAt: item.createdAt,
        }
  return formatToolEntry(toolEntry, useColors, colorMode, icons, deltaGlyph, maxWidth, diffLineNumbers, renderProfile)
}

const formatGuardrailBanner = (
  notice: GuardrailNotice | null | undefined,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
): string[] => {
  if (!notice) return []
  const prefixText = `${icons.error} Guardrail:`
  const prefix = useColors ? applyColor(prefixText, SEMANTIC_COLORS.error, useColors, colorMode) : prefixText
  const lines = [`${prefix} ${notice.summary}`]
  if (notice.detail) {
    if (notice.expanded) {
      lines.push(notice.detail)
    } else {
      const hint = useColors ? applyDim("Press e to expand for details", useColors, colorMode) : "Press e to expand for details"
      lines.push(hint)
    }
  }
  return lines
}

const formatLiveSlotLine = (slot: LiveSlotEntry, useColors: boolean, colorMode: ColorMode, icons: ReturnType<typeof resolveIcons>): string => {
  let glyph: string
  if (slot.status === "success") {
    glyph = useColors ? applyColor(icons.bullet, SEMANTIC_COLORS.success, useColors, colorMode) : icons.bullet
  } else if (slot.status === "error") {
    glyph = useColors ? applyColor(icons.bullet, SEMANTIC_COLORS.error, useColors, colorMode) : icons.error
  } else {
    glyph = useColors ? applyColor(icons.ellipsis, NEUTRAL_COLORS.midGray, useColors, colorMode) : icons.ellipsis
  }
  const text = useColors && slot.color ? applyColor(slot.text, slot.color, useColors, colorMode) : slot.text
  return `${glyph} ${text}`
}

const formatHints = (state: ReplState, useColors: boolean, colorMode: ColorMode, icons: ReturnType<typeof resolveIcons>): string[] => {
  return state.hints.slice(-4).map((hint) => {
    const bulletRaw = icons.bullet
    const bullet = useColors ? applyColor(bulletRaw, BRAND_COLORS.duneOrange, useColors, colorMode) : "-"
    return `${bullet} ${hint}`
  })
}

const formatModelMenu = (
  state: ReplState,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  star: string,
): string[] => {
  const menu = state.modelMenu
  if (menu.status === "hidden") return []
  const lines: string[] = ["", "=== Model Menu ==="]
  if (menu.status === "loading") {
    lines.push(`Loading model catalog${icons.ellipsis}`)
    return lines
  }
  if (menu.status === "error") {
    lines.push(`Error: ${menu.message}`)
    return lines
  }
  lines.push("Select a model (Enter to confirm, Esc to cancel)")
  menu.items.forEach((item) => {
    const left = item.isCurrent
      ? useColors
        ? applyColor(icons.bullet, SEMANTIC_COLORS.success, useColors, colorMode)
        : icons.bullet
      : " "
    const right = item.isDefault
      ? useColors
        ? applyColor(star, BRAND_COLORS.duneOrange, useColors, colorMode)
        : star
      : " "
    const detail = item.detail ? ` • ${item.detail}` : ""
    const label = `${left} ${item.label}${detail} ${right}`.trimEnd()
    lines.push(label)
  })
  return lines
}

export const renderStateToText = (state: ReplState, options: RenderTextOptions = {}): string => {
  const useColors = options.colors === true
  const colorMode = resolveColorMode(options.colorMode, useColors)
  const asciiOnly = resolveAsciiOnly(options.asciiOnly)
  const renderProfile: "default" | "claude" = options.renderProfile ?? "default"
  const baseIcons = resolveIcons(asciiOnly)
  const icons =
    renderProfile === "claude"
      ? {
          ...baseIcons,
          assistantDot: baseIcons.bullet,
        }
      : baseIcons
  const deltaGlyph = asciiOnly ? "d" : "Δ"
  const maxWidth = Math.max(40, options.maxWidth ?? 120)
  const star = asciiOnly ? "*" : "★"
  const hollowDot = asciiOnly ? "o" : "○"
  const includeHeader = options.includeHeader !== false
  const includeStatus = options.includeStatus !== false
  const header = formatHeader(useColors, colorMode)
  const lines: string[] = []
  if (includeHeader) {
    lines.push(...header)
  }
  if (includeStatus) {
    const title = "breadboard — interactive session"
    lines.push(useColors ? applyColor(title, SEMANTIC_COLORS.info, useColors, colorMode) : title)
    lines.push(formatStatusLine(state, useColors, colorMode, icons, hollowDot))
    lines.push("")
  } else if (includeHeader) {
    lines.push("")
  }
  const includeTools = state.viewPrefs?.toolRail !== false || state.viewPrefs?.rawStream === true
  const transcript = buildTranscript(
    {
      conversation: state.conversation,
      toolEvents: includeTools ? state.toolEvents : [],
      rawEvents: state.rawEvents ?? [],
    },
    { includeRawEvents: state.viewPrefs?.rawStream === true, pendingToolsInTail: true },
  )
  const collapseMode = state.viewPrefs?.collapseMode ?? "auto"
  const virtualizationMode = state.viewPrefs?.virtualization ?? "auto"
  const richMarkdownEnabled = state.viewPrefs?.richMarkdown === true
  const diffLineNumbers =
    state.viewPrefs?.diffLineNumbers === true ||
    ["1", "true", "yes", "on"].includes((process.env.BREADBOARD_DIFF_LINE_NUMBERS ?? "").toLowerCase())
  const includeStreamingTail = options.includeStreamingTail !== false
  const collapsePredicate = (entry: ConversationEntry): boolean => {
    if (collapseMode === "none") return false
    if (collapseMode === "all") return entry.speaker === "assistant"
    return shouldAutoCollapseEntry(entry)
  }
  const transcriptCapacity =
    virtualizationMode === "compact" ? Math.max(MIN_TRANSCRIPT_ROWS, 40) : MAX_TRANSCRIPT_ENTRIES
  const conversationWindow = buildTranscriptWindow(transcript.committed, transcriptCapacity)
  const virtualizationActive = transcriptCapacity < MAX_TRANSCRIPT_ENTRIES
  if (conversationWindow.truncated) {
    lines.push(
      `${icons.ellipsis} ${conversationWindow.hiddenCount} earlier ${
        conversationWindow.hiddenCount === 1 ? "entry" : "entries"
      } hidden ${icons.ellipsis}`,
    )
  }
  if (virtualizationActive) {
    const hiddenText = conversationWindow.hiddenCount > 0 ? ` (${conversationWindow.hiddenCount} hidden)` : ""
    lines.push(
      `Compact transcript mode active — window ${transcriptCapacity} rows, showing last ${conversationWindow.entries.length} entries${hiddenText}. Use /view scroll auto to expand.`,
    )
  }
  const showHelpBanner =
    conversationWindow.entries.length === 0 &&
    (!includeStreamingTail || transcript.tail.length === 0)
  if (showHelpBanner) {
    lines.push(
      "Slash commands: /help, /quit, /clear, /status, /remote on|off, /retry, /plan, /mode <plan|build|auto>, /model <id>, /test [suite], /files [path], /models, /skills",
    )
    lines.push("! for bash • / for commands • @ for files • Tab to complete • Esc closes menus")
  }
  lines.push(...formatGuardrailBanner(state.guardrailNotice ?? null, useColors, colorMode, icons))
  if (conversationWindow.entries.length > 0) {
    if (lines.length > 0) lines.push("")
    conversationWindow.entries.forEach((entry, index) => {
      if (index > 0) lines.push("")
      lines.push(
        ...formatTranscriptItem(
          entry as TranscriptItem,
          useColors,
          colorMode,
          icons,
          deltaGlyph,
          asciiOnly,
          collapsePredicate,
          richMarkdownEnabled,
          maxWidth,
          diffLineNumbers,
          renderProfile,
        ),
      )
    })
  }
  if (includeStreamingTail && transcript.tail.length > 0) {
    if (lines.length > 0) lines.push("")
    transcript.tail.forEach((entry, index) => {
      if (index > 0) lines.push("")
      lines.push(
        ...formatTranscriptItem(
          entry,
          useColors,
          colorMode,
          icons,
          deltaGlyph,
          asciiOnly,
          () => false,
          richMarkdownEnabled,
          maxWidth,
          diffLineNumbers,
          renderProfile,
        ),
      )
    })
  }
  if (conversationWindow.entries.length === 0 && (transcript.tail.length === 0 || !includeStreamingTail)) {
    lines.push(
      state.pendingResponse
        ? `Assistant is thinking${icons.ellipsis}`
        : "No conversation yet. Type a prompt to get started.",
    )
  }
  if (renderProfile !== "claude" && state.liveSlots?.length) {
    for (const slot of state.liveSlots) {
      lines.push(formatLiveSlotLine(slot, useColors, colorMode, icons))
    }
  }
  if (options.includeHints !== false) {
    const hints = formatHints(state, useColors, colorMode, icons)
    if (hints.length > 0) {
      lines.push(...hints)
    }
  }
  if (options.includeModelMenu !== false) {
    const menu = formatModelMenu(state, useColors, colorMode, icons, star)
    if (menu.length > 0) {
      lines.push(...menu)
    }
  }
  return lines.join("\n")
}
