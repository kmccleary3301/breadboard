import chalk from "chalk"
import type { ConversationEntry, LiveSlotEntry, GuardrailNotice, ToolLogEntry } from "../../repl/types.js"
import type { ReplState } from "./controller.js"
import { ASCII_HEADER, speakerColor, TOOL_EVENT_COLOR } from "../../repl/viewUtils.js"
import { applyForegroundGradient, Gradients } from "../../colors/gradients.js"
import type { Block, InlineNode } from "@stream-mdx/core/types"
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
  readonly includeHints?: boolean
  readonly includeModelMenu?: boolean
  readonly includeHeader?: boolean
  readonly includeStatus?: boolean
  readonly includeStreamingTail?: boolean
}

const formatHeader = (useColors: boolean): string[] => {
  if (!useColors) {
    return [...ASCII_HEADER]
  }
  return ASCII_HEADER.map((line: string) => applyForegroundGradient(line, Gradients.crush, true))
}

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

const formatStatusLine = (state: ReplState, useColors: boolean): string => {
  const { sessionId, status, pendingResponse, stats } = state
  const glyph = pendingResponse ? (useColors ? chalk.cyan("●") : "*") : useColors ? chalk.hex("#7CF2FF")("●") : "●"
  const modelGlyph = useColors ? chalk.hex("#B36BFF")("●") : "●"
  const remoteGlyph = stats.remote ? (useColors ? chalk.hex("#7CF2FF")("●") : "●") : useColors ? chalk.hex("#475569")("○") : "○"
  const toolsGlyph = stats.toolCount > 0 ? (useColors ? chalk.hex(TOOL_EVENT_COLOR)("●") : "●") : useColors ? chalk.hex("#475569")("○") : "○"
  const eventsGlyph = stats.eventCount > 0 ? (useColors ? chalk.hex("#A855F7")("●") : "●") : useColors ? chalk.hex("#475569")("○") : "○"
  const turnGlyph =
    stats.lastTurn != null ? (useColors ? chalk.hex("#34D399")("●") : "●") : useColors ? chalk.hex("#475569")("○") : "○"

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
      parts.push(useColors ? chalk.dim(usageText) : usageText)
    }
  }
  return parts.join(" ")
}

const colorizeDiffLine = (line: string, useColors: boolean): string => {
  if (!useColors) return line
  if (line.startsWith("diff --git") || line.startsWith("index ")) return chalk.hex("#7CF2FF")(line)
  if (line.startsWith("@@")) return chalk.hex("#FBBF24")(line)
  if (line.startsWith("---") || line.startsWith("+++")) return chalk.hex("#C4B5FD")(line)
  if (line.startsWith("+") && !line.startsWith("+++")) return chalk.hex("#34D399")(line)
  if (line.startsWith("-") && !line.startsWith("---")) return chalk.hex("#FB7185")(line)
  return line
}

const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")

