import chalk from "chalk"
import type { ConversationEntry, LiveSlotEntry, GuardrailNotice, ToolLogEntry } from "../../repl/types.js"
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
import {
  buildConversationWindow,
  computeDiffPreview,
  ENTRY_COLLAPSE_HEAD,
  ENTRY_COLLAPSE_TAIL,
  MAX_TRANSCRIPT_ENTRIES,
  MIN_TRANSCRIPT_ROWS,
  shouldAutoCollapseEntry,
} from "../../repl/transcriptUtils.js"

export interface RenderTextOptions {
  readonly colors?: boolean
  readonly colorMode?: ColorMode
  readonly asciiOnly?: boolean
  readonly includeHints?: boolean
  readonly includeModelMenu?: boolean
  readonly includeHeader?: boolean
  readonly includeStatus?: boolean
  readonly includeStreamingTail?: boolean
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

const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")

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

const blockToLines = (block: Block, useColors: boolean, colorMode: ColorMode): string[] => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  switch (block.type) {
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
      const raw = block.payload.raw ?? ""
      return renderCodeLines(raw, lang, useColors, colorMode)
    }
    case "paragraph": {
      const value = block.payload.inline
        ? renderInlineNodes(block.payload.inline, useColors, colorMode)
        : block.payload.raw ?? ""
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
): { lines: string[]; plain: string[] } => {
  if (!blocks || blocks.length === 0) return { lines: [], plain: [] }
  const lines: string[] = []
  for (const block of blocks) {
    const rendered = blockToLines(block, useColors, colorMode)
    if (rendered.length > 0) lines.push(...rendered)
  }
  return { lines, plain: lines.map(stripAnsi) }
}

const formatConversationEntry = (
  entry: ConversationEntry,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
  collapsePredicate?: (entry: ConversationEntry) => boolean,
): string[] => {
  const label = entry.speaker.toUpperCase().padEnd(9, " ")
  const coloredLabel = useColors ? applyColor(label, speakerColor(entry.speaker), useColors, colorMode) : label
  const pad = useColors ? applyDim(" ".repeat(9), useColors, colorMode) : " ".repeat(9)
  const useRich = Boolean(entry.richBlocks && entry.richBlocks.length > 0 && !entry.markdownError)
  const rich = useRich
    ? blocksToLines(entry.richBlocks, useColors, colorMode)
    : { lines: entry.text.split(/\r?\n/), plain: entry.text.split(/\r?\n/) }
  const lines = rich.lines
  const plain = rich.plain
  const errorLine =
    entry.markdownError && entry.speaker === "assistant"
      ? `${coloredLabel} rich markdown disabled: ${entry.markdownError}`
      : null
  const asLine = (text: string, index: number) =>
    `${index === 0 ? coloredLabel : pad} ${colorizeDiffLine(text, useColors, colorMode)}`
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
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
    diffPreview ? `Δ +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
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

const TOOL_LABEL_WIDTH = 12
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
): string[] => {
  const lines = formatStatusLines(entry.text)
  const safeLines = lines.length > 0 ? lines : [entry.text]
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
    if (entry.status === "error") return applyColor(line, SEMANTIC_COLORS.error, useColors, colorMode)
    if (entry.status === "pending") return applyColor(line, SEMANTIC_COLORS.warning, useColors, colorMode)
    if (entry.status === "success") return applyDim(line, useColors, colorMode)
    return applyDim(line, useColors, colorMode)
  }
  const prefixFirst = `${glyph} `
  const prefixNext = "  "
  const renderLine = (line: string, index: number) =>
    `${index === 0 ? prefixFirst : prefixNext}${colorizeStatusLine(line)}`
  if (safeLines.length <= TOOL_COLLAPSE_THRESHOLD) {
    return safeLines.map((line, index) => renderLine(line, index))
  }
  const head = safeLines.slice(0, TOOL_COLLAPSE_HEAD)
  const tail = safeLines.slice(-TOOL_COLLAPSE_TAIL)
  const hiddenCount = safeLines.length - head.length - tail.length
  const diffPreview = computeDiffPreview(safeLines)
  const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
  const summaryParts = [
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
    diffPreview ? `Δ +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
  ].filter(Boolean)
  const collapseLabel = useColors ? applyDim(icons.ellipsis, useColors, colorMode) : icons.ellipsis
  return [
    ...head.map((line, index) => renderLine(line, index)),
    `${prefixNext}${collapseLabel} ${summaryParts.join(" — ")}`,
    ...tail.map((line, index) => renderLine(line, head.length + index + 1)),
  ]
}

const formatToolEntry = (
  entry: ToolLogEntry,
  useColors: boolean,
  colorMode: ColorMode,
  icons: ReturnType<typeof resolveIcons>,
): string[] => {
  if (entry.kind === "status") {
    return formatStatusEntry(entry, useColors, colorMode, icons)
  }
  const lines = entry.text.split(/\r?\n/)
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
  const label = `[${entry.kind}]`.padEnd(TOOL_LABEL_WIDTH, " ")
  const labelColored = useColors ? applyDim(label, useColors, colorMode) : label
  const padLabel = useColors ? applyDim(" ".repeat(TOOL_LABEL_WIDTH), useColors, colorMode) : " ".repeat(TOOL_LABEL_WIDTH)
  const prefixFirst = `${glyph} ${labelColored}`
  const prefixNext = `${" ".repeat(2)}${padLabel}`
  const renderLine = (line: string, index: number) =>
    `${index === 0 ? prefixFirst : prefixNext} ${colorizeDiffLine(line, useColors, colorMode)}`
  if (lines.length <= TOOL_COLLAPSE_THRESHOLD) {
    return lines.map((line: string, index: number) => renderLine(line, index))
  }
  const head = lines.slice(0, TOOL_COLLAPSE_HEAD)
  const tail = lines.slice(-TOOL_COLLAPSE_TAIL)
  const hiddenCount = lines.length - head.length - tail.length
  const diffPreview = computeDiffPreview(lines)
  const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
  const summaryParts = [
    `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
    diffPreview ? `Δ +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
  ].filter(Boolean)
  const collapseLabel = useColors ? applyDim(icons.ellipsis, useColors, colorMode) : icons.ellipsis
  return [
    ...head.map((line: string, index: number) => renderLine(line, index)),
    `${prefixNext} ${collapseLabel} ${summaryParts.join(" — ")}`,
    ...tail.map((line: string, index: number) => renderLine(line, head.length + index + 1)),
  ]
}

const formatTools = (state: ReplState, useColors: boolean, colorMode: ColorMode, icons: ReturnType<typeof resolveIcons>): string[] => {
  const legacyAwareList = state.toolEvents as Array<ToolLogEntry | string>
  return legacyAwareList.flatMap((entry, index) => {
    const normalized: ToolLogEntry =
      typeof entry === "string"
        ? {
            id: `legacy-${index}`,
            kind: "status",
            text: entry,
            createdAt: index,
          }
        : entry
    return formatToolEntry(normalized, useColors, colorMode, icons)
  })
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
  const icons = resolveIcons(asciiOnly)
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
  lines.push("Slash commands: /help, /quit, /clear, /status, /remote on|off, /retry, /plan, /mode <plan|build|auto>, /model <id>, /test [suite], /files [path], /models, /skills")
  lines.push("! for bash • / for commands • @ for files • Tab to complete • Esc closes menus")
  lines.push(...formatGuardrailBanner(state.guardrailNotice ?? null, useColors, colorMode, icons))
  const { finals, streaming } = partitionConversation(state.conversation)
  const collapseMode = state.viewPrefs?.collapseMode ?? "auto"
  const virtualizationMode = state.viewPrefs?.virtualization ?? "auto"
  const richMarkdownEnabled = state.viewPrefs?.richMarkdown === true
  const collapsePredicate = (entry: ConversationEntry): boolean => {
    if (collapseMode === "none") return false
    if (collapseMode === "all") return entry.speaker === "assistant"
    return shouldAutoCollapseEntry(entry)
  }
  const transcriptCapacity =
    virtualizationMode === "compact" ? Math.max(MIN_TRANSCRIPT_ROWS, 40) : MAX_TRANSCRIPT_ENTRIES
  const conversationWindow = buildConversationWindow(finals, transcriptCapacity)
  const virtualizationActive = transcriptCapacity < MAX_TRANSCRIPT_ENTRIES
  if (conversationWindow.truncated) {
    lines.push(
      `${icons.ellipsis} ${conversationWindow.hiddenCount} earlier ${
        conversationWindow.hiddenCount === 1 ? "message" : "messages"
      } hidden ${icons.ellipsis}`,
    )
  }
  if (virtualizationActive) {
    const hiddenText = conversationWindow.hiddenCount > 0 ? ` (${conversationWindow.hiddenCount} hidden)` : ""
    lines.push(
      `Compact transcript mode active — window ${transcriptCapacity} rows, showing last ${conversationWindow.entries.length} messages${hiddenText}. Use /view scroll auto to expand.`,
    )
  }
  if (conversationWindow.entries.length > 0) {
    for (const entry of conversationWindow.entries) {
      lines.push(
        ...formatConversationEntry(
          richMarkdownEnabled ? entry : { ...entry, richBlocks: undefined },
          useColors,
          colorMode,
          icons,
          collapsePredicate,
        ),
      )
    }
  }
  const includeStreamingTail = options.includeStreamingTail !== false
  if (includeStreamingTail && streaming) {
    lines.push(
      ...formatConversationEntry(
        richMarkdownEnabled ? streaming : { ...streaming, richBlocks: undefined },
        useColors,
        colorMode,
        icons,
        () => false,
      ),
    )
  }
  if (conversationWindow.entries.length === 0 && (!streaming || !includeStreamingTail)) {
    lines.push(
      state.pendingResponse
        ? `Assistant is thinking${icons.ellipsis}`
        : "No conversation yet. Type a prompt to get started.",
    )
  }
  const showToolRail = state.viewPrefs?.toolRail !== false || state.viewPrefs?.rawStream === true
  if (showToolRail) {
    const toolEvents =
      state.viewPrefs?.rawStream === true
        ? [...state.toolEvents, ...(state.rawEvents ?? [])]
        : state.toolEvents
    const tools = formatTools({ ...state, toolEvents }, useColors, colorMode, icons)
    if (tools.length > 0) {
      lines.push(...tools)
    }
  }
  if (state.liveSlots?.length) {
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