const renderInlineNodes = (nodes: ReadonlyArray<InlineNode> | undefined, useColors: boolean): string => {
  if (!nodes) return ""
  const render = (node: InlineNode): string => {
    switch (node.kind) {
      case "text":
        return node.text
      case "strong":
        return (useColors ? chalk.bold : (s: string) => s)(renderInlineNodes(node.children, useColors))
      case "em":
        return (useColors ? chalk.italic : (s: string) => s)(renderInlineNodes(node.children, useColors))
      case "strike":
        return (useColors ? chalk.strikethrough : (s: string) => s)(renderInlineNodes(node.children, useColors))
      case "code":
        return useColors ? chalk.bgHex("#1f2937").hex("#e5e7eb")(` ${node.text} `) : node.text
      case "link":
        return `${renderInlineNodes(node.children, useColors)}${node.href ? (useColors ? chalk.underline(node.href) : ` (${node.href})`) : ""}`
      case "mention":
        return useColors ? chalk.hex("#7CF2FF")(`@${node.handle}`) : `@${node.handle}`
      case "citation":
        return useColors ? chalk.hex("#93c5fd")(`[${node.id}]`) : `[${node.id}]`
      case "math-inline":
      case "math-display":
        return useColors ? chalk.hex("#c7d2fe")(node.tex) : node.tex
      case "footnote-ref":
        return useColors ? chalk.hex("#fbbf24")(`[^${node.label}]`) : `[^${node.label}]`
      case "image":
        return node.alt ? `![${node.alt}]` : "[image]"
      case "br":
        return "\n"
      default:
        return "children" in node ? renderInlineNodes((node as { children?: InlineNode[] }).children, useColors) : ""
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

const renderCodeLines = (raw: string, lang: string | undefined, useColors: boolean): string[] => {
  const { code, langHint } = stripFence(raw)
  const finalLang = (lang ?? langHint ?? "").toLowerCase()
  const lines = (code || raw).replace(/\r\n?/g, "\n").split("\n")
  const isDiff = finalLang.includes("diff")
  return lines.map((line: string) => {
    if (!useColors) return line
    if (isDiff) return colorizeDiffLine(line, true)
    if (line.startsWith("+")) return chalk.hex("#34d399")(line)
    if (line.startsWith("-")) return chalk.hex("#fb7185")(line)
    if (line.startsWith("@@")) return chalk.hex("#22d3ee")(line)
    return chalk.hex("#cbd5e1")(line)
  })
}

const blockToLines = (block: Block, useColors: boolean): string[] => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  switch (block.type) {
    case "heading": {
      const levelRaw = typeof meta.level === "number" ? meta.level : typeof meta.depth === "number" ? meta.depth : 1
      const level = Math.min(6, Math.max(1, levelRaw || 1))
      const prefix = "#".repeat(level)
      const text = block.payload.inline ? renderInlineNodes(block.payload.inline, useColors) : block.payload.raw ?? ""
      return [useColors ? chalk.bold.hex("#f97316")(`${prefix} ${text}`) : `${prefix} ${text}`]
    }
    case "blockquote": {
      const content = block.payload.inline ? renderInlineNodes(block.payload.inline, useColors) : block.payload.raw ?? ""
      return content.split(/\r?\n/).map((line: string) => (useColors ? chalk.hex("#9ca3af")(`> ${line}`) : `> ${line}`))
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
      return renderCodeLines(raw, lang, useColors)
    }
    case "paragraph": {
      const value = block.payload.inline ? renderInlineNodes(block.payload.inline, useColors) : block.payload.raw ?? ""
      return value.split(/\r?\n/)
    }
    default: {
      const raw = block.payload.raw ?? ""
      return raw.split(/\r?\n/)
    }
  }
}

const blocksToLines = (blocks: ReadonlyArray<Block> | undefined, useColors: boolean): { lines: string[]; plain: string[] } => {
  if (!blocks || blocks.length === 0) return { lines: [], plain: [] }
  const lines: string[] = []
  for (const block of blocks) {
    const rendered = blockToLines(block, useColors)
    if (rendered.length > 0) lines.push(...rendered)
  }
  return { lines, plain: lines.map(stripAnsi) }
}

const formatConversationEntry = (
  entry: ConversationEntry,
  useColors: boolean,
  collapsePredicate?: (entry: ConversationEntry) => boolean,
): string[] => {
  const label = entry.speaker.toUpperCase().padEnd(9, " ")
  const coloredLabel = useColors ? chalk.hex(speakerColor(entry.speaker))(label) : label
  const pad = useColors ? chalk.dim(" ".repeat(9)) : " ".repeat(9)
  const useRich = Boolean(entry.richBlocks && entry.richBlocks.length > 0 && !entry.markdownError)
  const rich = useRich ? blocksToLines(entry.richBlocks, useColors) : { lines: entry.text.split(/\r?\n/), plain: entry.text.split(/\r?\n/) }
  const lines = rich.lines
  const plain = rich.plain
  const errorLine =
    entry.markdownError && entry.speaker === "assistant"
      ? `${coloredLabel} rich markdown disabled: ${entry.markdownError}`
      : null
  const asLine = (text: string, index: number) =>
    `${index === 0 ? coloredLabel : pad} ${colorizeDiffLine(text, useColors)}`
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
  const collapseLabel = useColors ? chalk.dim("…") : "…"
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

const formatToolEntry = (entry: ToolLogEntry, useColors: boolean): string[] => {
  const lines = entry.text.split(/\r?\n/)
  const glyph =
    entry.status === "success"
      ? useColors
        ? chalk.hex("#34D399")("●")
        : "●"
      : entry.status === "error"
        ? useColors
          ? chalk.hex("#F87171")("●")
          : "✖"
        : entry.kind === "call"
          ? useColors
            ? chalk.hex("#7CF2FF")("●")
            : "●"
          : useColors
            ? chalk.hex(TOOL_EVENT_COLOR)("●")
            : "●"
  const label = `[${entry.kind}]`.padEnd(TOOL_LABEL_WIDTH, " ")
  const labelColored = useColors ? chalk.dim(label) : label
  const padLabel = useColors ? chalk.dim(" ".repeat(TOOL_LABEL_WIDTH)) : " ".repeat(TOOL_LABEL_WIDTH)
  const prefixFirst = `${glyph} ${labelColored}`
  const prefixNext = `${" ".repeat(2)}${padLabel}`
  const renderLine = (line: string, index: number) => `${index === 0 ? prefixFirst : prefixNext} ${colorizeDiffLine(line, useColors)}`
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
  const collapseLabel = useColors ? chalk.dim("…") : "…"
  return [
    ...head.map((line: string, index: number) => renderLine(line, index)),
    `${prefixNext} ${collapseLabel} ${summaryParts.join(" — ")}`,
    ...tail.map((line: string, index: number) => renderLine(line, head.length + index + 1)),
  ]
}

const formatTools = (state: ReplState, useColors: boolean): string[] => {
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
    return formatToolEntry(normalized, useColors)
  })
}

const formatGuardrailBanner = (notice: GuardrailNotice | null | undefined, useColors: boolean): string[] => {
  if (!notice) return []
  const prefix = useColors ? chalk.hex("#fb7185")("! Guardrail:") : "! Guardrail:"
  const lines = [`${prefix} ${notice.summary}`]
  if (notice.detail) {
    if (notice.expanded) {
      lines.push(notice.detail)
    } else {
      const hint = useColors ? chalk.dim("Press e to expand for details") : "Press e to expand for details"
      lines.push(hint)
    }
  }
  return lines
}

const formatLiveSlotLine = (slot: LiveSlotEntry, useColors: boolean): string => {
  let glyph: string
  if (slot.status === "success") {
    glyph = useColors ? chalk.hex("#34D399")("●") : "●"
  } else if (slot.status === "error") {
    glyph = useColors ? chalk.hex("#F87171")("●") : "!"
  } else {
    glyph = useColors ? chalk.hex("#94A3B8")("…") : "…"
  }
  const text = useColors && slot.color ? chalk.hex(slot.color)(slot.text) : slot.text
  return `${glyph} ${text}`
}

const formatHints = (state: ReplState, useColors: boolean): string[] => {
  return state.hints.slice(-4).map((hint) => {
    const bullet = useColors ? chalk.yellow("•") : "-"
    return `${bullet} ${hint}`
  })
}

const formatModelMenu = (state: ReplState, useColors: boolean): string[] => {
  const menu = state.modelMenu
  if (menu.status === "hidden") return []
  const lines: string[] = ["", "=== Model Menu ==="]
  if (menu.status === "loading") {
    lines.push("Loading model catalog…")
    return lines
  }
  if (menu.status === "error") {
    lines.push(`Error: ${menu.message}`)
    return lines
  }
  lines.push("Select a model (Enter to confirm, Esc to cancel)")
  menu.items.forEach((item) => {
    const left = item.isCurrent ? (useColors ? chalk.hex("#34D399")("●") : "●") : " "
    const right = item.isDefault ? (useColors ? chalk.hex("#FBBF24")("★") : "★") : " "
    const detail = item.detail ? ` • ${item.detail}` : ""
    const label = `${left} ${item.label}${detail} ${right}`.trimEnd()
    lines.push(label)
  })
  return lines
}

export const renderStateToText = (state: ReplState, options: RenderTextOptions = {}): string => {
  const useColors = options.colors === true
  const includeHeader = options.includeHeader !== false
  const includeStatus = options.includeStatus !== false
  const header = formatHeader(useColors)
  const lines: string[] = []
  if (includeHeader) {
    lines.push(...header)
  }
  if (includeStatus) {
    lines.push(useColors ? chalk.cyan("breadboard — interactive session") : "breadboard — interactive session")
    lines.push(formatStatusLine(state, useColors))
    lines.push("")
  } else if (includeHeader) {
    lines.push("")
  }
  lines.push("Slash commands: /help, /quit, /clear, /status, /remote on|off, /retry, /plan, /mode <plan|build|auto>, /model <id>, /test [suite], /files [path], /models, /skills")
  lines.push("! for bash • / for commands • @ for files • Tab to complete • Esc closes menus")
  lines.push(...formatGuardrailBanner(state.guardrailNotice ?? null, useColors))
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
      `… ${conversationWindow.hiddenCount} earlier ${
        conversationWindow.hiddenCount === 1 ? "message" : "messages"
      } hidden …`,
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
        () => false,
      ),
    )
  }
  if (conversationWindow.entries.length === 0 && (!streaming || !includeStreamingTail)) {
    lines.push(state.pendingResponse ? "Assistant is thinking…" : "No conversation yet. Type a prompt to get started.")
  }
  const tools = formatTools(state, useColors)
  if (tools.length > 0) {
    lines.push(...tools)
  }
  if (state.liveSlots?.length) {
    for (const slot of state.liveSlots) {
      lines.push(formatLiveSlotLine(slot, useColors))
    }
  }
  if (options.includeHints !== false) {
    const hints = formatHints(state, useColors)
    if (hints.length > 0) {
      lines.push(...hints)
    }
  }
  if (options.includeModelMenu !== false) {
    const menu = formatModelMenu(state, useColors)
    if (menu.length > 0) {
      lines.push(...menu)
    }
  }
  return lines.join("\n")
}
