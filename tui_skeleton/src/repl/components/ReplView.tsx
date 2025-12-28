import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Box, Static, Text, useStdout } from "ink"
import chalk from "chalk"
import path from "node:path"
import type { Block, InlineNode } from "@stream-mdx/core/types"
import type {
  ConversationEntry,
  LiveSlotEntry,
  StreamStats,
  ModelMenuState,
  ModelMenuItem,
  GuardrailNotice,
  QueuedAttachment,
  TranscriptPreferences,
  ToolLogEntry,
  TodoItem,
  PermissionRequest,
  PermissionDecision,
  PermissionRuleScope,
  RewindMenuState,
  CheckpointSummary,
} from "../types.js"
import { useSpinner } from "../hooks/useSpinner.js"
import { SLASH_COMMANDS, buildSuggestions, SLASH_COMMAND_HINT } from "../slashCommands.js"
import type { SlashCommandInfo, SlashSuggestion } from "../slashCommands.js"
import { applyForegroundGradient, Gradients } from "../../colors/gradients.js"
import { ASCII_HEADER, speakerColor, TOOL_EVENT_COLOR } from "../viewUtils.js"
import { ModalHost, type ModalDescriptor } from "./ModalHost.js"
import { useKeyRouter, type KeyHandler, type LayerName } from "../hooks/useKeyRouter.js"
import { LineEditor } from "./LineEditor.js"
import { LiveSlot } from "./LiveSlot.js"
import { TranscriptViewer } from "./TranscriptViewer.js"
import { useAnimationClock } from "../hooks/useAnimationClock.js"
import type { ClipboardImage } from "../../util/clipboard.js"
import type { SessionFileInfo, SessionFileContent } from "../../api/types.js"
import { loadFileMentionConfig, type FileMentionMode } from "../fileMentions.js"
import { loadFilePickerConfig, loadFilePickerResources, type FilePickerMode, type FilePickerResource } from "../filePicker.js"
import { computeModelColumns, CONTEXT_COLUMN_WIDTH, PRICE_COLUMN_WIDTH } from "../modelMenu/layout.js"
import { GuardrailBanner } from "./GuardrailBanner.js"
import { loadKeymapConfig } from "../keymap.js"
import { loadChromeMode } from "../chrome.js"
import {
  buildConversationWindow,
  computeDiffPreview,
  ENTRY_COLLAPSE_HEAD,
  ENTRY_COLLAPSE_TAIL,
  MAX_TRANSCRIPT_ENTRIES,
  MIN_TRANSCRIPT_ROWS,
  shouldAutoCollapseEntry,
} from "../transcriptUtils.js"

const MAX_SUGGESTIONS = SLASH_COMMANDS.length
const META_LINE_COUNT = 2
const COMPOSER_MIN_ROWS = 6
const TOOL_COLLAPSE_THRESHOLD = 24
const TOOL_COLLAPSE_HEAD = 6
const TOOL_COLLAPSE_TAIL = 6
const TOOL_LABEL_WIDTH = 12
const LABEL_WIDTH = 9
const SCROLLBACK_MODE = true
const MODEL_PROVIDER_ORDER = [
  "openai",
  "anthropic",
  "google",
  "openrouter",
  "xai",
  "mistral",
  "meta",
  "cohere",
  "deepseek",
  "local",
  "other",
]
const DOUBLE_CTRL_C_WINDOW_MS = 1500
const CLI_VERSION = (process.env.BREADBOARD_TUI_VERSION ?? "0.2.0").trim()
const formatBytes = (bytes: number): string => {
  if (bytes < 1_000) return `${bytes} B`
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)} KB`
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`
  return `${(bytes / 1_000_000_000).toFixed(2)} GB`
}
const COLUMN_SEPARATOR = "  ·  "

const formatCell = (value: string, width: number, align: "left" | "right" = "left"): string => {
  if (width <= 0) return ""
  let output = value
  if (output.length > width) {
    output = width === 1 ? "…" : `${output.slice(0, width - 1)}…`
  }
  if (align === "right") return output.padStart(width, " ")
  return output.padEnd(width, " ")
}

const findFuzzyMatchIndices = (text: string, query: string): number[] | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  const haystack = text.toLowerCase()
  const indices: number[] = []
  let lastIndex = -1
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    const idx = haystack.indexOf(ch, lastIndex + 1)
    if (idx === -1) return null
    indices.push(idx)
    lastIndex = idx
  }
  return indices
}

const highlightFuzzyLabel = (label: string, command: string, query: string): string => {
  if (!query.trim()) return label
  const matches = findFuzzyMatchIndices(command, query)
  if (!matches || matches.length === 0) return label
  const matchSet = new Set(matches)
  let out = ""
  for (let i = 0; i < label.length; i += 1) {
    const ch = label[i]
    if (i < command.length && matchSet.has(i)) {
      out += chalk.hex("#7CF2FF")(ch)
    } else {
      out += ch
    }
  }
  return out
}

const formatProviderCell = (item: ModelMenuItem, width: number): string => {
  const currentGlyph = item.isCurrent ? "● " : "  "
  const defaultGlyph = item.isDefault ? " ★" : ""
  return formatCell(`${currentGlyph}${item.label}${defaultGlyph}`, width, "left")
}

const formatContextCell = (contextTokens: number | null | undefined, width: number): string => {
  if (contextTokens == null) return formatCell("—", width, "right")
  const contextK = Math.max(1, Math.round(contextTokens / 1000))
  return formatCell(`${contextK}k`, width, "right")
}

const formatPriceCell = (price: number | null | undefined, width: number): string => {
  if (price == null) return formatCell("—", width, "right")
  return formatCell(`$${price.toFixed(2)}`, width, "right")
}

const buildModelRowText = (cells: string[]): string => cells.filter((cell) => cell.length > 0).join(COLUMN_SEPARATOR)

const stripAnsiCodes = (value: string): string => value.replace(/\u001B\[[0-9;]*m/g, "")

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

const formatInlineNodes = (nodes?: ReadonlyArray<InlineNode>): string => {
  if (!nodes || nodes.length === 0) return ""
  const render = (node: InlineNode): string => {
    switch (node.kind) {
      case "text":
        return node.text
      case "strong":
        return chalk.bold(formatInlineNodes(node.children))
      case "em":
        return chalk.italic(formatInlineNodes(node.children))
      case "strike":
        return chalk.strikethrough(formatInlineNodes(node.children))
      case "code":
        return chalk.bgHex("#1f2937").hex("#e5e7eb")(` ${node.text} `)
      case "link":
        return `${chalk.underline(formatInlineNodes(node.children))}${node.href ? chalk.dim(` (${node.href})`) : ""}`
      case "mention":
        return chalk.hex("#7CF2FF")(`@${node.handle}`)
      case "citation":
        return chalk.hex("#93c5fd")(`[${node.id}]`)
      case "math-inline":
      case "math-display":
        return chalk.hex("#c7d2fe")(node.tex)
      case "footnote-ref":
        return chalk.hex("#fbbf24")(`[^${node.label}]`)
      case "image":
        return node.alt ? `![${node.alt}]` : "[image]"
      case "br":
        return "\n"
      default:
        return "children" in node ? formatInlineNodes((node as { children?: InlineNode[] }).children) : ""
    }
  }
  return nodes.map(render).join("")
}

const colorDiffLine = (line: string): string => {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return chalk.hex("#c4b5fd")(line)
  if (line.startsWith("@@")) return chalk.hex("#22d3ee")(line)
  if (line.startsWith("+")) return chalk.bgHex("#0b3b2e").hex("#34d399")(line)
  if (line.startsWith("-")) return chalk.bgHex("#3b0b14").hex("#fb7185")(line)
  return line
}

const renderCodeLines = (raw: string, lang?: string): string[] => {
  const { code, langHint } = stripFence(raw)
  const finalLang = lang ?? langHint
  const lines = (code || raw).replace(/\r\n?/g, "\n").split("\n")
  const isDiff = finalLang ? finalLang.toLowerCase().includes("diff") : false
  return lines.map((line) => {
    if (isDiff) return colorDiffLine(line)
    if (line.startsWith("+")) return chalk.hex("#34d399")(line)
    if (line.startsWith("-")) return chalk.hex("#fb7185")(line)
    if (line.startsWith("@@")) return chalk.hex("#22d3ee")(line)
    return chalk.hex("#cbd5e1")(line)
  })
}

interface DiffSection {
  readonly file: string
  readonly lines: ReadonlyArray<string>
}

const DIFF_SECTION_PATTERN = /^diff --git a\/(.+?) b\/(.+)$/
const DIFF_FALLBACK_NEW = "+++ b/"
const DIFF_FALLBACK_OLD = "--- a/"

const splitUnifiedDiff = (diffText: string): DiffSection[] => {
  const normalized = diffText.replace(/\r\n?/g, "\n").trimEnd()
  if (!normalized) return []
  const lines = normalized.split("\n")
  const sections: DiffSection[] = []
  let current: { file: string; lines: string[] } | null = null

  const pushCurrent = () => {
    if (!current) return
    if (current.lines.some((line) => line.trim().length > 0)) {
      sections.push({ file: current.file, lines: [...current.lines] })
    }
    current = null
  }

  for (const line of lines) {
    const match = line.match(DIFF_SECTION_PATTERN)
    if (match) {
      pushCurrent()
      const file = match[2] ?? match[1] ?? "diff"
      current = { file, lines: [line] }
      continue
    }
    if (!current) {
      current = { file: "diff", lines: [] }
    }
    current.lines.push(line)
  }
  pushCurrent()

  if (sections.length === 1 && sections[0]?.file === "diff") {
    const fallback = lines.find((line) => line.startsWith(DIFF_FALLBACK_NEW))?.slice(DIFF_FALLBACK_NEW.length).trim()
      ?? lines.find((line) => line.startsWith(DIFF_FALLBACK_OLD))?.slice(DIFF_FALLBACK_OLD.length).trim()
      ?? "diff"
    sections[0] = { file: fallback || "diff", lines: sections[0].lines }
  }

  return sections.length > 0 ? sections : [{ file: "diff", lines }]
}

const formatIsoTimestamp = (ms: number): string => {
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return "unknown time"
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z")
}

interface FilePickerState {
  readonly status: "hidden" | "loading" | "ready" | "error"
  readonly cwd: string
  readonly items: ReadonlyArray<SessionFileInfo>
  readonly index: number
  readonly message?: string
}

interface FileIndexMeta {
  readonly status: "idle" | "scanning" | "ready" | "error"
  readonly fileCount: number
  readonly dirCount: number
  readonly scannedDirs: number
  readonly queuedDirs: number
  readonly truncated: boolean
  readonly message?: string
  readonly version: number
}

interface FileIndexStore {
  generation: number
  running: boolean
  visited: Set<string>
  queue: string[]
  files: Map<string, SessionFileInfo>
  dirs: Map<string, SessionFileInfo>
  lastMetaUpdateMs: number
}

type FileMenuRow =
  | { readonly kind: "resource"; readonly resource: FilePickerResource }
  | { readonly kind: "file"; readonly item: SessionFileInfo }

interface ActiveAtMention {
  readonly start: number
  readonly end: number
  readonly query: string
  readonly quoted: boolean
}

interface QueuedFileMention {
  readonly id: string
  readonly path: string
  readonly size?: number | null
  readonly requestedMode: FileMentionMode
  readonly addedAt: number
}

interface StaticFeedItem {
  readonly id: string
  readonly node: React.ReactNode
}

interface TranscriptMatch {
  readonly line: number
  readonly start: number
  readonly end: number
}

const displayPathForCwd = (fullPath: string, cwd: string): string => {
  if (!cwd || cwd === "." || cwd === "/") return fullPath
  const prefix = `${cwd}/`
  return fullPath.startsWith(prefix) ? fullPath.slice(prefix.length) : fullPath
}

const COMMAND_RESULT_LIMIT = 64
const MAX_COMMAND_LINES = 64

const stripCommandQuotes = (value: string | undefined): string | undefined => {
  if (!value) return undefined
  const trimmed = value.trim()
  if (trimmed.length >= 2) {
    const first = trimmed[0]
    const last = trimmed[trimmed.length - 1]
    if ((first === "\"" && last === "\"") || (first === "'" && last === "'")) {
      return trimmed.slice(1, -1)
    }
  }
  return trimmed
}

const formatSizeDetail = (bytes: number | null | undefined): string | null => {
  if (bytes == null) return null
  return formatBytes(bytes)
}

const normalizeNewlines = (value: string): string => value.replace(/\r\n?/g, "\n")

const measureBytes = (value: string): number => Buffer.byteLength(value, "utf8")

const makeSnippet = (content: string, headLines: number, tailLines: number): string => {
  const lines = content.split("\n")
  if (lines.length <= headLines + tailLines) {
    return content
  }
  const head = lines.slice(0, headLines)
  const tail = lines.slice(-tailLines)
  const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
  if (hiddenCount <= 0) {
    return content
  }
  return [...head, "", "… (truncated) …", "", ...tail].join("\n")
}

const guessFenceLang = (filePath: string): string => {
  const match = /\.([a-zA-Z0-9]+)$/.exec(filePath)
  return match ? match[1].toLowerCase() : "text"
}

type AtCommandKind = "list" | "read"

const AT_COMMAND_ALIASES: Record<AtCommandKind, ReadonlyArray<string>> = {
  list: ["list", "ls", "files"],
  read: ["read", "cat"],
}

const AT_COMMAND_ALIAS_MAP = (() => {
  const map = new Map<string, AtCommandKind>()
  for (const kind of Object.keys(AT_COMMAND_ALIASES) as AtCommandKind[]) {
    for (const alias of AT_COMMAND_ALIASES[kind]) {
      map.set(alias, kind)
    }
  }
  return map
})()

const parseAtCommand = (value: string): { readonly kind: AtCommandKind; readonly argument?: string } | null => {
  const trimmedStart = value.trimStart()
  if (!trimmedStart.startsWith("@")) return null
  const afterAt = trimmedStart.slice(1)
  const match = afterAt.match(/^([a-zA-Z]+)\b/)
  if (!match) return null
  const alias = match[1].toLowerCase()
  const kind = AT_COMMAND_ALIAS_MAP.get(alias)
  if (!kind) return null
  const remainder = afterAt.slice(match[0].length)
  const argument = remainder.trim()
  const parsedArgument = argument.length > 0 ? stripCommandQuotes(argument) ?? argument : undefined
  return { kind, argument: parsedArgument }
}

const normalizeSessionPath = (value: string): string => {
  if (!value) return "."
  const withSlashes = value.replace(/\\/g, "/")
  const trimmedLeading = withSlashes.replace(/^\.\/+/, "")
  const collapsed = trimmedLeading.replace(/\/+/g, "/")
  const strippedTrailing = collapsed.replace(/\/$/, "")
  return strippedTrailing === "" ? "." : strippedTrailing
}

const clampCommandLines = (lines: string[], fallback?: string): string[] => {
  if (lines.length <= MAX_COMMAND_LINES) return lines
  const trimmed = lines.slice(0, Math.max(0, MAX_COMMAND_LINES - 1))
  const suffix = fallback ?? "…Command output truncated for readability."
  return [...trimmed, suffix]
}

const formatFileListLines = (files: SessionFileInfo[]): string[] => {
  if (files.length === 0) {
    return ["(empty)"]
  }
  const sorted = [...files].sort((a, b) => a.path.localeCompare(b.path))
  const typeWidth = Math.max(
    "Type".length,
    ...sorted.map((entry) => entry.type.length),
  )
  const pathWidth = Math.max(
    "Path".length,
    ...sorted.map((entry) => entry.path.length),
  )
  const sizeEntries = sorted.map((entry) => (entry.size != null ? formatBytes(entry.size) : "—"))
  const sizeWidth = Math.max(
    "Size".length,
    ...sizeEntries.map((entry) => entry.length),
  )
  const header = [
    formatCell("Type", typeWidth),
    formatCell("Path", pathWidth),
    formatCell("Size", sizeWidth, "right"),
  ].join(COLUMN_SEPARATOR)
  const underline = [
    "-".repeat(typeWidth),
    "-".repeat(pathWidth),
    "-".repeat(sizeWidth),
  ].join(COLUMN_SEPARATOR)
  const rows = sorted.map((entry, index) => [
    formatCell(entry.type, typeWidth),
    formatCell(entry.path, pathWidth),
    formatCell(sizeEntries[index], sizeWidth, "right"),
  ].join(COLUMN_SEPARATOR))
  return [header, underline, ...rows]
}

const findActiveAtMention = (value: string, cursor: number): ActiveAtMention | null => {
  const safeCursor = Math.max(0, Math.min(cursor, value.length))
  if (value.length === 0) return null
  for (let start = safeCursor - 1; start >= 0; start -= 1) {
    if (value[start] !== "@") continue
    if (start > 0 && !/\s/.test(value[start - 1] ?? "")) continue
    const quoted = value[start + 1] === "\""
    if (quoted) {
      const quoteStart = start + 2
      const closingQuote = value.indexOf("\"", quoteStart)
      const end = closingQuote >= 0 ? closingQuote + 1 : value.length
      if (safeCursor > end) continue
      const query = value.slice(quoteStart, Math.max(quoteStart, safeCursor))
      return { start, end, query, quoted: true }
    }
    const nextWhitespace = value.slice(start + 1).search(/\s/)
    const end = nextWhitespace >= 0 ? start + 1 + nextWhitespace : value.length
    if (safeCursor > end) continue
    const query = value.slice(start + 1, Math.min(end, safeCursor))
    return { start, end, query, quoted: false }
  }
  return null
}

const parseAtMentionQuery = (query: string): { cwd: string; needle: string } => {
  const normalized = query.replace(/^\.\/+/, "")
  const lastSlash = normalized.lastIndexOf("/")
  if (lastSlash < 0) {
    return { cwd: ".", needle: normalized }
  }
  const cwd = normalized.slice(0, lastSlash).replace(/\/+$/, "") || "."
  const needle = normalized.slice(lastSlash + 1)
  return { cwd, needle }
}

const scoreFuzzyMatch = (candidate: string, query: string): number | null => {
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
    const prevChar = index > 0 ? haystack[index - 1] : ""
    if (index === 0 || "/_-.".includes(prevChar)) {
      score += 8
    }
    if (index === lastIndex + 1) {
      consecutive += 1
      score += 12 + consecutive
    } else {
      consecutive = 0
      score -= Math.max(0, index - lastIndex - 1)
    }
    lastIndex = index
  }
  score += Math.max(0, 30 - haystack.length)
  return score
}

const rankFuzzyFileItems = (
  items: ReadonlyArray<SessionFileInfo>,
  query: string,
  limit: number,
  display: (item: SessionFileInfo) => string,
): ReadonlyArray<SessionFileInfo> => {
  const needle = query.trim()
  if (!needle) return items.slice(0, Math.max(0, limit))
  const scored: Array<{ item: SessionFileInfo; score: number }> = []
  for (const item of items) {
    const label = display(item)
    const score = scoreFuzzyMatch(label, needle)
    if (score == null) continue
    scored.push({ item, score })
  }
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    if (a.item.type !== b.item.type) return a.item.type === "file" ? -1 : 1
    const aLen = a.item.path.length
    const bLen = b.item.path.length
    if (aLen !== bLen) return aLen - bLen
    return a.item.path.localeCompare(b.item.path)
  })
  return scored.slice(0, Math.max(0, limit)).map((entry) => entry.item)
}

const longestCommonPrefix = (values: ReadonlyArray<string>): string => {
  if (values.length === 0) return ""
  let prefix = values[0] ?? ""
  for (let index = 1; index < values.length; index += 1) {
    const value = values[index] ?? ""
    let length = 0
    const max = Math.min(prefix.length, value.length)
    while (length < max && prefix[length] === value[length]) {
      length += 1
    }
    prefix = prefix.slice(0, length)
    if (!prefix) break
  }
  return prefix
}

interface WindowSlice<T> {
  readonly items: ReadonlyArray<T>
  readonly hiddenCount: number
  readonly usedLines: number
  readonly truncated: boolean
}

const sliceTailByLineBudget = <T,>(
  entries: ReadonlyArray<T>,
  budgetLines: number,
  measure: (entry: T) => number,
): WindowSlice<T> => {
  const budget = Math.max(0, Math.floor(budgetLines))
  if (entries.length === 0 || budget === 0) {
    return { items: [], hiddenCount: entries.length, usedLines: 0, truncated: false }
  }
  let used = 0
  let start = entries.length
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const cost = Math.max(1, measure(entries[index]))
    if (used + cost > budget && start < entries.length) {
      break
    }
    used += cost
    start = index
    if (used >= budget) break
  }
	  let items = entries.slice(start)
	  let hidden = start
	  let truncated = hidden > 0
	  if (truncated) {
	    while (items.length > 1 && used + 1 > budget) {
	      const removed = items[0]
	      items = items.slice(1)
	      used = Math.max(0, used - Math.max(1, measure(removed)))
	      hidden += 1
	    }
	    truncated = hidden > 0 && items.length > 0
	  }
  return { items, hiddenCount: hidden, usedLines: used + (truncated ? 1 : 0), truncated }
}

const blockToLines = (block: Block): string[] => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  switch (block.type) {
    case "paragraph": {
      const value = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      return value.split(/\r?\n/)
    }
    case "heading": {
      const levelRaw = typeof meta.level === "number" ? meta.level : typeof meta.depth === "number" ? meta.depth : 1
      const level = Math.min(6, Math.max(1, levelRaw || 1))
      const prefix = "#".repeat(level)
      const text = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      return [chalk.bold.hex("#f97316")(`${prefix} ${text}`)]
    }
    case "blockquote": {
      const content = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      return content.split(/\r?\n/).map((line) => chalk.hex("#9ca3af")(`> ${line}`))
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
      return renderCodeLines(raw, lang)
    }
    case "list": {
      const raw = block.payload.raw ?? ""
      return raw.split(/\r?\n/).map((line) => chalk.hex("#e5e7eb")(line))
    }
    case "footnotes":
    case "footnote-def":
    case "table":
    case "mdx":
    case "html":
      return (block.payload.raw ?? "").split(/\r?\n/)
    default:
      return (block.payload.raw ?? "").split(/\r?\n/)
  }
}

const blocksToLines = (blocks?: ReadonlyArray<Block>): string[] => {
  if (!blocks || blocks.length === 0) return []
  const lines: string[] = []
  for (const block of blocks) {
    const rendered = blockToLines(block)
    if (rendered.length === 0) continue
    lines.push(...rendered)
  }
  return lines
}

interface ReplViewProps {
  readonly sessionId: string
  readonly conversation: ConversationEntry[]
  readonly toolEvents: ToolLogEntry[]
  readonly liveSlots: LiveSlotEntry[]
  readonly status: string
  readonly pendingResponse: boolean
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly guardrailNotice?: GuardrailNotice | null
  readonly viewPrefs: TranscriptPreferences
  readonly todos: TodoItem[]
  readonly permissionRequest?: PermissionRequest | null
  readonly permissionQueueDepth?: number
  readonly rewindMenu: RewindMenuState
  readonly onSubmit: (value: string, attachments?: ReadonlyArray<QueuedAttachment>) => Promise<void>
  readonly onModelMenuOpen: () => Promise<void>
  readonly onModelSelect: (item: ModelMenuItem) => Promise<void>
  readonly onModelMenuCancel: () => void
  readonly onGuardrailToggle: () => void
  readonly onGuardrailDismiss: () => void
  readonly onPermissionDecision: (decision: PermissionDecision) => Promise<void>
  readonly onRewindClose: () => void
  readonly onRewindRestore: (checkpointId: string, mode: "conversation" | "code" | "both") => Promise<void>
  readonly onListFiles: (path?: string) => Promise<SessionFileInfo[]>
  readonly onReadFile: (path: string, options?: { mode?: "cat" | "snippet"; headLines?: number; tailLines?: number; maxBytes?: number }) => Promise<SessionFileContent>
}

export const ReplView: React.FC<ReplViewProps> = ({
  sessionId,
  conversation,
  toolEvents,
  liveSlots,
  status,
  pendingResponse,
  hints,
  stats,
  modelMenu,
  guardrailNotice,
  viewPrefs,
  todos,
  permissionRequest,
  permissionQueueDepth,
  rewindMenu,
  onSubmit,
  onModelMenuOpen,
  onModelSelect,
  onModelMenuCancel,
  onGuardrailToggle,
  onGuardrailDismiss,
  onPermissionDecision,
  onRewindClose,
  onRewindRestore,
  onListFiles,
  onReadFile,
}) => {
  const [input, setInput] = useState("")
  const [cursor, setCursor] = useState(0)
  const [suggestIndex, setSuggestIndex] = useState(0)
  const [historyEntries, setHistoryEntries] = useState<string[]>([])
  const [historyPos, setHistoryPos] = useState(0)
  const historyDraftRef = useRef("")
  const [attachments, setAttachments] = useState<QueuedAttachment[]>([])
  const [fileMentions, setFileMentions] = useState<QueuedFileMention[]>([])
  const collapsedEntriesRef = useRef(new Map<string, boolean>())
  const [collapsedVersion, setCollapsedVersion] = useState(0)
  const [selectedCollapsibleEntryId, setSelectedCollapsibleEntryId] = useState<string | null>(null)
  const [paletteState, setPaletteState] = useState<{ status: "hidden" | "open"; query: string; index: number }>({
    status: "hidden",
    query: "",
    index: 0,
  })
  const [confirmState, setConfirmState] = useState<{
    status: "hidden" | "prompt"
    message?: string
    action?: (() => Promise<void> | void) | null
  }>({ status: "hidden" })
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [modelSearch, setModelSearch] = useState("")
  const [modelIndex, setModelIndex] = useState(0)
  const [modelOffset, setModelOffset] = useState(0)
  const [permissionTab, setPermissionTab] = useState<"summary" | "diff" | "rules" | "note">("summary")
  const [permissionScope, setPermissionScope] = useState<PermissionRuleScope>("project")
  const [permissionFileIndex, setPermissionFileIndex] = useState(0)
  const [permissionScroll, setPermissionScroll] = useState(0)
  const [permissionNote, setPermissionNote] = useState("")
  const [permissionNoteCursor, setPermissionNoteCursor] = useState(0)
  const permissionTabRef = useRef(permissionTab)
  const permissionInputSnapshotRef = useRef<{ value: string; cursor: number } | null>(null)
  const permissionActiveRef = useRef(false)
  const permissionNoteRef = useRef(permissionNote)
  const permissionDecisionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [todosOpen, setTodosOpen] = useState(false)
  const [todoScroll, setTodoScroll] = useState(0)
  const [rewindIndex, setRewindIndex] = useState(0)
  const [filePicker, setFilePicker] = useState<FilePickerState>({
    status: "hidden",
    cwd: ".",
    items: [],
    index: 0,
  })
  const filePickerIndexRef = useRef(0)
  const [fileMenuItems, setFileMenuItems] = useState<SessionFileInfo[]>([])
  const fileMenuCacheRef = useRef<{ key: string; status: FileIndexMeta["status"]; mode: "tree" | "fuzzy" } | null>(null)
  const filePickerLoadSeq = useRef(0)
  const [filePickerDismissed, setFilePickerDismissed] = useState<{ tokenStart: number; textVersion: number } | null>(null)
  const [inputTextVersion, setInputTextVersion] = useState(0)
  const inputValueRef = useRef("")
  const [escPrimedAt, setEscPrimedAt] = useState<number | null>(null)
  const [ctrlCPrimedAt, setCtrlCPrimedAt] = useState<number | null>(null)
  const [verboseOutput, setVerboseOutput] = useState(false)
  const [transcriptViewerOpen, setTranscriptViewerOpen] = useState(false)
  const [transcriptViewerScroll, setTranscriptViewerScroll] = useState(0)
  const [transcriptViewerFollowTail, setTranscriptViewerFollowTail] = useState(true)
  const [transcriptSearchQuery, setTranscriptSearchQuery] = useState("")
  const [transcriptSearchOpen, setTranscriptSearchOpen] = useState(false)
  const [transcriptSearchIndex, setTranscriptSearchIndex] = useState(0)
  const { stdout } = useStdout()
  const [staticFeed, setStaticFeed] = useState<StaticFeedItem[]>([])
  const pushCommandResult = useCallback((title: string, lines: string[]) => {
    const entryId = `atcmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
    const node = (
      <Box flexDirection="column" marginBottom={1}>
        <Text color="cyan">{title}</Text>
        {lines.map((line, index) => (
          <Text key={`${entryId}-line-${index}`} wrap="truncate-end">
            {line}
          </Text>
        ))}
      </Box>
    )
    setStaticFeed((prev) => {
      const next = [...prev, { id: entryId, node }]
      if (next.length <= COMMAND_RESULT_LIMIT) return next
      return next.slice(next.length - COMMAND_RESULT_LIMIT)
    })
  }, [])
  const headerPrintedRef = useRef(false)
  const printedConversationIdsRef = useRef(new Set<string>())
  const printedToolIdsRef = useRef(new Set<string>())
  const fileMentionConfig = useMemo(() => loadFileMentionConfig(), [])
  const filePickerConfig = useMemo(() => loadFilePickerConfig(), [])
  const keymap = useMemo(() => loadKeymapConfig(), [])
  const chromeMode = useMemo(() => loadChromeMode(keymap), [keymap])
  const claudeChrome = chromeMode === "claude"
  const filePickerResources = useMemo(() => loadFilePickerResources(), [])
  const [filePickerNeedle, setFilePickerNeedle] = useState("")
  const filePickerNeedleSeq = useRef(0)
  const fileIndexRef = useRef<FileIndexStore>({
    generation: 0,
    running: false,
    visited: new Set<string>(),
    queue: [],
    files: new Map<string, SessionFileInfo>(),
    dirs: new Map<string, SessionFileInfo>(),
    lastMetaUpdateMs: 0,
  })
  const [fileIndexMeta, setFileIndexMeta] = useState<FileIndexMeta>({
    status: "idle",
    fileCount: 0,
    dirCount: 0,
    scannedDirs: 0,
    queuedDirs: 0,
    truncated: false,
    message: undefined,
    version: 0,
  })
  const columnWidth = stdout?.columns && Number.isFinite(stdout.columns) ? stdout.columns : 80
  const contentWidth = useMemo(() => Math.max(10, columnWidth - 2), [columnWidth])
  const rowCount = stdout?.rows && Number.isFinite(stdout.rows) ? stdout.rows : 40
  const PANEL_WIDTH = useMemo(() => Math.min(96, Math.max(60, Math.floor(columnWidth * 0.8))), [columnWidth])
  const modelColumnLayout = useMemo(() => computeModelColumns(columnWidth), [columnWidth])
  const modelMenuHeaderText = useMemo(() => {
    const headerCells = [
      formatCell("Provider · Model", modelColumnLayout.providerWidth),
    ]
    if (modelColumnLayout.showContext) headerCells.push(formatCell("Context", CONTEXT_COLUMN_WIDTH, "right"))
    if (modelColumnLayout.showPriceIn) headerCells.push(formatCell("$/M In", PRICE_COLUMN_WIDTH, "right"))
    if (modelColumnLayout.showPriceOut) headerCells.push(formatCell("$/M Out", PRICE_COLUMN_WIDTH, "right"))
    return buildModelRowText(headerCells)
  }, [modelColumnLayout])
  const formatModelRowText = useCallback(
    (item: ModelMenuItem) => {
      const cells = [formatProviderCell(item, modelColumnLayout.providerWidth)]
      if (modelColumnLayout.showContext) cells.push(formatContextCell(item.contextTokens ?? null, CONTEXT_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceIn) cells.push(formatPriceCell(item.priceInPerM ?? null, PRICE_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceOut) cells.push(formatPriceCell(item.priceOutPerM ?? null, PRICE_COLUMN_WIDTH))
      return buildModelRowText(cells)
    },
    [modelColumnLayout],
  )
  const CONTENT_PADDING = 6
  const MAX_VISIBLE_MODELS = 8
  const animationActive = pendingResponse || liveSlots.length > 0
  const animationTick = useAnimationClock(animationActive, 120)
  const spinner = useSpinner(pendingResponse, animationActive ? animationTick : undefined)
  const suggestions = useMemo(() => buildSuggestions(input, MAX_SUGGESTIONS), [input])
  const activeSlashQuery = useMemo(() => {
    if (!input.startsWith("/")) return ""
    const [lookup] = input.slice(1).split(/\s+/)
    return lookup ?? ""
  }, [input])
  const maxVisibleSuggestions = useMemo(() => {
    if (claudeChrome) {
      return Math.max(10, Math.min(18, Math.floor(rowCount * 0.55)))
    }
    return Math.max(8, Math.min(14, Math.floor(rowCount * 0.4)))
  }, [claudeChrome, rowCount])
  const fileMenuMaxRows = useMemo(() => {
    if (claudeChrome) {
      return Math.max(8, Math.min(16, Math.floor(rowCount * 0.5)))
    }
    return 8
  }, [claudeChrome, rowCount])
  const wrapSuggestionText = useCallback((text: string, width: number, maxLines = Infinity) => {
    if (width <= 0) return [text]
    const words = text.trim().split(/\s+/).filter(Boolean)
    if (words.length === 0) return [""]
    const lines: string[] = []
    let current = ""
    for (const word of words) {
      if (word.length > width) {
        if (current) {
          lines.push(current)
          current = ""
        }
        for (let i = 0; i < word.length; i += width) {
          lines.push(word.slice(i, i + width))
        }
        continue
      }
      if (!current) {
        current = word
        continue
      }
      if (current.length + 1 + word.length <= width) {
        current = `${current} ${word}`
      } else {
        lines.push(current)
        current = word
      }
    }
    if (current) lines.push(current)
    if (lines.length > maxLines) {
      const trimmed = lines.slice(0, Math.max(1, maxLines))
      const lastIndex = trimmed.length - 1
      let last = trimmed[lastIndex] ?? ""
      const ellipsis = "…"
      if (width <= 1) {
        last = ellipsis
      } else if (last.length + ellipsis.length > width) {
        last = last.slice(0, Math.max(0, width - 1))
      }
      trimmed[lastIndex] = `${last}${ellipsis}`
      return trimmed
    }
    return lines
  }, [])
  const suggestionLayout = useMemo(() => {
    const maxWidth = claudeChrome ? columnWidth - 4 : Math.min(columnWidth - 4, 72)
    const totalWidth = Math.max(24, maxWidth)
    const commandWidth = Math.max(14, Math.min(28, Math.floor(totalWidth * 0.35)))
    const summaryWidth = Math.max(8, totalWidth - commandWidth - 2)
    return { totalWidth, commandWidth, summaryWidth }
  }, [claudeChrome, columnWidth])
  const buildSuggestionLines = useCallback(
    (row: SlashSuggestion, multiline: boolean): Array<{ label: string; summary: string }> => {
      const label = `${row.command}${row.usage ? ` ${row.usage}` : ""}`
      if (!multiline) {
        return [{ label, summary: row.summary }]
      }
      const summaryMaxLines = 2
      const summaryLines = wrapSuggestionText(row.summary, suggestionLayout.summaryWidth, summaryMaxLines)
      if (summaryLines.length === 0) summaryLines.push("")
      return summaryLines.map((line, index) => ({
        label: index === 0 ? label : "",
        summary: line,
      }))
    },
    [suggestionLayout.summaryWidth, wrapSuggestionText],
  )
  const suggestionWindow = useMemo(() => {
    if (suggestions.length === 0) {
      return { items: [] as SlashSuggestion[], hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount: 0 }
    }
    const maxRows = Math.max(1, Math.min(maxVisibleSuggestions, suggestions.length))
    if (suggestions.length <= maxRows) {
      const lineCount = suggestions.reduce((sum, row) => sum + buildSuggestionLines(row, claudeChrome).length, 0)
      return { items: suggestions, hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount }
    }
    const half = Math.floor(maxRows / 2)
    const start = Math.min(
      Math.max(0, suggestIndex - half),
      Math.max(0, suggestions.length - maxRows),
    )
    const items = suggestions.slice(start, start + maxRows)
    const lineCount = items.reduce((sum, row) => sum + buildSuggestionLines(row, claudeChrome).length, 0)
    return {
      items,
      hiddenAbove: start,
      hiddenBelow: Math.max(0, suggestions.length - (start + items.length)),
      start,
      lineCount,
    }
  }, [buildSuggestionLines, claudeChrome, maxVisibleSuggestions, suggestIndex, suggestions])
  const paletteItems: ReadonlyArray<SlashCommandInfo> = useMemo(() => {
    if (paletteState.status === "hidden") return []
    const query = paletteState.query.trim().toLowerCase()
    if (!query) return SLASH_COMMANDS
    return SLASH_COMMANDS.filter((command) => command.name.toLowerCase().includes(query))
  }, [paletteState])
  const inputLocked =
    modelMenu.status !== "hidden" ||
    paletteState.status === "open" ||
    confirmState.status === "prompt" ||
    shortcutsOpen ||
    Boolean(permissionRequest) ||
    rewindMenu.status !== "hidden" ||
    todosOpen ||
    transcriptViewerOpen

  const overlayActive =
    modelMenu.status !== "hidden" ||
    paletteState.status === "open" ||
    confirmState.status === "prompt" ||
    shortcutsOpen ||
    Boolean(permissionRequest) ||
    rewindMenu.status !== "hidden" ||
    todosOpen ||
    transcriptViewerOpen

  const activeAtMention = useMemo(
    () => (inputLocked ? null : findActiveAtMention(input, cursor)),
    [cursor, input, inputLocked],
  )
  const activeAtMentionQuery = activeAtMention?.query ?? ""
  const filePickerQueryParts = useMemo(
    () => (activeAtMention ? parseAtMentionQuery(activeAtMentionQuery) : { cwd: ".", needle: "" }),
    [activeAtMention, activeAtMentionQuery],
  )
  const activeAtCommand = useMemo(() => {
    if (inputLocked) return false
    const command = parseAtCommand(input)
    return Boolean(command)
  }, [input, inputLocked])
  const filePickerActive =
    activeAtMention != null &&
    !activeAtCommand &&
    (!filePickerDismissed ||
      filePickerDismissed.tokenStart !== activeAtMention.start ||
      filePickerDismissed.textVersion !== inputTextVersion)
  const filePickerResourcesVisible = useMemo(() => {
    if (!claudeChrome || !filePickerActive) return [] as FilePickerResource[]
    if (filePickerQueryParts.cwd !== ".") return [] as FilePickerResource[]
    const needle = filePickerQueryParts.needle.trim().toLowerCase()
    if (!needle) return filePickerResources
    return filePickerResources.filter((entry) => entry.label.toLowerCase().includes(needle))
  }, [claudeChrome, filePickerActive, filePickerQueryParts.cwd, filePickerQueryParts.needle, filePickerResources])

  useEffect(() => {
    if (!filePickerActive || filePickerConfig.mode === "tree") {
      setFilePickerNeedle("")
      return
    }
    const trimmed = filePickerQueryParts.needle.trim()
    if (!trimmed) {
      setFilePickerNeedle("")
      return
    }
    const seq = (filePickerNeedleSeq.current += 1)
    const timer = setTimeout(() => {
      if (filePickerNeedleSeq.current !== seq) return
      setFilePickerNeedle(trimmed)
    }, 80)
    return () => clearTimeout(timer)
  }, [filePickerActive, filePickerConfig.mode, filePickerQueryParts.needle])

  const transcriptViewerLines = useMemo(() => {
    const lines: string[] = []
    const normalizeNewlines = (text: string) => text.replace(/\r\n?/g, "\n")
    for (const entry of conversation) {
      const speaker = entry.speaker.toUpperCase()
      lines.push(`${speaker}:`)
      const entryLines = normalizeNewlines(entry.text).split("\n")
      for (const line of entryLines) {
        lines.push(`  ${line}`)
      }
      lines.push("")
    }
    if (toolEvents.length > 0) {
      if (lines.length > 0 && lines[lines.length - 1] !== "") {
        lines.push("")
      }
      lines.push("TOOLS:")
      for (const entry of toolEvents) {
        const status = entry.status ? ` ${entry.status}` : ""
        const kindToken = `[${entry.kind}]`
        const entryText = entry.text.startsWith(kindToken)
          ? entry.text.slice(kindToken.length).trimStart()
          : entry.text
        lines.push(`  [${entry.kind}]${status}`)
        const toolLines = normalizeNewlines(entryText).split("\n")
        if (verboseOutput || toolLines.length <= TOOL_COLLAPSE_THRESHOLD) {
          for (const line of toolLines) {
            lines.push(`    ${line}`)
          }
        } else {
          const head = toolLines.slice(0, 2)
          const tail = toolLines.slice(-1)
          const hidden = Math.max(0, toolLines.length - head.length - tail.length)
          for (const line of head) {
            lines.push(`    ${line}`)
          }
          if (hidden > 0) {
            const diffPreview = computeDiffPreview(toolLines)
            const filesPart =
              diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
            const summary =
              diffPreview
                ? `Δ +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}`
                : `${hidden} line${hidden === 1 ? "" : "s"} hidden`
            lines.push(`    … ${summary} (Ctrl+O for detailed transcript)`)
          }
          for (const line of tail) {
            lines.push(`    ${line}`)
          }
        }
        lines.push("")
      }
    }
    while (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop()
    }
    return lines
  }, [conversation, toolEvents, verboseOutput])

  const transcriptViewerBodyRows = useMemo(() => Math.max(1, rowCount - 4), [rowCount])
  const transcriptViewerMaxScroll = useMemo(
    () => Math.max(0, transcriptViewerLines.length - transcriptViewerBodyRows),
    [transcriptViewerBodyRows, transcriptViewerLines.length],
  )
  const transcriptViewerEffectiveScroll = useMemo(() => {
    if (transcriptViewerFollowTail) return transcriptViewerMaxScroll
    return Math.max(0, Math.min(transcriptViewerScroll, transcriptViewerMaxScroll))
  }, [transcriptViewerFollowTail, transcriptViewerMaxScroll, transcriptViewerScroll])

  const transcriptSearchMatches = useMemo<ReadonlyArray<TranscriptMatch>>(() => {
    const query = transcriptSearchQuery.trim().toLowerCase()
    if (!query) return []
    const matches: TranscriptMatch[] = []
    for (let line = 0; line < transcriptViewerLines.length; line += 1) {
      const candidate = stripAnsiCodes(transcriptViewerLines[line] ?? "")
      const haystack = candidate.toLowerCase()
      let start = 0
      while (start <= haystack.length) {
        const index = haystack.indexOf(query, start)
        if (index === -1) break
        matches.push({ line, start: index, end: index + query.length })
        start = index + query.length
      }
    }
    return matches
  }, [transcriptSearchQuery, transcriptViewerLines])

  const transcriptSearchSafeIndex = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return 0
    return Math.max(0, Math.min(transcriptSearchIndex, transcriptSearchMatches.length - 1))
  }, [transcriptSearchIndex, transcriptSearchMatches.length])

  const transcriptSearchActiveLine = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return null
    return transcriptSearchMatches[transcriptSearchSafeIndex]?.line ?? null
  }, [transcriptSearchMatches, transcriptSearchSafeIndex])

  const transcriptSearchLineMatches = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return []
    const set = new Set<number>()
    for (const match of transcriptSearchMatches) {
      set.add(match.line)
    }
    return Array.from(set)
  }, [transcriptSearchMatches])

  const jumpTranscriptToLine = useCallback(
    (line: number) => {
      const target = Math.max(0, Math.min(transcriptViewerLines.length - 1, line))
      const desired = Math.max(0, target - Math.floor(transcriptViewerBodyRows / 2))
      const clamped = Math.max(0, Math.min(transcriptViewerMaxScroll, desired))
      setTranscriptViewerFollowTail(false)
      setTranscriptViewerScroll(clamped)
    },
    [transcriptViewerBodyRows, transcriptViewerLines.length, transcriptViewerMaxScroll],
  )

  useEffect(() => {
    if (!transcriptViewerOpen) return
    if (!transcriptSearchOpen) return
    if (transcriptSearchMatches.length === 0) return
    setTranscriptSearchIndex(0)
    const line = transcriptSearchMatches[0]?.line
    if (typeof line === "number") {
      jumpTranscriptToLine(line)
    }
  }, [jumpTranscriptToLine, transcriptSearchMatches, transcriptSearchOpen, transcriptViewerOpen, transcriptSearchQuery])

  const permissionDiffSections = useMemo(() => {
    const diff = permissionRequest?.diffText
    if (!diff) return []
    return splitUnifiedDiff(diff)
  }, [permissionRequest?.diffText])

  const permissionDiffPreview = useMemo(() => {
    const diff = permissionRequest?.diffText
    if (!diff) return null
    const lines = diff.replace(/\r\n?/g, "\n").split("\n")
    return computeDiffPreview(lines)
  }, [permissionRequest?.diffText])

  const permissionSelectedFileIndex = useMemo(() => {
    if (permissionDiffSections.length === 0) return 0
    return Math.max(0, Math.min(permissionFileIndex, permissionDiffSections.length - 1))
  }, [permissionDiffSections.length, permissionFileIndex])

  const permissionSelectedSection = useMemo(() => {
    if (permissionDiffSections.length === 0) return null
    return permissionDiffSections[permissionSelectedFileIndex] ?? null
  }, [permissionDiffSections, permissionSelectedFileIndex])

  const permissionDiffLines = useMemo(() => {
    if (!permissionSelectedSection) return []
    return renderCodeLines(permissionSelectedSection.lines.join("\n"), "diff")
  }, [permissionSelectedSection])

  const permissionViewportRows = useMemo(() => Math.max(8, Math.min(20, Math.floor(rowCount * 0.45))), [rowCount])

  const rewindCheckpoints: ReadonlyArray<CheckpointSummary> = useMemo(() => {
    if (rewindMenu.status === "hidden") return []
    return rewindMenu.checkpoints
  }, [rewindMenu])

  const rewindSelectedIndex = useMemo(() => {
    if (rewindCheckpoints.length === 0) return 0
    return Math.max(0, Math.min(rewindIndex, rewindCheckpoints.length - 1))
  }, [rewindCheckpoints.length, rewindIndex])

  const rewindVisibleLimit = useMemo(() => Math.max(6, Math.min(10, Math.floor(rowCount * 0.28))), [rowCount])
  const rewindOffset = useMemo(() => {
    if (rewindCheckpoints.length <= rewindVisibleLimit) return 0
    const half = Math.floor(rewindVisibleLimit / 2)
    const target = Math.max(0, rewindSelectedIndex - half)
    return Math.min(target, Math.max(0, rewindCheckpoints.length - rewindVisibleLimit))
  }, [rewindCheckpoints.length, rewindSelectedIndex, rewindVisibleLimit])

  const rewindVisible = useMemo(
    () => rewindCheckpoints.slice(rewindOffset, rewindOffset + rewindVisibleLimit),
    [rewindCheckpoints, rewindOffset, rewindVisibleLimit],
  )
  const todoGroups = useMemo(() => {
    const groups: Record<string, TodoItem[]> = {
      in_progress: [],
      todo: [],
      blocked: [],
      done: [],
      canceled: [],
    }
    for (const item of todos) {
      const status = String(item.status || "todo").toLowerCase()
      if (status in groups) {
        groups[status].push(item)
      } else {
        groups.todo.push(item)
      }
    }
    return groups
  }, [todos])
  const todoRows = useMemo(() => {
    const rows: Array<{ kind: "header" | "item"; label: string; status?: string }> = []
    const pushGroup = (label: string, items: TodoItem[], status: string) => {
      if (items.length === 0) return
      rows.push({ kind: "header", label, status })
      for (const item of items) {
        rows.push({ kind: "item", label: item.title, status: item.status })
      }
    }
    pushGroup("In Progress", todoGroups.in_progress, "in_progress")
    pushGroup("Todo", todoGroups.todo, "todo")
    pushGroup("Blocked", todoGroups.blocked, "blocked")
    pushGroup("Done", todoGroups.done, "done")
    pushGroup("Canceled", todoGroups.canceled, "canceled")
    return rows
  }, [todoGroups])
  const todoViewportRows = useMemo(() => Math.max(8, Math.min(18, Math.floor(rowCount * 0.45))), [rowCount])
  const todoMaxScroll = Math.max(0, todoRows.length - todoViewportRows)
  const filteredModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const query = modelSearch.trim().toLowerCase()
    const base = modelMenu.items
    const groups = new Map<string, ModelMenuItem[]>()
    const order: string[] = []
    const normalizeProvider = (value: string | null | undefined): string => {
      if (!value) return "other"
      return value.toLowerCase()
    }
    if (query.length === 0) {
      for (const item of base) {
        const key = normalizeProvider(item.provider)
        if (!groups.has(key)) {
          groups.set(key, [])
        }
        groups.get(key)?.push(item)
      }
      for (const provider of MODEL_PROVIDER_ORDER) {
        if (groups.has(provider)) order.push(provider)
      }
      for (const provider of groups.keys()) {
        if (!order.includes(provider)) order.push(provider)
      }
    } else {
      const scored = new Map<string, Array<{ item: ModelMenuItem; score: number }>>()
      for (const item of base) {
        const candidate = `${item.provider} ${item.label} ${item.value}`
        const score = scoreFuzzyMatch(candidate, query)
        if (score == null) continue
        const key = normalizeProvider(item.provider)
        if (!scored.has(key)) scored.set(key, [])
        scored.get(key)?.push({ item, score })
      }
      for (const item of base) {
        const key = normalizeProvider(item.provider)
        if (!scored.has(key)) continue
        if (!order.includes(key)) order.push(key)
      }
      for (const [key, entries] of scored.entries()) {
        entries.sort((a, b) => {
          if (b.score !== a.score) return b.score - a.score
          if (a.item.label.length !== b.item.label.length) return a.item.label.length - b.item.label.length
          return a.item.label.localeCompare(b.item.label)
        })
        groups.set(
          key,
          entries.map((entry) => entry.item),
        )
      }
      const ordered: string[] = []
      for (const provider of MODEL_PROVIDER_ORDER) {
        if (groups.has(provider)) ordered.push(provider)
      }
      for (const provider of order) {
        if (!ordered.includes(provider)) ordered.push(provider)
      }
      order.splice(0, order.length, ...ordered)
    }
    const grouped: ModelMenuItem[] = []
    for (const provider of order) {
      const items = groups.get(provider)
      if (items) grouped.push(...items)
    }
    return grouped
  }, [modelMenu, modelSearch])

  useEffect(() => {
    if (modelMenu.status === "hidden") {
      setModelSearch("")
      setModelIndex(0)
      setModelOffset(0)
    }
  }, [modelMenu.status])

  useEffect(() => {
    if (!permissionRequest) return
    setPermissionScope("project")
    setPermissionScroll(0)
    setPermissionFileIndex(0)
    setPermissionNote("")
    setPermissionNoteCursor(0)
    const initialTab = permissionRequest.diffText ? "diff" : "summary"
    permissionTabRef.current = initialTab
    setPermissionTab(initialTab)
  }, [permissionRequest])

  useEffect(() => {
    permissionTabRef.current = permissionTab
  }, [permissionTab])

  useEffect(() => {
    permissionNoteRef.current = permissionNote
  }, [permissionNote])

  useEffect(() => {
    return () => {
      if (permissionDecisionTimerRef.current) {
        clearTimeout(permissionDecisionTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!todosOpen) return
    setTodoScroll(0)
  }, [todosOpen, todos.length])

  useEffect(() => {
    if (rewindMenu.status === "hidden") {
      setRewindIndex(0)
    }
  }, [rewindMenu.status])

  useEffect(() => {
    filePickerIndexRef.current = filePicker.index
  }, [filePicker.index])

  useEffect(() => {
    if (!input.startsWith("/")) return
    setSuggestIndex(0)
  }, [input, suggestions.length])

  useEffect(() => {
    if (!escPrimedAt) return
    const timer = setTimeout(() => setEscPrimedAt(null), 700)
    return () => clearTimeout(timer)
  }, [escPrimedAt])

  useEffect(() => {
    if (!ctrlCPrimedAt) return
    const timer = setTimeout(() => setCtrlCPrimedAt(null), DOUBLE_CTRL_C_WINDOW_MS)
    return () => clearTimeout(timer)
  }, [ctrlCPrimedAt])

  useEffect(() => {
    if (!stdout?.isTTY) return
    if (!transcriptViewerOpen) return
    return () => {
      try {
        stdout.write("\u001b[?1006l")
        stdout.write("\u001b[?1000l")
        stdout.write("\u001b[?1049l")
      } catch {
        // ignore
      }
    }
  }, [stdout, transcriptViewerOpen])

  const getTopLayer = useCallback<() => LayerName>(
    () => {
      if (
        confirmState.status === "prompt" ||
        modelMenu.status !== "hidden" ||
        shortcutsOpen ||
        permissionRequest ||
        rewindMenu.status !== "hidden" ||
        todosOpen ||
        transcriptViewerOpen
      )
        return "modal"
      if (paletteState.status === "open") return "palette"
      return "editor"
    },
    [
      confirmState.status,
      modelMenu.status,
      shortcutsOpen,
      paletteState.status,
      permissionRequest,
      rewindMenu.status,
      todosOpen,
      transcriptViewerOpen,
    ],
  )

  const enterTranscriptViewer = useCallback(() => {
    if (transcriptViewerOpen) return
    setCtrlCPrimedAt(null)
    setEscPrimedAt(null)
    setTranscriptViewerFollowTail(true)
    setTranscriptViewerScroll(0)
    setTranscriptSearchOpen(false)
    setTranscriptSearchQuery("")
    setTranscriptSearchIndex(0)
    if (stdout?.isTTY) {
      try {
        stdout.write("\u001b[?1049h")
        stdout.write("\u001b[H\u001b[2J")
        stdout.write("\u001b[?1000h")
        stdout.write("\u001b[?1006h")
      } catch {
        // ignore
      }
    }
    setTranscriptViewerOpen(true)
  }, [stdout, transcriptViewerOpen])

  const exitTranscriptViewer = useCallback(() => {
    if (!transcriptViewerOpen) return
    if (stdout?.isTTY) {
      try {
        stdout.write("\u001b[?1006l")
        stdout.write("\u001b[?1000l")
        stdout.write("\u001b[?1049l")
      } catch {
        // ignore
      }
    }
    setTranscriptViewerOpen(false)
    setTranscriptViewerFollowTail(true)
    setTranscriptViewerScroll(0)
    setTranscriptSearchOpen(false)
    setTranscriptSearchQuery("")
    setTranscriptSearchIndex(0)
    setEscPrimedAt(null)
  }, [stdout, transcriptViewerOpen])

  const handleLineEdit = useCallback(
    (nextValue: string, nextCursor: number) => {
      const prevValue = inputValueRef.current
      inputValueRef.current = nextValue
      if (nextValue !== prevValue) {
        setInputTextVersion((prev) => prev + 1)
      }
      setInput(nextValue)
      setCursor(Math.max(0, Math.min(nextCursor, nextValue.length)))
      if (historyPos === historyEntries.length) {
        historyDraftRef.current = nextValue
      }
    },
    [historyEntries.length, historyPos],
  )

  const handleLineEditGuarded = useCallback(
    (nextValue: string, nextCursor: number) => {
      if (inputLocked) return
      if (!shortcutsOpen && nextValue === "?" && inputValueRef.current.trim() === "") {
        setShortcutsOpen(true)
        handleLineEdit("", 0)
        return
      }
      handleLineEdit(nextValue, nextCursor)
    },
    [handleLineEdit, inputLocked, shortcutsOpen],
  )

  useEffect(() => {
    const active = Boolean(permissionRequest)
    const wasActive = permissionActiveRef.current
    if (active && !wasActive) {
      permissionInputSnapshotRef.current = { value: inputValueRef.current, cursor }
    }
    if (!active && wasActive) {
      const snapshot = permissionInputSnapshotRef.current
      if (snapshot) {
        handleLineEdit(snapshot.value, snapshot.cursor)
      }
      permissionInputSnapshotRef.current = null
      if (permissionNote) {
        setPermissionNote("")
        setPermissionNoteCursor(0)
      }
    }
    permissionActiveRef.current = active
  }, [cursor, handleLineEdit, permissionNote, permissionRequest])

  const closeFilePicker = useCallback(() => {
    setFilePicker((prev) =>
      prev.status === "hidden"
        ? prev
        : {
            status: "hidden",
            cwd: ".",
            items: [],
            index: 0,
          },
    )
  }, [])

  const findFileMetadata = useCallback(
    async (targetPath: string): Promise<SessionFileInfo | null> => {
      try {
        const normalized = normalizeSessionPath(targetPath)
        const parent = path.posix.dirname(normalized)
        const scope = parent === "." ? undefined : parent
        const entries = await onListFiles(scope)
        return entries.find((entry) => normalizeSessionPath(entry.path) === normalized) ?? null
      } catch {
        return null
      }
    },
    [onListFiles],
  )

  const handleAtCommand = useCallback(
    async (command: { readonly kind: AtCommandKind; readonly argument?: string }) => {
      closeFilePicker()
      try {
        if (command.kind === "list") {
          const displayTarget =
            command.argument && command.argument.trim().length > 0 ? command.argument.trim() : "."
          const normalizedTarget = displayTarget === "." ? "." : normalizeSessionPath(displayTarget)
          const listScope = normalizedTarget === "." ? undefined : normalizedTarget
          const entries = await onListFiles(listScope)
          const headerLine = `Listing files in ${displayTarget === "." ? "workspace root" : displayTarget}:`
          const title = displayTarget === "." ? "Files" : `Files: ${displayTarget}`
          const lines = clampCommandLines(
            [headerLine, ...formatFileListLines(entries)],
            "…Listing truncated to keep output manageable.",
          )
          pushCommandResult(title, lines)
          return
        }
        if (command.kind === "read") {
          if (!command.argument) {
            pushCommandResult("@read", ["Please provide a file path to read (e.g., @read src/index.ts)."])
            return
          }
          const rawArg = command.argument.trim()
          const normalizedTarget = normalizeSessionPath(rawArg)
          const metadata = await findFileMetadata(normalizedTarget)
          const preferCat =
            metadata?.size != null && metadata.size <= fileMentionConfig.maxInlineBytesPerFile
          const readOptions: { mode: "cat" } | { mode: "snippet"; headLines: number; tailLines: number; maxBytes: number } =
            preferCat
              ? { mode: "cat" }
              : {
                  mode: "snippet",
                  headLines: fileMentionConfig.snippetHeadLines,
                  tailLines: fileMentionConfig.snippetTailLines,
                  maxBytes: fileMentionConfig.snippetMaxBytes,
                }
          const result = await onReadFile(normalizedTarget, readOptions)
          let content = normalizeNewlines(result.content ?? "")
          let truncated = result.truncated === true
          if (preferCat) {
            const byteCount = measureBytes(content)
            if (byteCount > fileMentionConfig.maxInlineBytesPerFile) {
              truncated = true
              content = makeSnippet(content, fileMentionConfig.snippetHeadLines, fileMentionConfig.snippetTailLines)
            }
          } else {
            truncated = true
          }
          const sizeBytes = metadata?.size ?? result.total_bytes ?? null
          const headerSize = formatSizeDetail(sizeBytes)
          const headerLine = `### File: ${rawArg}${headerSize ? ` (${headerSize})` : ""}`
          const fenceLang = guessFenceLang(normalizedTarget)
          const contentLines = content.length === 0 ? [""] : content.split("\n")
          const blockLines = [`\`\`\`${fenceLang}`, ...contentLines, "```"]
          const resultLines = [headerLine, "", ...blockLines]
          let truncatedNotice: string | undefined
          if (truncated) {
            truncatedNotice = `…This file is truncated, as it is too large${headerSize ? ` (${headerSize})` : ""} to fully display. You may read and search through the full file using your provided tools.`
            resultLines.push("", truncatedNotice)
          }
          const finalLines = clampCommandLines(resultLines, truncatedNotice)
          pushCommandResult(`File: ${rawArg}`, finalLines)
          return
        }
      } catch (error) {
        const message = (error as Error).message || String(error)
        pushCommandResult(`@${command.kind}`, [`Error: ${message}`])
      } finally {
        setSuggestIndex(0)
        handleLineEdit("", 0)
      }
    },
    [
      closeFilePicker,
      fileMentionConfig,
      findFileMetadata,
      handleLineEdit,
      onListFiles,
      onReadFile,
      pushCommandResult,
      setSuggestIndex,
    ],
  )

  useEffect(() => {
    if (!activeAtMention) {
      setFilePickerDismissed(null)
    }
  }, [activeAtMention])

  const shouldIndexDirectory = useCallback(
    (dirPath: string): boolean => {
      const name = dirPath.split("/").filter(Boolean).pop() ?? dirPath
      if (!name) return true
      if (name === ".git") return false
      if (!filePickerConfig.indexNodeModules && name === "node_modules") return false
      if (name === ".venv" || name === "__pycache__") return false
      if (!filePickerConfig.indexHiddenDirs && name.startsWith(".") && name !== ".github") return false
      return true
    },
    [filePickerConfig.indexHiddenDirs, filePickerConfig.indexNodeModules],
  )

  const ensureFileIndexScan = useCallback(() => {
    if (filePickerConfig.mode === "tree") return
    if (fileIndexMeta.status === "ready") return
    const store = fileIndexRef.current
    if (store.running) return

    if (fileIndexMeta.status === "error") {
      store.visited.clear()
      store.queue.length = 0
      store.files.clear()
      store.dirs.clear()
    }

    store.running = true
    store.generation += 1
    const generation = store.generation
    store.lastMetaUpdateMs = 0

    if (store.queue.length === 0 && store.visited.size === 0) {
      store.queue.push(".")
    } else if (!store.visited.has(".") && !store.queue.includes(".")) {
      store.queue.push(".")
    }

     const updateMeta = (force: boolean, statusOverride?: FileIndexMeta["status"], message?: string) => {
       if (fileIndexRef.current.generation !== generation) return
       const now = Date.now()
       if (!force && now - store.lastMetaUpdateMs < 250) return
       store.lastMetaUpdateMs = now
       const truncated = store.files.size >= filePickerConfig.maxIndexFiles
       setFileIndexMeta((prev) => ({
         status: statusOverride ?? prev.status,
        fileCount: store.files.size,
        dirCount: store.dirs.size,
        scannedDirs: store.visited.size,
        queuedDirs: store.queue.length,
        truncated,
        message,
        version: prev.version + 1,
      }))
    }

    setFileIndexMeta((prev) => ({
      ...prev,
      status: "scanning",
      message: undefined,
      queuedDirs: store.queue.length,
      scannedDirs: store.visited.size,
      fileCount: store.files.size,
      dirCount: store.dirs.size,
      truncated: store.files.size >= filePickerConfig.maxIndexFiles,
      version: prev.version + 1,
    }))

    const maxFiles = filePickerConfig.maxIndexFiles
    const concurrency = Math.max(1, Math.min(16, Math.floor(filePickerConfig.indexConcurrency)))

    const worker = async () => {
      while (fileIndexRef.current.generation === generation) {
        const cwd = store.queue.shift()
        if (!cwd) return
        if (store.visited.has(cwd)) continue
        store.visited.add(cwd)
        try {
          const scope = cwd === "." ? undefined : cwd
          const entries = await onListFiles(scope)
          for (const entry of entries) {
            if (entry.type === "directory") {
              if (!shouldIndexDirectory(entry.path)) continue
              if (!store.dirs.has(entry.path)) {
                store.dirs.set(entry.path, entry)
              }
              if (!store.visited.has(entry.path)) {
                store.queue.push(entry.path)
              }
              continue
            }
            if (!store.files.has(entry.path)) {
              store.files.set(entry.path, entry)
            }
            if (store.files.size >= maxFiles) {
              store.queue.length = 0
              break
            }
          }
        } catch (error) {
          if (cwd === ".") {
            throw error
          }
        } finally {
          updateMeta(false, "scanning")
        }
      }
    }

    void (async () => {
      try {
        const workers = Array.from({ length: concurrency }, () => worker())
        await Promise.all(workers)
        if (fileIndexRef.current.generation !== generation) return
        store.running = false
        updateMeta(true, "ready")
      } catch (error) {
        if (fileIndexRef.current.generation !== generation) return
        store.running = false
        const message = (error as Error).message || String(error)
        updateMeta(true, "error", message)
      }
    })()
  }, [
    fileIndexMeta.status,
    filePickerConfig.indexConcurrency,
    filePickerConfig.maxIndexFiles,
    filePickerConfig.mode,
    onListFiles,
    shouldIndexDirectory,
  ])

  const fuzzyNeedle = filePickerNeedle
  const lastFuzzyNeedleRef = useRef<string>("")
  useEffect(() => {
    if (!filePickerActive) {
      lastFuzzyNeedleRef.current = ""
      return
    }
    if (filePickerConfig.mode === "tree") {
      lastFuzzyNeedleRef.current = ""
      return
    }
    if (!fuzzyNeedle) return
    if (lastFuzzyNeedleRef.current === fuzzyNeedle) return
    lastFuzzyNeedleRef.current = fuzzyNeedle
    ensureFileIndexScan()
  }, [ensureFileIndexScan, filePickerActive, filePickerConfig.mode, fuzzyNeedle])

  const loadFilePickerDirectory = useCallback(
    async (cwd: string) => {
      const seq = (filePickerLoadSeq.current += 1)
      setFilePicker((prev) => ({
        status: "loading",
        cwd,
        items: prev.status === "hidden" ? [] : prev.items,
        index: 0,
      }))
      try {
        const scope = cwd === "." ? undefined : cwd
        const items = await onListFiles(scope)
        if (filePickerLoadSeq.current !== seq) return
        setFilePicker({
          status: "ready",
          cwd,
          items,
          index: 0,
        })
      } catch (error) {
        if (filePickerLoadSeq.current !== seq) return
        setFilePicker({
          status: "error",
          cwd,
          items: [],
          index: 0,
          message: (error as Error).message,
        })
      }
    },
    [onListFiles],
  )

  useEffect(() => {
    if (!filePickerActive) {
      closeFilePicker()
      return
    }
    if (filePicker.status === "hidden" || filePicker.cwd !== filePickerQueryParts.cwd) {
      void loadFilePickerDirectory(filePickerQueryParts.cwd)
    }
  }, [closeFilePicker, filePicker.status, filePicker.cwd, filePickerActive, filePickerQueryParts.cwd, loadFilePickerDirectory])

  const filePickerFilteredItems = useMemo(() => {
    if (!filePickerActive) return []
    const normalized = filePickerQueryParts.needle.trim().toLowerCase()
    const filtered = normalized
      ? filePicker.items.filter((item) => displayPathForCwd(item.path, filePicker.cwd).toLowerCase().includes(normalized))
      : [...filePicker.items]
    filtered.sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1
      const aDisplay = displayPathForCwd(a.path, filePicker.cwd).toLowerCase()
      const bDisplay = displayPathForCwd(b.path, filePicker.cwd).toLowerCase()
      return aDisplay.localeCompare(bDisplay)
    })
    return filtered
  }, [filePicker.cwd, filePicker.items, filePickerActive, filePickerQueryParts.needle])

  const filePickerIndex = useMemo(() => {
    if (!filePickerActive) return 0
    if (filePickerFilteredItems.length === 0) return 0
    return Math.max(0, Math.min(filePicker.index, filePickerFilteredItems.length - 1))
  }, [filePicker.index, filePickerActive, filePickerFilteredItems.length])

  const fileIndexItems = useMemo(() => {
    const store = fileIndexRef.current
    return [...store.dirs.values(), ...store.files.values()]
  }, [fileIndexMeta.version])

  const fileMenuMode = useMemo<"tree" | "fuzzy">(() => {
    if (!filePickerActive) return "tree"
    if (filePickerConfig.mode === "tree") return "tree"
    if (filePickerNeedle.trim().length === 0) return "tree"
    return "fuzzy"
  }, [filePickerActive, filePickerConfig.mode, filePickerNeedle])

  const fileMenuNeedle = fileMenuMode === "fuzzy" ? filePickerNeedle : filePickerQueryParts.needle

  useEffect(() => {
    if (!filePickerActive) {
      if (fileMenuItems.length > 0) {
        setFileMenuItems([])
      }
      fileMenuCacheRef.current = null
      return
    }
    if (fileMenuMode === "tree") {
      setFileMenuItems([...filePickerFilteredItems])
      fileMenuCacheRef.current = {
        key: `${filePickerQueryParts.cwd}|${fileMenuNeedle}`,
        status: fileIndexMeta.status,
        mode: "tree",
      }
      return
    }
    const key = `${filePickerQueryParts.cwd}|${fileMenuNeedle}`
    const previous = fileMenuCacheRef.current
    const shouldRefresh =
      !previous ||
      previous.mode !== "fuzzy" ||
      previous.key !== key ||
      (previous.status !== "ready" && fileIndexMeta.status === "ready")
    if (!shouldRefresh) return
    const cwd = filePickerQueryParts.cwd
    const prefix = cwd === "." ? "" : `${cwd}/`
    const candidates = prefix
      ? fileIndexItems.filter((item) => item.path === cwd || item.path.startsWith(prefix))
      : fileIndexItems
    const ranked = rankFuzzyFileItems(
      candidates,
      fileMenuNeedle,
      filePickerConfig.maxResults,
      (item) => displayPathForCwd(item.path, cwd),
    )
    setFileMenuItems([...ranked])
    fileMenuCacheRef.current = { key, status: fileIndexMeta.status, mode: "fuzzy" }
  }, [
    fileIndexItems,
    fileIndexMeta.status,
    fileMenuItems.length,
    fileMenuMode,
    filePickerActive,
    filePickerConfig.maxResults,
    filePickerFilteredItems,
    filePickerQueryParts.cwd,
    fileMenuNeedle,
  ])

  const fileMenuRows = useMemo<FileMenuRow[]>(() => {
    if (!filePickerActive) return []
    const rows: FileMenuRow[] = []
    for (const resource of filePickerResourcesVisible) {
      rows.push({ kind: "resource", resource })
    }
    for (const item of fileMenuItems) {
      rows.push({ kind: "file", item })
    }
    return rows
  }, [filePickerActive, fileMenuItems, filePickerResourcesVisible])

  const fileMenuIndex = useMemo(() => {
    if (!filePickerActive) return 0
    if (fileMenuRows.length === 0) return 0
    return Math.max(0, Math.min(filePicker.index, fileMenuRows.length - 1))
  }, [fileMenuRows.length, filePicker.index, filePickerActive])

  const fileMenuWindow = useMemo(() => {
    if (fileMenuRows.length === 0) {
      return { items: [] as FileMenuRow[], hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount: 0 }
    }
    const maxRows = fileMenuMaxRows
    if (fileMenuRows.length <= maxRows) {
      const lineCount = fileMenuRows.reduce(
        (sum, row) => sum + (row.kind === "resource" && row.resource.detail ? 2 : 1),
        0,
      )
      return { items: fileMenuRows, hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount }
    }
    const half = Math.floor(maxRows / 2)
    const start = Math.min(
      Math.max(0, fileMenuIndex - half),
      Math.max(0, fileMenuRows.length - maxRows),
    )
    const items = fileMenuRows.slice(start, start + maxRows)
    const lineCount = items.reduce(
      (sum, row) => sum + (row.kind === "resource" && row.resource.detail ? 2 : 1),
      0,
    )
    return {
      items,
      hiddenAbove: start,
      hiddenBelow: Math.max(0, fileMenuRows.length - (start + items.length)),
      start,
      lineCount,
    }
  }, [fileMenuIndex, fileMenuMaxRows, fileMenuRows])

  const lastFileMenuNeedleRef = useRef<string>("")
  useEffect(() => {
    if (!filePickerActive) return
    if (fileMenuNeedle === lastFileMenuNeedleRef.current) return
    lastFileMenuNeedleRef.current = fileMenuNeedle
    setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: 0 }))
  }, [fileMenuNeedle, filePickerActive])

  const insertFileMention = useCallback(
    (filePath: string, activeMention: ActiveAtMention) => {
      const mentionToken = /\s/.test(filePath) ? `@"${filePath}"` : `@${filePath}`
      if (!fileMentionConfig.insertPath) {
        const before = input.slice(0, activeMention.start)
        const after = input.slice(activeMention.end)
        const trimmedAfter = before.endsWith(" ") && after.startsWith(" ") ? after.slice(1) : after
        const nextValue = `${before}${trimmedAfter}`
        handleLineEdit(nextValue, before.length)
        return
      }
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const trail = after.length === 0 || !/^\s/.test(after) ? " " : ""
      const inserted = `${mentionToken}${trail}`
      const nextValue = `${before}${inserted}${after}`
      handleLineEdit(nextValue, before.length + inserted.length)
    },
    [fileMentionConfig.insertPath, handleLineEdit, input],
  )

  const insertDirectoryMention = useCallback(
    (dirPath: string, activeMention: ActiveAtMention) => {
      const normalized = dirPath.replace(/\/+$/, "")
      const withSlash = normalized ? `${normalized}/` : `${dirPath}/`
      const mentionToken = /\s/.test(withSlash) ? `@"${withSlash}"` : `@${withSlash}`
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const nextValue = `${before}${mentionToken}${after}`
      handleLineEdit(nextValue, before.length + mentionToken.length)
    },
    [handleLineEdit, input],
  )

  const insertResourceMention = useCallback(
    (resource: FilePickerResource, activeMention: ActiveAtMention) => {
      const label = resource.label.trim()
      const tokenBody = /\s/.test(label) ? `resource:\"${label}\"` : `resource:${label}`
      const mentionToken = `@${tokenBody}`
      const before = input.slice(0, activeMention.start)
      const after = input.slice(activeMention.end)
      const trail = after.length === 0 || !/^\s/.test(after) ? " " : ""
      const inserted = `${mentionToken}${trail}`
      const nextValue = `${before}${inserted}${after}`
      handleLineEdit(nextValue, before.length + inserted.length)
    },
    [handleLineEdit, input],
  )

  const queueFileMention = useCallback(
    (file: SessionFileInfo) => {
      if (fileMentionConfig.mode === "reference") return
      const now = Date.now()
      setFileMentions((prev) => {
        const existingIndex = prev.findIndex((entry) => entry.path === file.path)
        const next = existingIndex >= 0 ? prev.filter((_, idx) => idx !== existingIndex) : [...prev]
        next.push({
          id: `file-mention-${now.toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
          path: file.path,
          size: file.size ?? null,
          requestedMode: fileMentionConfig.mode,
          addedAt: now,
        })
        return next
      })
    },
    [fileMentionConfig.mode],
  )

  const openPalette = useCallback(() => {
    setPaletteState({ status: "open", query: "", index: 0 })
  }, [])

  const closePalette = useCallback(() => {
    setPaletteState((prev) => (prev.status === "hidden" ? prev : { status: "hidden", query: "", index: 0 }))
  }, [])

  const openConfirm = useCallback((message: string, action: () => Promise<void> | void) => {
    setConfirmState({ status: "prompt", message, action })
  }, [])

  const closeConfirm = useCallback(() => {
    setConfirmState({ status: "hidden", message: undefined, action: null })
  }, [])

  const pushHistoryEntry = useCallback((entry: string) => {
    if (!entry.trim()) return
    setHistoryEntries((prev) => {
      if (entry === prev[prev.length - 1]) {
        setHistoryPos(prev.length)
        return prev
      }
      const next = [...prev, entry]
      setHistoryPos(next.length)
      return next
    })
    historyDraftRef.current = ""
  }, [])

  const runConfirmAction = useCallback(() => {
    const action = confirmState.action
    closeConfirm()
    if (action) void action()
  }, [closeConfirm, confirmState.action])

  const handleOverlayKeys = useCallback<KeyHandler>(
    function handleOverlayKeys(char, key): boolean {
      if (
        typeof char === "string" &&
        char.length > 1 &&
        !key.ctrl &&
        !key.meta &&
        !key.shift &&
        !key.tab &&
        !key.return &&
        !key.escape &&
        !key.backspace &&
        !key.delete &&
        !key.upArrow &&
        !key.downArrow &&
        !key.leftArrow &&
        !key.rightArrow &&
        !key.pageUp &&
        !key.pageDown &&
        !char.includes("\u001b")
      ) {
        let handled = false
        for (const ch of char) {
          handled = handleOverlayKeys(ch, key) || handled
        }
        return handled
      }
      const lowerChar = char?.toLowerCase()
      const isReturnKey = key.return || char === "\r" || char === "\n"
      const hasTabChar = typeof char === "string" && char.includes("\t")
      const hasShiftTabChar = typeof char === "string" && char.includes("\u001b[Z")
      const isTabKey = key.tab || hasTabChar || hasShiftTabChar
      const isShiftTab = (key.shift && isTabKey) || hasShiftTabChar
      const isCtrlT = key.ctrl && lowerChar === "t"
      const isCtrlShiftT = key.ctrl && key.shift && lowerChar === "t"
      if (shortcutsOpen && (char === "?" || key.escape)) {
        setShortcutsOpen(false)
        return true
      }
      if (isCtrlShiftT) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlT) {
        if (keymap === "claude") {
          setTodosOpen((prev) => !prev)
        } else if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (key.ctrl && (lowerChar === "c" || char === "\u0003")) {
        const now = Date.now()
        if (ctrlCPrimedAt && now - ctrlCPrimedAt < DOUBLE_CTRL_C_WINDOW_MS) {
          setCtrlCPrimedAt(null)
          void onSubmit("/quit")
          process.exit(0)
          return true
        }
        setCtrlCPrimedAt(now)
        return true
      }
      if (key.ctrl && lowerChar === "d") {
        void onSubmit("/quit")
        process.exit(0)
        return true
      }
      if (key.ctrl && lowerChar === "z") {
        if (inputValueRef.current.trim().length === 0) {
          try {
            process.kill(process.pid, "SIGTSTP")
          } catch {
            // ignore
          }
        }
        return true
      }
      if (key.ctrl && lowerChar === "d") {
        void onSubmit("/quit")
        process.exit(0)
        return true
      }
      if (key.ctrl && lowerChar === "z") {
        if (inputValueRef.current.trim().length === 0) {
          try {
            process.kill(process.pid, "SIGTSTP")
          } catch {
            // ignore
          }
        }
        return true
      }
      if (transcriptViewerOpen) {
        if (key.escape || char === "\u001b") {
          exitTranscriptViewer()
          return true
        }
        if (stdout?.isTTY && char && char.startsWith("[<")) {
          const match = char.match(/^\[<(\d+);(\d+);(\d+)([mM])$/)
          if (match) {
            const code = Number.parseInt(match[1] ?? "", 10)
            if (Number.isFinite(code) && (code & 64) === 64) {
              const down = (code & 1) === 1
              const delta = down ? 3 : -3
              setTranscriptViewerFollowTail(false)
              setTranscriptViewerScroll((prev) => {
                const base = transcriptViewerFollowTail ? transcriptViewerMaxScroll : prev
                return Math.max(0, Math.min(transcriptViewerMaxScroll, base + delta))
              })
              return true
            }
          }
        }
        if (transcriptSearchOpen) {
          if (isReturnKey || isTabKey) {
            if (transcriptSearchMatches.length > 0) {
              const direction = isShiftTab ? -1 : 1
              setTranscriptSearchIndex((prev) => {
                const count = transcriptSearchMatches.length
                const next = count === 0 ? 0 : (prev + direction + count) % count
                const line = transcriptSearchMatches[next]?.line
                if (typeof line === "number") {
                  jumpTranscriptToLine(line)
                }
                return next
              })
            }
            return true
          }
          if (key.upArrow || key.downArrow) {
            if (transcriptSearchMatches.length > 0) {
              const direction = key.upArrow ? -1 : 1
              setTranscriptSearchIndex((prev) => {
                const count = transcriptSearchMatches.length
                const next = count === 0 ? 0 : (prev + direction + count) % count
                const line = transcriptSearchMatches[next]?.line
                if (typeof line === "number") {
                  jumpTranscriptToLine(line)
                }
                return next
              })
            }
            return true
          }
          if (key.backspace || key.delete) {
            setTranscriptSearchQuery((prev) => prev.slice(0, -1))
            return true
          }
          if (key.ctrl && lowerChar === "u") {
            setTranscriptSearchQuery("")
            setTranscriptSearchIndex(0)
            return true
          }
          if (char && char.length > 0 && !key.ctrl && !key.meta) {
            setTranscriptSearchQuery((prev) => prev + char)
            return true
          }
        }
        if (lowerChar === "/") {
          setTranscriptSearchOpen((prev) => !prev)
          return true
        }
        const scrollBy = (delta: number) => {
          setTranscriptViewerFollowTail(false)
          setTranscriptViewerScroll((prev) => {
            const base = transcriptViewerFollowTail ? transcriptViewerMaxScroll : prev
            return Math.max(0, Math.min(transcriptViewerMaxScroll, base + delta))
          })
        }
        if (key.pageUp || (key.ctrl && lowerChar === "b")) {
          scrollBy(-transcriptViewerBodyRows)
          return true
        }
        if (key.pageDown || (key.ctrl && lowerChar === "f")) {
          scrollBy(transcriptViewerBodyRows)
          return true
        }
        if (key.upArrow) {
          scrollBy(-1)
          return true
        }
        if (key.downArrow) {
          const next = Math.min(transcriptViewerMaxScroll, transcriptViewerEffectiveScroll + 1)
          if (next >= transcriptViewerMaxScroll) {
            setTranscriptViewerFollowTail(true)
            setTranscriptViewerScroll(transcriptViewerMaxScroll)
          } else {
            setTranscriptViewerFollowTail(false)
            setTranscriptViewerScroll(next)
          }
          return true
        }
        if (lowerChar === "g" && key.shift) {
          setTranscriptViewerFollowTail(true)
          setTranscriptViewerScroll(transcriptViewerMaxScroll)
          return true
        }
        if (lowerChar === "g") {
          setTranscriptViewerFollowTail(false)
          setTranscriptViewerScroll(0)
          return true
        }
        return true
      }
      if (permissionRequest) {
        const tabOrder: Array<"summary" | "diff" | "rules"> = ["summary", "diff", "rules"]
        let activeTab = permissionTabRef.current ?? permissionTab
        const currentIndex = tabOrder.indexOf(activeTab as "summary" | "diff" | "rules")
        const nextTab = () => tabOrder[currentIndex >= 0 ? (currentIndex + 1) % tabOrder.length : 0] ?? "summary"
        const finalizePermissionDecision = (decision: PermissionDecision) => {
          if (permissionDecisionTimerRef.current) {
            clearTimeout(permissionDecisionTimerRef.current)
          }
          const snapshot = permissionInputSnapshotRef.current
          permissionDecisionTimerRef.current = setTimeout(() => {
            const latestNote = permissionNoteRef.current.trim()
            const snapshotValue = snapshot?.value.trim() ?? ""
            const currentInput = inputValueRef.current.trim()
            const fallbackNote =
              !latestNote && activeTab === "note" && currentInput && currentInput !== snapshotValue ? currentInput : ""
            const notePayload = latestNote || fallbackNote ? { note: latestNote || fallbackNote } : {}
            void onPermissionDecision({ ...decision, ...notePayload })
            if (snapshot) {
              handleLineEdit(snapshot.value, snapshot.cursor)
            }
            permissionInputSnapshotRef.current = null
            if (permissionNoteRef.current) {
              setPermissionNote("")
              setPermissionNoteCursor(0)
            }
            permissionDecisionTimerRef.current = null
          }, 100)
        }

        if (isTabKey) {
          if (isShiftTab) {
            finalizePermissionDecision({
              kind: "allow-always",
              scope: permissionScope,
              rule: permissionRequest.ruleSuggestion ?? null,
            })
            return true
          }
          const next = nextTab()
          permissionTabRef.current = next
          setPermissionTab(next)
          return true
        }
        const isPrintable =
          char &&
          char.length > 0 &&
          !key.ctrl &&
          !key.meta &&
          !key.return &&
          !key.escape &&
          !key.backspace &&
          !key.delete
        if (
          isPrintable &&
          activeTab !== "note" &&
          lowerChar !== "a" &&
          lowerChar !== "d" &&
          lowerChar !== "p" &&
          lowerChar !== "1" &&
          lowerChar !== "2" &&
          lowerChar !== "3" &&
          char !== "D"
        ) {
          permissionTabRef.current = "note"
          setPermissionTab("note")
          activeTab = "note"
        }
        if (key.escape) {
          finalizePermissionDecision({ kind: "deny-stop" })
          return true
        }
        if (activeTab === "note") {
          if (isReturnKey) {
            finalizePermissionDecision({ kind: "deny-once" })
            return true
          }
          if (key.leftArrow) {
            setPermissionNoteCursor((prev) => Math.max(0, prev - 1))
            return true
          }
          if (key.rightArrow) {
            setPermissionNoteCursor((prev) => Math.min(permissionNote.length, prev + 1))
            return true
          }
          if (key.backspace) {
            if (permissionNoteCursor > 0) {
              const next = permissionNote.slice(0, permissionNoteCursor - 1) + permissionNote.slice(permissionNoteCursor)
              setPermissionNote(next)
              setPermissionNoteCursor(permissionNoteCursor - 1)
            }
            return true
          }
          if (key.delete) {
            if (permissionNoteCursor < permissionNote.length) {
              const next = permissionNote.slice(0, permissionNoteCursor) + permissionNote.slice(permissionNoteCursor + 1)
              setPermissionNote(next)
            }
            return true
          }
          if (key.ctrl && lowerChar === "u") {
            setPermissionNote("")
            setPermissionNoteCursor(0)
            return true
          }
          if (char && char.length > 0 && !key.ctrl && !key.meta) {
            const next = permissionNote.slice(0, permissionNoteCursor) + char + permissionNote.slice(permissionNoteCursor)
            setPermissionNote(next)
            setPermissionNoteCursor(permissionNoteCursor + char.length)
            return true
          }
          return true
        }
        if (lowerChar === "1") {
          finalizePermissionDecision({ kind: "allow-once" })
          return true
        }
        if (lowerChar === "2") {
          finalizePermissionDecision({
            kind: "allow-always",
            scope: permissionScope,
            rule: permissionRequest.ruleSuggestion ?? null,
          })
          return true
        }
        if (lowerChar === "3") {
          permissionTabRef.current = "note"
          setPermissionTab("note")
          return true
        }
        if (isReturnKey) {
          finalizePermissionDecision({ kind: "allow-once" })
          return true
        }
        if (lowerChar === "a") {
          finalizePermissionDecision({
            kind: "allow-always",
            scope: permissionScope,
            rule: permissionRequest.ruleSuggestion ?? null,
          })
          return true
        }
        if (char === "D") {
          finalizePermissionDecision({
            kind: "deny-always",
            scope: permissionScope,
            rule: permissionRequest.ruleSuggestion ?? null,
          })
          return true
        }
        if (lowerChar === "d") {
          finalizePermissionDecision({ kind: "deny-once" })
          return true
        }
        if (lowerChar === "p") {
          permissionTabRef.current = "diff"
          setPermissionTab("diff")
          return true
        }
        if (activeTab === "rules") {
          if (key.leftArrow || key.rightArrow) {
            const order: PermissionRuleScope[] = ["session", "project", "global"]
            const index = Math.max(0, order.indexOf(permissionScope))
            const nextIndex = key.leftArrow ? (index - 1 + order.length) % order.length : (index + 1) % order.length
            setPermissionScope(order[nextIndex] ?? "project")
            return true
          }
          return true
        }
        if (activeTab === "diff") {
          const maxIndex = Math.max(0, permissionDiffSections.length - 1)
          if (key.leftArrow) {
            setPermissionFileIndex((prev) => Math.max(0, prev - 1))
            setPermissionScroll(0)
            return true
          }
          if (key.rightArrow) {
            setPermissionFileIndex((prev) => Math.min(maxIndex, prev + 1))
            setPermissionScroll(0)
            return true
          }
          if (key.pageUp) {
            setPermissionScroll((prev) => Math.max(0, prev - permissionViewportRows))
            return true
          }
          if (key.pageDown) {
            setPermissionScroll((prev) => prev + permissionViewportRows)
            return true
          }
          if (key.upArrow) {
            setPermissionScroll((prev) => Math.max(0, prev - 1))
            return true
          }
          if (key.downArrow) {
            setPermissionScroll((prev) => prev + 1)
            return true
          }
          return true
        }
        return true
      }
      if (todosOpen) {
        if (key.escape || char === "\u001b") {
          setTodosOpen(false)
          return true
        }
        if (isCtrlT && keymap === "claude") {
          setTodosOpen(false)
          return true
        }
        const clampScroll = (value: number) => Math.max(0, Math.min(todoMaxScroll, value))
        if (key.pageUp) {
          setTodoScroll((prev) => clampScroll(prev - todoViewportRows))
          return true
        }
        if (key.pageDown) {
          setTodoScroll((prev) => clampScroll(prev + todoViewportRows))
          return true
        }
        if (key.upArrow) {
          setTodoScroll((prev) => clampScroll(prev - 1))
          return true
        }
        if (key.downArrow) {
          setTodoScroll((prev) => clampScroll(prev + 1))
          return true
        }
        return true
      }
      if (rewindMenu.status !== "hidden") {
        const checkpoints = rewindMenu.checkpoints
        const lowerChar = char?.toLowerCase()
        if (key.escape) {
          onRewindClose()
          return true
        }
        if (key.upArrow) {
          setRewindIndex((prev) => (checkpoints.length === 0 ? 0 : Math.max(0, prev - 1)))
          return true
        }
        if (key.downArrow) {
          setRewindIndex((prev) =>
            checkpoints.length === 0 ? 0 : Math.min(checkpoints.length - 1, prev + 1),
          )
          return true
        }
        const current = checkpoints[Math.max(0, Math.min(rewindIndex, checkpoints.length - 1))]
        if (current) {
          if (lowerChar === "1") {
            void onRewindRestore(current.checkpointId, "conversation")
            return true
          }
          if (lowerChar === "2") {
            void onRewindRestore(current.checkpointId, "code")
            return true
          }
          if (lowerChar === "3") {
            void onRewindRestore(current.checkpointId, "both")
            return true
          }
        }
        return true
      }
      if (confirmState.status === "prompt") {
        if (key.escape) {
          closeConfirm()
          return true
        }
        if (isReturnKey) {
          runConfirmAction()
          return true
        }
        return true
      }
      if (modelMenu.status === "hidden") return false
      if (key.escape) {
        onModelMenuCancel()
        return true
      }
      if (modelMenu.status === "loading") {
        return true
      }
      if (modelMenu.status === "error") {
        if (key.return) onModelMenuCancel()
        return true
      }
      if (modelMenu.status !== "ready") {
        return true
      }
      if (isReturnKey) {
        const choice = filteredModels[modelIndex]
        if (choice) void onModelSelect(choice)
        return true
      }
      if ((key.backspace || key.delete) && modelSearch.length > 0) {
        setModelSearch((prev) => prev.slice(0, -1))
        setModelIndex(0)
        setModelOffset(0)
        return true
      }
      const count = filteredModels.length
      if (count > 0) {
        if (key.downArrow || isTabKey) {
          setModelIndex((index) => {
            const next = (index + 1) % count
            setModelOffset((offset) => {
              if (next < offset) return next
              if (next >= offset + MAX_VISIBLE_MODELS) return Math.max(0, next - MAX_VISIBLE_MODELS + 1)
              return offset
            })
            return next
          })
          return true
        }
        if (key.upArrow || isShiftTab) {
          setModelIndex((index) => {
            const next = (index - 1 + count) % count
            setModelOffset((offset) => {
              if (next < offset) return next
              if (next >= offset + MAX_VISIBLE_MODELS) return Math.max(0, next - MAX_VISIBLE_MODELS + 1)
              return offset
            })
            return next
          })
          return true
        }
      }
      if (char && char.length > 0 && !key.meta && !key.ctrl) {
        setModelSearch((prev) => prev + char)
        setModelIndex(0)
        setModelOffset(0)
        return true
      }
      return true
    },
    [
      ctrlCPrimedAt,
      closeConfirm,
      confirmState.status,
      enterTranscriptViewer,
      exitTranscriptViewer,
      filteredModels,
      jumpTranscriptToLine,
      modelIndex,
      modelMenu.status,
      modelSearch.length,
      keymap,
      onModelMenuCancel,
      onPermissionDecision,
      onRewindClose,
      onRewindRestore,
      onModelSelect,
      stdout,
      permissionDiffSections.length,
      permissionNote,
      permissionNoteCursor,
      permissionRequest,
      permissionScope,
      permissionTab,
      todoMaxScroll,
      todoViewportRows,
      todosOpen,
      rewindIndex,
      rewindMenu,
      runConfirmAction,
      shortcutsOpen,
      onSubmit,
      transcriptSearchMatches,
      transcriptSearchOpen,
      transcriptSearchQuery,
      transcriptSearchSafeIndex,
      transcriptViewerBodyRows,
      transcriptViewerEffectiveScroll,
      transcriptViewerFollowTail,
      transcriptViewerMaxScroll,
      transcriptViewerOpen,
    ],
  )

  const applySuggestion = useCallback(
    (choice?: SlashSuggestion) => {
      if (!choice) return
      const beforeCursor = input.slice(0, cursor)
      const afterCursor = input.slice(cursor)
      let replaceStart = beforeCursor.lastIndexOf(" ")
      replaceStart = replaceStart === -1 ? 0 : replaceStart + 1
      if (beforeCursor[replaceStart] !== "/") {
        replaceStart = 0
      }
      const prefix = input.slice(0, replaceStart)
      const inserted = `${choice.command}${choice.usage ? ` ${choice.usage}` : ""}`
      const newValue = `${prefix}${inserted}${afterCursor}`
      const newCursor = prefix.length + inserted.length
      handleLineEdit(newValue, newCursor)
      setSuggestIndex(0)
    },
    [cursor, handleLineEdit, input],
  )

  const applyPaletteItem = useCallback(
    (item?: SlashCommandInfo) => {
      if (!item) return
      const inserted = `/${item.name}${item.usage ? " " : " "}`
      handleLineEdit(inserted, inserted.length)
      closePalette()
    },
    [closePalette, handleLineEdit],
  )

  const recallHistory = useCallback(
    (direction: -1 | 1) => {
      if (historyEntries.length === 0) return
      setHistoryPos((prev) => {
        const length = historyEntries.length
        let next = prev + direction
        next = Math.max(0, Math.min(length, next))
        if (next === prev) return prev
        if (prev === length) {
          historyDraftRef.current = input
        }
        if (next === length) {
          handleLineEdit(historyDraftRef.current, historyDraftRef.current.length)
        } else {
          const entry = historyEntries[next]
          handleLineEdit(entry, entry.length)
        }
        return next
      })
    },
    [handleLineEdit, historyEntries, input],
  )

  const handleEditorKeys = useCallback<KeyHandler>(
    (char, key) => {
      const isTabKey = key.tab || (typeof char === "string" && (char.includes("\t") || char.includes("\u001b[Z")))
      const isReturnKey = key.return || char === "\r" || char === "\n"
      const lowerChar = char?.toLowerCase()
      if (!key.ctrl && !key.meta && lowerChar === "?" && inputValueRef.current.trim() === "") {
        setShortcutsOpen(true)
        handleLineEdit("", 0)
        return true
      }
      if (key.ctrl && lowerChar === "s") {
        const stashValue = inputValueRef.current
        if (stashValue.trim().length > 0) {
          pushHistoryEntry(stashValue)
          handleLineEdit("", 0)
        }
        return true
      }
      if (modelMenu.status !== "hidden") {
        return false
      }
      if (filePickerActive) {
        if (key.escape) {
          if (pendingResponse) return false
          if (key.meta) {
            setEscPrimedAt(null)
            handleLineEdit("", 0)
            closeFilePicker()
            return true
          }
          setEscPrimedAt(Date.now())
          if (activeAtMention) {
            setFilePickerDismissed({ tokenStart: activeAtMention.start, textVersion: inputTextVersion })
          }
          closeFilePicker()
          return true
        }
        const menuStatus =
          fileMenuMode === "tree"
            ? filePicker.status
            : fileIndexMeta.status === "idle"
              ? "scanning"
              : fileIndexMeta.status
        if (menuStatus === "loading" || menuStatus === "scanning") {
          if (fileMenuRows.length === 0) return true
        }
        if (menuStatus === "error") {
          if (isTabKey) {
            if (fileMenuMode === "tree") {
              void loadFilePickerDirectory(filePickerQueryParts.cwd)
            } else {
              ensureFileIndexScan()
            }
            return true
          }
          return true
        }
        if (key.upArrow) {
          const count = fileMenuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex - 1 + count) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (key.downArrow) {
          const count = fileMenuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex + 1) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (isTabKey) {
          if (!activeAtMention) return true
          const count = fileMenuRows.length
          if (count === 0) return true

          const resolvedIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
          const current = fileMenuRows[resolvedIndex]
          if (!current) return true
          if (current.kind === "resource") {
            insertResourceMention(current.resource, activeAtMention)
            closeFilePicker()
            return true
          }

          const completionCandidates = fileMenuRows
            .filter((row) => row.kind === "file")
            .map((row) => {
              const item = row.item
              return item.type === "directory" ? `${item.path.replace(/\/+$/, "")}/` : item.path
            })
          const commonPrefix = longestCommonPrefix(completionCandidates)
          const rawQuery = activeAtMention.query ?? ""
          const leadingDot = rawQuery.match(/^\.\/+/)?.[0] ?? ""
          const normalizedQuery = rawQuery.replace(/^\.\/+/, "")

          if (commonPrefix && commonPrefix.length > normalizedQuery.length) {
            const tokenContentStart = activeAtMention.start + (activeAtMention.quoted ? 2 : 1)
            const afterCursor = input.slice(cursor)
            const nextQuery = `${leadingDot}${commonPrefix}`
            const nextValue = `${input.slice(0, tokenContentStart)}${nextQuery}${afterCursor}`
            handleLineEdit(nextValue, tokenContentStart + nextQuery.length)
            filePickerIndexRef.current = 0
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: 0 }))
            return true
          }

          if (current.item.type === "directory") {
            insertDirectoryMention(current.item.path, activeAtMention)
            setFilePickerDismissed({ tokenStart: activeAtMention.start, textVersion: inputTextVersion + 1 })
            closeFilePicker()
            return true
          }

          insertFileMention(current.item.path, activeAtMention)
          queueFileMention(current.item)
          closeFilePicker()
          return true
        }
        return false
      }
      if (suggestions.length > 0) {
        if (key.downArrow) {
          setSuggestIndex((index) => (index + 1) % suggestions.length)
          return true
        }
        if (key.upArrow) {
          setSuggestIndex((index) => (index - 1 + suggestions.length) % suggestions.length)
          return true
        }
        if (isReturnKey && !key.shift) {
          const trimmed = inputValueRef.current.trim()
          const isSlash = trimmed.startsWith("/")
          if (isSlash) {
            const body = trimmed.slice(1).trim()
            const [commandName] = body.split(/\s+/)
            const isExactCommand = Boolean(commandName) && SLASH_COMMANDS.some((cmd) => cmd.name === commandName)
            if (isExactCommand) {
              return false
            }
          }
          const choice = suggestions[Math.max(0, Math.min(suggestIndex, suggestions.length - 1))]
          applySuggestion(choice)
          return true
        }
        if (isTabKey) {
          if (key.shift) {
            setSuggestIndex((index) => (index - 1 + suggestions.length) % suggestions.length)
            return true
          }
          const choice = suggestions[Math.max(0, Math.min(suggestIndex, suggestions.length - 1))]
          applySuggestion(choice)
          return true
        }
        return false
      }
      if (key.upArrow || (key.ctrl && lowerChar === "p" && keymap !== "claude")) {
        recallHistory(-1)
        return true
      }
      if (key.downArrow || (key.ctrl && lowerChar === "n")) {
        recallHistory(1)
        return true
      }
      return false
    },
    [
      activeAtMention,
      applySuggestion,
      closeFilePicker,
      ensureFileIndexScan,
      fileIndexMeta.status,
      fileMenuIndex,
      fileMenuRows,
      fileMenuMode,
      filePicker.status,
      filePickerActive,
      filePickerQueryParts.cwd,
      insertDirectoryMention,
      insertFileMention,
      insertResourceMention,
      handleLineEdit,
      inputTextVersion,
      keymap,
      loadFilePickerDirectory,
      modelMenu.status,
      pendingResponse,
      pushHistoryEntry,
      queueFileMention,
      recallHistory,
      setShortcutsOpen,
      suggestIndex,
      suggestions,
    ],
  )

  const handlePaletteKeys = useCallback<KeyHandler>(
    (char, key) => {
      const isTabKey = key.tab || (typeof char === "string" && (char.includes("\t") || char.includes("\u001b[Z")))
      if (paletteState.status !== "open") return false
      const lowerChar = char?.toLowerCase()
      const isCtrlT = key.ctrl && lowerChar === "t"
      const isCtrlShiftT = key.ctrl && key.shift && lowerChar === "t"
      if (isCtrlShiftT) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlT) {
        if (keymap === "claude") {
          setTodosOpen((prev) => !prev)
        } else if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (key.ctrl && (lowerChar === "c" || char === "\u0003")) {
        const now = Date.now()
        if (ctrlCPrimedAt && now - ctrlCPrimedAt < DOUBLE_CTRL_C_WINDOW_MS) {
          setCtrlCPrimedAt(null)
          void onSubmit("/quit")
          process.exit(0)
          return true
        }
        setCtrlCPrimedAt(now)
        return true
      }
      if (key.escape) {
        closePalette()
        return true
      }
      if (key.return) {
        applyPaletteItem(paletteItems[Math.max(0, Math.min(paletteState.index, paletteItems.length - 1))])
        return true
      }
      if (paletteItems.length > 0 && (key.downArrow || isTabKey)) {
        setPaletteState((prev) => ({
          ...prev,
          index: (prev.index + 1) % paletteItems.length,
        }))
        return true
      }
      if (paletteItems.length > 0 && key.upArrow) {
        setPaletteState((prev) => ({
          ...prev,
          index: (prev.index - 1 + paletteItems.length) % paletteItems.length,
        }))
        return true
      }
      if (key.backspace) {
        setPaletteState((prev) => ({
          ...prev,
          query: prev.query.slice(0, -1),
          index: 0,
        }))
        return true
      }
      if (char && !key.ctrl && !key.meta) {
        setPaletteState((prev) => ({
          ...prev,
          query: prev.query + char,
          index: 0,
        }))
        return true
      }
      return true
    },
    [
      applyPaletteItem,
      closePalette,
      ctrlCPrimedAt,
      enterTranscriptViewer,
      exitTranscriptViewer,
      keymap,
      paletteItems,
      paletteState.index,
      paletteState.status,
      onSubmit,
      transcriptViewerOpen,
    ],
  )

  const handleAttachment = useCallback((attachment: ClipboardImage) => {
    setAttachments((prev) => [
      ...prev,
      {
        id: `attachment-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        mime: attachment.mime,
        base64: attachment.base64,
        size: attachment.size,
      },
    ])
  }, [])

  const handleLineSubmit = useCallback(
    async (value: string) => {
      const trimmed = value.trim()
      if (!trimmed || inputLocked) return
      const normalized = value.trimEnd()
      if (trimmed.startsWith("/")) {
        const [command] = trimmed.slice(1).split(/\s+/)
        if (command === "todos") {
          setTodosOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "transcript") {
          enterTranscriptViewer()
          handleLineEdit("", 0)
          return
        }
      }
      const command = parseAtCommand(value)
      if (command) {
        await handleAtCommand(command)
        return
      }
      const segments: string[] = [normalized]
      if (attachments.length > 0) {
        const summaryLines = attachments.map(
          (attachment, index) => `[Attachment ${index + 1}: ${attachment.mime} ${formatBytes(attachment.size)}]`,
        )
        segments.push(summaryLines.join("\n"))
      }
      if (fileMentions.length > 0 && fileMentionConfig.mode !== "reference") {
        let remainingInlineBudget = fileMentionConfig.maxInlineBytesTotal
        const blocks: string[] = []
        for (const entry of fileMentions) {
          if (entry.requestedMode === "reference") continue
          const sizeBytes = entry.size ?? null
          const shouldInline =
            entry.requestedMode === "inline"
              ? sizeBytes == null ||
                (sizeBytes <= fileMentionConfig.maxInlineBytesPerFile && sizeBytes <= remainingInlineBudget)
              : sizeBytes != null &&
                sizeBytes <= fileMentionConfig.maxInlineBytesPerFile &&
                sizeBytes <= remainingInlineBudget

          const fetchMode: "cat" | "snippet" = shouldInline ? "cat" : "snippet"
          let content = ""
          let truncated = fetchMode === "snippet"
          try {
            const result = await onReadFile(
              entry.path,
              fetchMode === "cat"
                ? { mode: "cat" }
                : {
                    mode: "snippet",
                    headLines: fileMentionConfig.snippetHeadLines,
                    tailLines: fileMentionConfig.snippetTailLines,
                    maxBytes: fileMentionConfig.snippetMaxBytes,
                  },
            )
            content = normalizeNewlines(result.content ?? "")
            truncated = truncated || result.truncated === true
          } catch (error) {
            const message = (error as Error).message || String(error)
            blocks.push(
              `### File: ${entry.path}${sizeBytes != null ? ` (${formatSizeDetail(sizeBytes)})` : ""}\n\n(Unable to attach file contents: ${message}. You may read and search through the full file using your provided tools.)`,
            )
            continue
          }

          const byteCount = measureBytes(content)
          if (fetchMode === "cat") {
            remainingInlineBudget = Math.max(0, remainingInlineBudget - byteCount)
          }
          if (fetchMode === "cat" && byteCount > fileMentionConfig.maxInlineBytesPerFile) {
            content = makeSnippet(content, fileMentionConfig.snippetHeadLines, fileMentionConfig.snippetTailLines)
            truncated = true
          }
          if (fetchMode === "snippet") {
            const snippetContent = makeSnippet(content, fileMentionConfig.snippetHeadLines, fileMentionConfig.snippetTailLines)
            content = snippetContent.slice(0, fileMentionConfig.snippetMaxBytes)
            truncated = true
          }

          const headerSize = formatSizeDetail(sizeBytes)
          const header = `### File: ${entry.path}${headerSize ? ` (${headerSize})` : ""}`
          const fenceLang = guessFenceLang(entry.path)
          const blockParts: string[] = [header, `\`\`\`${fenceLang}\n${content}\n\`\`\``]
          if (truncated) {
            blockParts.push(
              `…This file is truncated, as it is too large${headerSize ? ` (${headerSize})` : ""} to fully display. You may read and search through the full file using your provided tools.`,
            )
          }
          blocks.push(blockParts.join("\n\n"))
        }
        if (blocks.length > 0) {
          segments.push(blocks.join("\n\n"))
        }
      }
      await onSubmit(segments.join("\n\n"), attachments)
      if (attachments.length > 0) {
        setAttachments([])
      }
      if (fileMentions.length > 0) {
        setFileMentions([])
      }
      pushHistoryEntry(normalized)
      handleLineEdit("", 0)
      setSuggestIndex(0)
    },
    [
      attachments,
      enterTranscriptViewer,
      fileMentionConfig,
      fileMentions,
      handleAtCommand,
      handleLineEdit,
      inputLocked,
      onReadFile,
      onSubmit,
      pushHistoryEntry,
      setTodosOpen,
    ],
  )

  useEffect(() => {
    if (filteredModels.length === 0) {
      setModelIndex(0)
      setModelOffset(0)
      return
    }
    if (modelIndex >= filteredModels.length) {
      setModelIndex(0)
      setModelOffset(0)
      return
    }
    if (modelIndex < modelOffset) {
      setModelOffset(modelIndex)
    } else if (modelIndex >= modelOffset + MAX_VISIBLE_MODELS) {
      setModelOffset(modelIndex - MAX_VISIBLE_MODELS + 1)
    }
  }, [filteredModels.length, modelIndex, modelOffset])

  const visibleModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    return filteredModels.slice(modelOffset, modelOffset + MAX_VISIBLE_MODELS)
  }, [filteredModels, modelMenu.status, modelOffset])
  const visibleModelRows = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const rows: Array<{ kind: "header"; label: string } | { kind: "item"; item: ModelMenuItem; index: number }> = []
    for (let idx = 0; idx < visibleModels.length; idx += 1) {
      const item = visibleModels[idx]
      const globalIndex = modelOffset + idx
      const prev = filteredModels[globalIndex - 1]
      if (idx === 0 || !prev || prev.provider !== item.provider) {
        rows.push({ kind: "header", label: item.provider || "other" })
      }
      rows.push({ kind: "item", item, index: globalIndex })
    }
    return rows
  }, [filteredModels, modelMenu.status, modelOffset, visibleModels])

  const headerLines = useMemo(
    () => ["", ...ASCII_HEADER.map((line) => applyForegroundGradient(line, Gradients.crush, true))],
    [],
  )
  const headerSubtitleLines = useMemo(() => {
    if (!claudeChrome) {
      return [chalk.cyan("breadboard — interactive session")]
    }
    const modelLine = stats.model ? `${stats.model} · API Usage Billing` : "Model unknown · API Usage Billing"
    const cwdLine = process.cwd()
    return [
      chalk.cyan(`breadboard v${CLI_VERSION}`),
      chalk.dim(modelLine),
      chalk.dim(cwdLine),
    ]
  }, [claudeChrome, stats.model])
  const promptRule = useMemo(
    () => "─".repeat(contentWidth),
    [contentWidth],
  )
  const compactMode = viewPrefs.virtualization === "compact" || (viewPrefs.virtualization === "auto" && rowCount <= 32)

  const headerReserveRows = useMemo(() => (SCROLLBACK_MODE ? 0 : headerLines.length + 5), [headerLines.length])
  const guardrailReserveRows = useMemo(() => {
    if (!guardrailNotice) return 0
    const expanded = Boolean(guardrailNotice.detail && guardrailNotice.expanded)
    return expanded ? 7 : 6
  }, [guardrailNotice])
  const composerReserveRows = useMemo(() => {
    const outerMargin = 1
    const promptLine = 1
    const promptRuleRows = claudeChrome ? 2 : 0
    const suggestionRows = (() => {
      if (overlayActive) return 1
      if (filePickerActive) {
        const chromeRows = claudeChrome ? 0 : 2
        const listMargin = claudeChrome ? 0 : 1
        const base = chromeRows + listMargin
        if (fileMenuMode === "tree") {
          if (filePicker.status === "loading" || filePicker.status === "hidden") return base + 1
          if (filePicker.status === "error") return base + 2
        } else {
          if (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning") {
            if (fileMenuRows.length === 0) return base + 1
          }
          if (fileIndexMeta.status === "error" && fileMenuRows.length === 0) return base + 2
        }
        if (fileMenuRows.length === 0) return base + 1
        const hiddenRows =
          (fileMenuWindow.hiddenAbove > 0 ? 1 : 0) + (fileMenuWindow.hiddenBelow > 0 ? 1 : 0)
        const fuzzyStatusRows =
          fileMenuMode === "fuzzy"
            ? (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning" ? 1 : 0) +
              (fileIndexMeta.truncated ? 1 : 0)
            : 0
        return base + fileMenuWindow.lineCount + hiddenRows + fuzzyStatusRows
      }
      if (suggestions.length === 0) return 1
      const hiddenRows =
        (suggestionWindow.hiddenAbove > 0 ? 1 : 0) + (suggestionWindow.hiddenBelow > 0 ? 1 : 0)
      return 1 + suggestionWindow.lineCount + hiddenRows
    })()
    const hintCount = overlayActive ? 0 : Math.min(4, hints.length)
    const hintRows = hintCount > 0 ? 1 + hintCount : 0
    const attachmentRows = overlayActive ? 0 : attachments.length > 0 ? attachments.length + 3 : 0
    const fileMentionRows = overlayActive ? 0 : fileMentions.length > 0 ? fileMentions.length + 3 : 0
    return outerMargin + promptRuleRows + promptLine + suggestionRows + hintRows + attachmentRows + fileMentionRows
  }, [
    attachments.length,
    claudeChrome,
    fileMentions.length,
    fileIndexMeta.status,
    fileIndexMeta.truncated,
    fileMenuIndex,
    fileMenuRows.length,
    fileMenuWindow.hiddenAbove,
    fileMenuWindow.hiddenBelow,
    fileMenuWindow.lineCount,
    fileMenuMode,
    filePicker.status,
    filePickerActive,
    hints.length,
    overlayActive,
    suggestions.length,
    suggestionWindow.hiddenAbove,
    suggestionWindow.hiddenBelow,
    suggestionWindow.lineCount,
  ])

  const overlayReserveRows = useMemo(() => {
    if (!overlayActive) return 0
    return Math.min(Math.max(10, Math.floor(rowCount * 0.55)), Math.max(0, rowCount - 8))
  }, [overlayActive, rowCount])

  const bodyTopMarginRows = 1
  const bodyBudgetRows = useMemo(() => {
    const available = rowCount - headerReserveRows - guardrailReserveRows - composerReserveRows - bodyTopMarginRows - overlayReserveRows
    return Math.max(0, available)
  }, [composerReserveRows, guardrailReserveRows, headerReserveRows, overlayReserveRows, rowCount])

  const statusGlyph = pendingResponse ? spinner : chalk.hex("#7CF2FF")("●")
  const modelGlyph = chalk.hex("#B36BFF")("●")
  const remoteGlyph = stats.remote ? chalk.hex("#7CF2FF")("●") : chalk.hex("#475569")("○")
  const toolsGlyph = stats.toolCount > 0 ? chalk.hex("#FBBF24")("●") : chalk.hex("#475569")("○")
  const eventsGlyph = stats.eventCount > 0 ? chalk.hex("#A855F7")("●") : chalk.hex("#475569")("○")
  const turnGlyph = stats.lastTurn != null ? chalk.hex("#34D399")("●") : chalk.hex("#475569")("○")

  const finalConversationEntries = useMemo(
    () => conversation.filter((entry) => entry.phase === "final"),
    [conversation],
  )

  const streamingConversationEntry = useMemo(() => {
    for (let index = conversation.length - 1; index >= 0; index -= 1) {
      const entry = conversation[index]
      if (entry?.phase === "streaming") {
        return entry
      }
    }
    return undefined
  }, [conversation])

  const measureToolEntryLines = useCallback((entry: ToolLogEntry): number => {
    const lines = entry.text.split(/\r?\n/)
    if (lines.length <= TOOL_COLLAPSE_THRESHOLD) return lines.length
    const head = lines.slice(0, TOOL_COLLAPSE_HEAD)
    const tail = lines.slice(-TOOL_COLLAPSE_TAIL)
    return head.length + 1 + tail.length
  }, [])

  const isEntryCollapsible = useCallback(
    (entry: ConversationEntry) => {
      if (entry.phase !== "final") return false
      if (verboseOutput) return false
      if (viewPrefs.collapseMode === "none") return false
      if (viewPrefs.collapseMode === "all") return entry.speaker === "assistant"
      return shouldAutoCollapseEntry(entry)
    },
    [viewPrefs.collapseMode, verboseOutput],
  )

  const measureConversationEntryLines = useCallback(
    (entry: ConversationEntry): number => {
      const useRich =
        viewPrefs.richMarkdown && entry.richBlocks && entry.richBlocks.length > 0 && !entry.markdownError
      const lines = useRich ? blocksToLines(entry.richBlocks) : entry.text.split(/\r?\n/)
      const errorLines = entry.markdownError && entry.speaker === "assistant" ? 1 : 0
      const collapsible = isEntryCollapsible(entry)
      const collapsed = collapsible ? collapsedEntriesRef.current.get(entry.id) !== false : false
      if (!collapsible || !collapsed) {
        return errorLines + Math.max(1, lines.length)
      }
      const head = lines.slice(0, ENTRY_COLLAPSE_HEAD)
      const tail = lines.slice(-ENTRY_COLLAPSE_TAIL)
      const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
      const summaryLines = hiddenCount > 0 ? 1 : 0
      return errorLines + Math.max(1, head.length + summaryLines + tail.length)
    },
    [collapsedVersion, isEntryCollapsible, viewPrefs.richMarkdown],
  )

  const toolLineBudget = useMemo(() => {
    if (overlayActive) return 0
    if (toolEvents.length === 0) return 0
    return Math.max(0, Math.min(14, Math.floor(bodyBudgetRows * 0.33)))
  }, [bodyBudgetRows, overlayActive, toolEvents.length])

  const unprintedToolEvents = useMemo(() => {
    if (!SCROLLBACK_MODE) return toolEvents
    const printed = printedToolIdsRef.current
    return toolEvents.filter((entry) => !printed.has(entry.id))
  }, [toolEvents])

  const toolWindow = useMemo(() => {
    if (toolLineBudget === 0) return { items: [], hiddenCount: unprintedToolEvents.length, usedLines: 0, truncated: false }
    return sliceTailByLineBudget(unprintedToolEvents, toolLineBudget, measureToolEntryLines)
  }, [measureToolEntryLines, toolLineBudget, unprintedToolEvents])

  const toolSectionMargin = !overlayActive && toolWindow.items.length > 0 ? 1 : 0
  const remainingBodyBudgetForTranscript = Math.max(0, bodyBudgetRows - toolWindow.usedLines - toolSectionMargin)

  const unprintedFinalConversationEntries = useMemo(() => {
    if (!SCROLLBACK_MODE) return finalConversationEntries
    const printed = printedConversationIdsRef.current
    return finalConversationEntries.filter((entry) => !printed.has(entry.id))
  }, [finalConversationEntries])

  const conversationEntriesForWindow = useMemo(() => {
    if (!SCROLLBACK_MODE) {
      if (!streamingConversationEntry) return finalConversationEntries
      return [...finalConversationEntries, streamingConversationEntry]
    }
    if (!streamingConversationEntry) return unprintedFinalConversationEntries
    return [...unprintedFinalConversationEntries, streamingConversationEntry]
  }, [finalConversationEntries, streamingConversationEntry, unprintedFinalConversationEntries])

  const transcriptLineBudget = overlayActive ? Math.min(10, remainingBodyBudgetForTranscript) : remainingBodyBudgetForTranscript
  const conversationWindow = useMemo(
    () => sliceTailByLineBudget(conversationEntriesForWindow, transcriptLineBudget, measureConversationEntryLines),
    [conversationEntriesForWindow, measureConversationEntryLines, transcriptLineBudget],
  )

  const collapsibleEntries = useMemo(
    () => conversationWindow.items.filter((entry) => isEntryCollapsible(entry)),
    [conversationWindow, isEntryCollapsible],
  )

  const collapsibleMeta = useMemo(() => {
    const map = new Map<string, { index: number; total: number }>()
    const total = collapsibleEntries.length
    collapsibleEntries.forEach((entry, index) => {
      map.set(entry.id, { index, total })
    })
    return map
  }, [collapsibleEntries])

  useEffect(() => {
    const map = collapsedEntriesRef.current
    let changed = false
    const activeIds = new Set<string>(collapsibleEntries.map((entry) => entry.id))
    for (const key of Array.from(map.keys())) {
      if (!activeIds.has(key)) {
        map.delete(key)
        changed = true
      }
    }
    if (viewPrefs.collapseMode === "none") {
      for (const id of activeIds) {
        if (map.get(id) !== false) {
          map.set(id, false)
          changed = true
        }
      }
    } else if (viewPrefs.collapseMode === "all") {
      for (const id of activeIds) {
        if (map.get(id) !== true) {
          map.set(id, true)
          changed = true
        }
      }
    } else {
      for (const id of activeIds) {
        if (!map.has(id)) {
          map.set(id, true)
          changed = true
        }
      }
    }
    if (changed) {
      setCollapsedVersion((value) => value + 1)
    }
  }, [collapsibleEntries, viewPrefs.collapseMode])

  useEffect(() => {
    setSelectedCollapsibleEntryId((prev) => {
      if (collapsibleEntries.length === 0) {
        return prev === null ? prev : null
      }
      if (prev && collapsibleEntries.some((entry) => entry.id === prev)) {
        return prev
      }
      const latest = collapsibleEntries[collapsibleEntries.length - 1]
      return latest?.id ?? null
    })
  }, [collapsibleEntries])

  const toggleCollapsedEntry = useCallback((entryId: string) => {
    const map = collapsedEntriesRef.current
    const isExpanded = map.get(entryId) === false
    map.set(entryId, isExpanded ? true : false)
    setCollapsedVersion((value) => value + 1)
  }, [])

  const cycleCollapsibleSelection = useCallback(
    (direction: 1 | -1) => {
      if (collapsibleEntries.length === 0) return false
      if (!selectedCollapsibleEntryId) {
        setSelectedCollapsibleEntryId(collapsibleEntries[collapsibleEntries.length - 1]?.id ?? null)
        return true
      }
      const currentIndex = collapsibleEntries.findIndex((entry) => entry.id === selectedCollapsibleEntryId)
      const safeIndex = currentIndex === -1 ? collapsibleEntries.length - 1 : currentIndex
      const nextIndex = (safeIndex + direction + collapsibleEntries.length) % collapsibleEntries.length
      setSelectedCollapsibleEntryId(collapsibleEntries[nextIndex].id)
      return true
    },
    [collapsibleEntries, selectedCollapsibleEntryId],
  )

  const toggleSelectedCollapsibleEntry = useCallback(() => {
    if (!selectedCollapsibleEntryId) return false
    toggleCollapsedEntry(selectedCollapsibleEntryId)
    return true
  }, [selectedCollapsibleEntryId, toggleCollapsedEntry])

  const renderConversationText = useCallback(
    (entry: ConversationEntry, key?: string) => {
      const useRich = viewPrefs.richMarkdown && entry.richBlocks && entry.richBlocks.length > 0 && !entry.markdownError
      const label = chalk.hex(speakerColor(entry.speaker))(entry.speaker.toUpperCase().padEnd(LABEL_WIDTH, " "))
      const padLabel = chalk.dim(" ".repeat(LABEL_WIDTH))
      const errorLine =
        entry.markdownError && entry.speaker === "assistant"
          ? (
              <Text key={`${entry.id}-md-error`} color="#f87171">
                {label} rich markdown disabled: {entry.markdownError}
              </Text>
            )
          : null
      const colorizeContent = useRich
        ? (line: string) => line
        : (line: string) => {
            if (line.startsWith("diff --git") || line.startsWith("index ")) return chalk.hex("#7CF2FF")(line)
            if (line.startsWith("@@")) return chalk.hex("#FBBF24")(line)
            if (line.startsWith("---") || line.startsWith("+++")) return chalk.hex("#C4B5FD")(line)
            if (line.startsWith("+") && !line.startsWith("+++")) return chalk.hex("#34D399")(line)
            if (line.startsWith("-") && !line.startsWith("---")) return chalk.hex("#FB7185")(line)
            return line
          }
      const renderLine = (line: string, index: number) => (
        <Text key={`${entry.id}-ln-${index}`}>
          {index === 0 ? label : padLabel} {colorizeContent(line)}
        </Text>
      )
      const lines = useRich ? blocksToLines(entry.richBlocks) : entry.text.split(/\r?\n/)
      const plainLines = useRich ? lines.map(stripAnsiCodes) : lines
      const collapsible = isEntryCollapsible(entry)
      const collapsed = collapsible ? collapsedEntriesRef.current.get(entry.id) !== false : false
      if (!collapsible || !collapsed) {
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {errorLine}
            {lines.map((line, idx) => renderLine(line, idx))}
          </Box>
        )
      }
      const head = lines.slice(0, ENTRY_COLLAPSE_HEAD)
      const tail = lines.slice(-ENTRY_COLLAPSE_TAIL)
      const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
      const diffPreview = computeDiffPreview(plainLines)
      const meta = collapsibleMeta.get(entry.id)
      const isSelected = selectedCollapsibleEntryId === entry.id
      const selectionGlyph = isSelected ? chalk.hex("#7CF2FF")("▶") : chalk.dim("•")
      const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
      const summaryParts = [
        `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
        meta ? `block ${meta.index + 1}/${meta.total}` : null,
        diffPreview ? `Δ +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
      ].filter(Boolean)
      const summary = summaryParts.join(" • ")
      const instruction = isSelected ? "press e to expand/collapse" : "use [ / ] to target, then press e"
      return (
        <Box key={key ?? entry.id} flexDirection="column">
          {errorLine}
          {head.map((line, idx) => renderLine(line, idx))}
          {hiddenCount > 0 && (
            <Text color={isSelected ? "#7CF2FF" : "gray"}>
              {padLabel} {selectionGlyph} {summary} — {instruction}
            </Text>
          )}
          {tail.map((line, idx) => renderLine(line, head.length + idx + 1))}
        </Box>
      )
    },
    [collapsedVersion, collapsibleMeta, isEntryCollapsible, selectedCollapsibleEntryId, viewPrefs.richMarkdown],
  )

  const renderToolEntry = useCallback((entry: ToolLogEntry, key?: string) => {
    const label = `[${entry.kind}]`.padEnd(TOOL_LABEL_WIDTH, " ")
    const labelPad = " ".repeat(TOOL_LABEL_WIDTH)
    const kindToken = `[${entry.kind}]`
    const glyph =
      entry.status === "success"
        ? chalk.hex("#34D399")("●")
        : entry.status === "error"
          ? chalk.hex("#F87171")("●")
          : entry.kind === "call"
            ? chalk.hex("#7CF2FF")("●")
            : chalk.hex(TOOL_EVENT_COLOR)("●")
    const rawText = entry.text.startsWith(kindToken)
      ? entry.text.slice(kindToken.length).trimStart()
      : entry.text
    const lines = rawText.split(/\r?\n/)
    const colorizeToolLine = (line: string) => {
      if (!line) return line
      if (line.includes("\u001b")) return line
      if (line.startsWith("diff --git") || line.startsWith("index ")) return chalk.hex("#7CF2FF")(line)
      if (line.startsWith("@@")) return chalk.hex("#FBBF24")(line)
      if (line.startsWith("---") || line.startsWith("+++")) return chalk.hex("#C4B5FD")(line)
      if (line.startsWith("+") && !line.startsWith("+++")) return chalk.hex("#34D399")(line)
      if (line.startsWith("-") && !line.startsWith("---")) return chalk.hex("#FB7185")(line)
      return chalk.gray(line)
    }
    const renderLine = (line: string, index: number) => (
      <Text key={`${entry.id}-ln-${index}`}>
        {index === 0
          ? `${glyph} ${chalk.dim(label)} ${colorizeToolLine(line)}`
          : `${" ".repeat(2)}${chalk.dim(labelPad)} ${colorizeToolLine(line)}`}
      </Text>
    )
    if (verboseOutput || lines.length <= TOOL_COLLAPSE_THRESHOLD) {
      return (
        <Box key={key ?? entry.id} flexDirection="column">
          {lines.map((line, idx) => renderLine(line, idx))}
        </Box>
      )
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
    return (
      <Box key={key ?? entry.id} flexDirection="column">
        {head.map((line, idx) => renderLine(line, idx))}
        <Text color="gray">
          {`${" ".repeat(2)}${chalk.dim(labelPad)} … ${summaryParts.join(" — ")}`}
        </Text>
        {tail.map((line, idx) => renderLine(line, head.length + idx + 1))}
      </Box>
    )
  }, [verboseOutput])

  const toolNodes = useMemo(() => {
    if (toolWindow.items.length === 0) return []
    const nodes: React.ReactNode[] = []
    if (!SCROLLBACK_MODE && toolWindow.truncated) {
      nodes.push(
        <Text key="tool-truncated" color="dim">
          … {toolWindow.hiddenCount} earlier tool event{toolWindow.hiddenCount === 1 ? "" : "s"} hidden …
        </Text>,
      )
    }
    for (const entry of toolWindow.items) {
      nodes.push(renderToolEntry(entry))
    }
    return nodes
  }, [renderToolEntry, toolWindow])

  const liveSlotNodes = useMemo(
    () =>
      liveSlots.map((slot, idx) => (
        <LiveSlot
          key={`slot-${slot.id}`}
          slot={slot}
          index={idx}
          tick={animationTick}
        />
      )),
    [animationTick, liveSlots],
  )

  const renderPermissionNoteLine = useCallback((value: string, cursorIndex: number) => {
    if (!value) {
      return `${chalk.inverse(" ")}${chalk.dim(" Tell me what to do differently…")}`
    }
    const safeCursor = Math.max(0, Math.min(cursorIndex, value.length))
    const before = value.slice(0, safeCursor)
    const currentChar = value[safeCursor] ?? " "
    const after = value.slice(safeCursor + 1)
    return `${before}${chalk.inverse(currentChar === "" ? " " : currentChar)}${after}`
  }, [])

  const metaNodes = useMemo(() => {
    if (claudeChrome) return []
    const hintParts = [
      "! for bash",
      "/ for commands",
      "@ for files",
      "Tab to complete",
      "Esc interrupt",
      "Esc Esc clear input",
      "Ctrl+K model",
      "Ctrl+O detailed",
    ]
    if (keymap === "claude") {
      hintParts.push("Ctrl+T todos")
      hintParts.push("Ctrl+Shift+T transcript")
    } else {
      hintParts.push("Ctrl+T transcript")
    }
    return [
      <Text key="meta-slash" color="dim">
        Slash commands: {SLASH_COMMAND_HINT}
      </Text>,
      <Text key="meta-hints" color="dim">
        {hintParts.join(" • ")}
      </Text>,
    ]
  }, [claudeChrome, keymap])

  const hintNodes = useMemo(() => {
    const filtered = claudeChrome ? hints.filter((hint) => !hint.startsWith("Session ")) : hints
    if (claudeChrome) {
      let statusText = filtered.slice(-1)[0] ?? ""
      if (escPrimedAt && !pendingResponse) {
        statusText = "Press Esc again to clear input."
      } else if (ctrlCPrimedAt) {
        statusText = "Press Ctrl+C again to exit."
      }
      const leftText = "  ? for shortcuts"
      const maxRight = Math.max(0, contentWidth - stripAnsiCodes(leftText).length)
      const rightText = statusText.length > 0 ? formatCell(statusText, maxRight, "right").trimStart() : ""
      const leftWidth = Math.max(0, contentWidth - stripAnsiCodes(rightText).length)
      const line = `${formatCell(leftText, leftWidth, "left")}${rightText}`
      return [
        <Text key="hint-claude-footer" color="dim">
          {line}
        </Text>,
      ]
    }
    const latest = filtered.slice(-4)
    const nodes = latest.map((hint, index) => (
      <Text key={`hint-${index}`} color="yellow">
        {chalk.yellow("•")} {hint}
      </Text>
    ))
    if (escPrimedAt && !pendingResponse) {
      nodes.push(
        <Text key="hint-esc-clear" color="yellow">
          {chalk.yellow("•")} Press Esc again to clear input.
        </Text>,
      )
    }
    if (ctrlCPrimedAt) {
      nodes.push(
        <Text key="hint-exit" color="yellow">
          {chalk.yellow("•")} Press Ctrl+C again to exit.
        </Text>,
      )
    }
    return nodes
  }, [claudeChrome, contentWidth, ctrlCPrimedAt, escPrimedAt, hints, pendingResponse])

  const shortcutLines = useMemo(() => {
    const rows: Array<[string, string]> = [
      ["Ctrl+C ×2", "Exit the REPL"],
      ["Ctrl+D", "Exit immediately"],
      ["Ctrl+Z", "Suspend (empty input)"],
      ["Ctrl+S", "Stash input to history"],
      ["Esc", "Stop streaming"],
      ["Esc Esc", "Clear input / rewind when empty"],
      ["Shift+Enter", "Insert newline"],
      ["Alt+Enter", "Insert newline"],
      ["Ctrl+O", "Toggle detailed transcript"],
      ["Ctrl+P", "Command palette"],
      ["Ctrl+K", "Model picker"],
      ["Alt+P", "Model picker"],
      ["Tab", "Complete @ or / list"],
      ["/", "Slash commands"],
      ["@", "File picker"],
      ["?", "Toggle shortcuts"],
    ]
    if (keymap === "claude") {
      rows.push(["Ctrl+T", "Todos panel"])
      rows.push(["Ctrl+Shift+T", "Transcript viewer"])
    } else {
      rows.push(["Ctrl+T", "Transcript viewer"])
    }
    const pad = 14
    return rows.map(([key, desc]) => `${chalk.cyan(key.padEnd(pad))} ${desc}`)
  }, [keymap])

  const collapsedHintNode = useMemo(() => {
    if (collapsibleEntries.length === 0) return null
    const meta = selectedCollapsibleEntryId ? collapsibleMeta.get(selectedCollapsibleEntryId) : undefined
    const selectionText = meta ? `block ${meta.index + 1}/${meta.total}` : "no block targeted"
    return (
      <Text color="dim">
        Collapsed transcript controls: use "[" and "]" to cycle ({selectionText}), press e to expand/collapse.
      </Text>
    )
  }, [collapsibleEntries.length, collapsibleMeta, selectedCollapsibleEntryId])

  const virtualizationHintNode = useMemo(() => {
    if (SCROLLBACK_MODE) return null
    if (!compactMode) return null
    const hidden = conversationWindow.hiddenCount
    const hiddenText = hidden > 0 ? ` (${hidden} hidden)` : ""
    return (
      <Text color="dim">
        Compact transcript mode active — showing last {conversationWindow.items.length} message{conversationWindow.items.length === 1 ? "" : "s"}
        {hiddenText}. Use /view scroll auto to expand.
      </Text>
    )
  }, [compactMode, conversationWindow.hiddenCount, conversationWindow.items.length])

  const transcriptNodes = useMemo(() => {
    if (conversationWindow.items.length === 0) {
      if (SCROLLBACK_MODE) {
        return streamingConversationEntry
          ? [renderConversationText(streamingConversationEntry, "streaming-only")]
          : []
      }
      return [
        <Text key="transcript-empty" color="gray">
          {pendingResponse ? "Assistant is thinking…" : "No conversation yet. Type a prompt to get started."}
        </Text>,
      ]
    }
    const nodes: React.ReactNode[] = []
    if (!SCROLLBACK_MODE && conversationWindow.truncated) {
      nodes.push(
        <Text key="transcript-truncated" color="dim">
          … {conversationWindow.hiddenCount} earlier {conversationWindow.hiddenCount === 1 ? "message" : "messages"} hidden …
        </Text>,
      )
    }
    for (const entry of conversationWindow.items) {
      nodes.push(renderConversationText(entry))
    }
    return nodes
  }, [conversationWindow, pendingResponse, renderConversationText])

  const handleGlobalKeys = useCallback<KeyHandler>(
    (char, key) => {
      const lowerChar = char?.toLowerCase()
      const isEscapeKey = key.escape || char === "\u001b"
      const isCtrlT = key.ctrl && lowerChar === "t"
      const isCtrlShiftT = key.ctrl && key.shift && lowerChar === "t"
      if (isCtrlShiftT) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlT) {
        if (keymap === "claude") {
          setTodosOpen((prev) => !prev)
        } else if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (key.ctrl && (lowerChar === "o" || char === "\u000f")) {
        setVerboseOutput((prev) => {
          const next = !prev
          pushCommandResult("Detailed transcript", [next ? "ON" : "OFF"])
          return next
        })
        return true
      }
      if (key.meta && !key.ctrl && lowerChar === "p") {
        if (modelMenu.status === "hidden") {
          void onModelMenuOpen()
        } else {
          onModelMenuCancel()
        }
        return true
      }
      if (key.ctrl && (lowerChar === "c" || char === "\u0003")) {
        const now = Date.now()
        if (ctrlCPrimedAt && now - ctrlCPrimedAt < DOUBLE_CTRL_C_WINDOW_MS) {
          setCtrlCPrimedAt(null)
          void onSubmit("/quit")
          process.exit(0)
          return true
        }
        setCtrlCPrimedAt(now)
        return true
      }
      if (
        isEscapeKey &&
        !permissionRequest &&
        rewindMenu.status === "hidden" &&
        confirmState.status === "hidden" &&
        modelMenu.status === "hidden" &&
        paletteState.status === "hidden"
      ) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
          return true
        }
        if (pendingResponse) {
          setEscPrimedAt(null)
          void onSubmit("/stop")
          return true
        }
        if (key.meta) {
          setEscPrimedAt(null)
          handleLineEdit("", 0)
          return true
        }
        const now = Date.now()
        if (escPrimedAt && now - escPrimedAt < 650) {
          setEscPrimedAt(null)
          if (inputValueRef.current.trim().length === 0) {
            void onSubmit("/rewind")
          } else {
            handleLineEdit("", 0)
          }
          return true
        }
        setEscPrimedAt(now)
        return true
      }
      if (!key.ctrl && !key.meta) {
        if (guardrailNotice) {
          if (lowerChar === "e") {
            onGuardrailToggle()
            return true
          }
          if (lowerChar === "x") {
            onGuardrailDismiss()
            return true
          }
        } else {
          if (lowerChar === "e" && toggleSelectedCollapsibleEntry()) {
            return true
          }
        }
        if (char === "[" && cycleCollapsibleSelection(-1)) {
          return true
        }
        if (char === "]" && cycleCollapsibleSelection(1)) {
          return true
        }
      }
      if (key.ctrl && lowerChar === "k") {
        if (modelMenu.status === "hidden") {
          void onModelMenuOpen()
        } else {
          onModelMenuCancel()
        }
        return true
      }
      if (key.ctrl && key.shift && lowerChar === "c") {
        openConfirm("Clear conversation and tool logs?", async () => {
          await onSubmit("/clear")
        })
        return true
      }
      if (key.ctrl && lowerChar === "p") {
        if (paletteState.status === "open") closePalette()
        else openPalette()
        return true
      }
      if (isEscapeKey && modelMenu.status !== "hidden") {
        onModelMenuCancel()
        return true
      }
      return false
    },
    [
      cycleCollapsibleSelection,
      confirmState.status,
      ctrlCPrimedAt,
      enterTranscriptViewer,
      escPrimedAt,
      exitTranscriptViewer,
      guardrailNotice,
      handleLineEdit,
      keymap,
      closePalette,
      modelMenu.status,
      onModelMenuCancel,
      onModelMenuOpen,
      openConfirm,
      openPalette,
      onSubmit,
      onGuardrailDismiss,
      onGuardrailToggle,
      paletteState.status,
      pendingResponse,
      permissionRequest,
      pushCommandResult,
      rewindMenu.status,
      transcriptViewerOpen,
      toggleSelectedCollapsibleEntry,
    ],
  )

  useKeyRouter(getTopLayer, {
    modal: handleOverlayKeys,
    palette: handlePaletteKeys,
    editor: handleEditorKeys,
    global: handleGlobalKeys,
  })

  useLayoutEffect(() => {
    if (!SCROLLBACK_MODE) return
    if (transcriptViewerOpen) return
    setStaticFeed((prev) => {
      let next = prev
      let changed = false

      if (!headerPrintedRef.current) {
        const headerNode = (
          <Box flexDirection="column">
            {headerLines.map((line, index) => (
              <Text key={`header-${index}`}>{line}</Text>
            ))}
            {headerSubtitleLines.map((line, index) => (
              <Text key={`header-sub-${index}`}>{line}</Text>
            ))}
          </Box>
        )
        next = [...next, { id: `header-${sessionId}`, node: headerNode }]
        headerPrintedRef.current = true
        changed = true
      }

      for (const entry of finalConversationEntries) {
        if (printedConversationIdsRef.current.has(entry.id)) continue
        printedConversationIdsRef.current.add(entry.id)
        next = [...next, { id: `conv-${entry.id}`, node: renderConversationText(entry, `static-${entry.id}`) }]
        changed = true
      }

      for (const entry of toolEvents) {
        if (printedToolIdsRef.current.has(entry.id)) continue
        printedToolIdsRef.current.add(entry.id)
        next = [...next, { id: `tool-${entry.id}`, node: renderToolEntry(entry, `static-${entry.id}`) }]
        changed = true
      }

      return changed ? next : prev
    })
  }, [
    finalConversationEntries,
    headerLines,
    headerSubtitleLines,
    renderConversationText,
    renderToolEntry,
    sessionId,
    toolEvents,
    transcriptViewerOpen,
  ])

  const baseContent = (
    <Box flexDirection="column" paddingX={1} marginTop={SCROLLBACK_MODE ? 1 : 0}>
        {!claudeChrome && (
          <Text>
            {chalk.dim(sessionId.slice(0, 12))} {statusGlyph} {status} {modelGlyph} model {chalk.bold(stats.model)}{" "}
            {remoteGlyph} {stats.remote ? "remote" : "local"} {eventsGlyph} events {stats.eventCount} {toolsGlyph} tools{" "}
            {stats.toolCount}
            {stats.lastTurn != null ? ` ${turnGlyph} turn ${stats.lastTurn}` : ""}
          </Text>
        )}
        {metaNodes.length > 0 && (
          <Box flexDirection="column" marginTop={1}>
            {metaNodes}
          </Box>
        )}
      {guardrailNotice && <GuardrailBanner notice={guardrailNotice} />}

      <Box flexDirection="column" marginTop={claudeChrome ? 0 : 1}>
        <Box flexDirection="column">
          {transcriptNodes}
        </Box>
        {toolNodes.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            {toolNodes}
          </Box>
        )}
        {!overlayActive && liveSlotNodes.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            {liveSlotNodes}
          </Box>
        )}
        {!overlayActive && collapsedHintNode && (
          <Box marginTop={1}>
            {collapsedHintNode}
          </Box>
        )}
        {!overlayActive && virtualizationHintNode && (
          <Box marginTop={collapsedHintNode ? 0 : 1}>
            {virtualizationHintNode}
          </Box>
        )}
      </Box>

      <Box marginTop={1} flexDirection="column">
        {claudeChrome && <Text color="dim">{promptRule}</Text>}
        <Box>
          <Text color={claudeChrome ? undefined : "cyan"}>{claudeChrome ? "> " : "› "}</Text>
          <LineEditor
            value={input}
            cursor={cursor}
            focus={!inputLocked}
            placeholder={claudeChrome ? 'Try "edit <file> to..."' : "Type your request…"}
            placeholderPad={!claudeChrome}
            hideCaretWhenPlaceholder={claudeChrome}
            onChange={handleLineEditGuarded}
            onSubmit={handleLineSubmit}
            submitOnEnter
            onPasteAttachment={handleAttachment}
          />
        </Box>
        {claudeChrome && <Text color="dim">{promptRule}</Text>}
        {overlayActive ? (
          claudeChrome ? null : <Text color="dim">Input locked — use the active modal controls.</Text>
        ) : filePickerActive ? (
          <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
            {!claudeChrome && (
              <Text color="#7CF2FF">{`Attach file ${chalk.dim(filePickerQueryParts.cwd === "." ? "(root)" : filePickerQueryParts.cwd)}`}</Text>
            )}
            {!claudeChrome && (
              <Text color="gray">
                {fileMenuMode === "fuzzy" ? "Fuzzy search • " : ""}
                Type to filter • ↑/↓ navigate • Tab complete • Esc clear
              </Text>
            )}
            <Box flexDirection="column" marginTop={claudeChrome ? 0 : 1}>
              {fileMenuMode === "tree" && (filePicker.status === "loading" || filePicker.status === "hidden") ? (
                <Text color="gray">Loading…</Text>
              ) : fileMenuMode === "tree" && filePicker.status === "error" ? (
                <>
                  <Text color="#fb7185">{filePicker.message ? `Error: ${filePicker.message}` : "Error loading files."}</Text>
                  <Text color="gray">Tab to retry • Esc to clear</Text>
                </>
              ) : fileMenuMode === "fuzzy" && fileIndexMeta.status === "error" && fileMenuRows.length === 0 ? (
                <>
                  <Text color="#fb7185">{fileIndexMeta.message ? `Error: ${fileIndexMeta.message}` : "Error indexing files."}</Text>
                  <Text color="gray">Tab to retry • Esc to clear</Text>
                </>
              ) : fileMenuRows.length === 0 ? (
                fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning") ? (
                  <Text color="dim">{`Indexing… (${fileIndexMeta.fileCount} files)`}</Text>
                ) : (
                  <Text color="dim">(no matches)</Text>
                )
              ) : (
                (() => {
                  const contentWidth = Math.max(10, columnWidth - 4)
                  const windowItems = fileMenuWindow.items
                  const hiddenAbove = fileMenuWindow.hiddenAbove
                  const hiddenBelow = fileMenuWindow.hiddenBelow
                   return (
                     <>
                       {fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning") && (
                         <Text color="dim">{`Indexing… (${fileIndexMeta.fileCount} files)`}</Text>
                      )}
                      {fileMenuMode === "fuzzy" && fileIndexMeta.truncated && (
                        <Text color="dim">{`Index truncated at ${fileIndexMeta.fileCount} files.`}</Text>
                      )}
                      {hiddenAbove > 0 && <Text color="dim">{`↑ ${hiddenAbove} more…`}</Text>}
                      {windowItems.map((row, index) => {
                        const absoluteIndex = fileMenuWindow.start + index
                        const selected = absoluteIndex === fileMenuIndex
                        if (row.kind === "resource") {
                          return (
                            <Box key={`resource:${row.resource.label}`} flexDirection="column">
                              <Text inverse={selected} color={selected ? undefined : "white"}>
                                {formatCell(row.resource.label, contentWidth, "left")}
                              </Text>
                              {row.resource.detail && (
                                <Text color="dim">{`    ${row.resource.detail}`}</Text>
                              )}
                            </Box>
                          )
                        }
                        const display = row.item.path
                        const suffix = row.item.type === "directory" ? "/" : ""
                        const label = formatCell(`${display}${suffix}`, contentWidth, "left")
                        return (
                          <Text
                            key={`${row.item.type}:${row.item.path}`}
                            inverse={selected}
                            color={selected ? undefined : row.item.type === "directory" ? "cyan" : "white"}
                          >
                            {label}
                          </Text>
                        )
                      })}
                      {hiddenBelow > 0 && <Text color="dim">{`↓ ${hiddenBelow} more…`}</Text>}
                    </>
                  )
                })()
              )}
            </Box>
          </Box>
        ) : suggestions.length > 0 ? (
          <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
            {suggestionWindow.hiddenAbove > 0 && (
              <Text color="dim">{`↑ ${suggestionWindow.hiddenAbove} more…`}</Text>
            )}
            {suggestionWindow.items.map((row, index) => {
              const globalIndex = suggestionWindow.start + index
              const selected = globalIndex === suggestIndex
              const lines = buildSuggestionLines(row, claudeChrome)
              return lines.map((line, lineIndex) => {
                const left = formatCell(line.label, suggestionLayout.commandWidth)
                const right = formatCell(line.summary, suggestionLayout.summaryWidth)
                const styledLeft = selected
                  ? left
                  : highlightFuzzyLabel(left, row.command, activeSlashQuery)
                const styledRight = selected ? right : chalk.dim(right)
                const rendered = `${styledLeft}  ${styledRight}`
                return (
                  <Text
                    key={`suggestion-${globalIndex}-${lineIndex}`}
                    inverse={selected}
                    wrap={claudeChrome ? "wrap" : "truncate-end"}
                  >
                    {rendered}
                  </Text>
                )
              })
            })}
            {suggestionWindow.hiddenBelow > 0 && (
              <Text color="dim">{`↓ ${suggestionWindow.hiddenBelow} more…`}</Text>
            )}
          </Box>
        ) : (
          <Text color="dim">{" "}</Text>
        )}
        {!overlayActive && hintNodes.length > 0 && (
      <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
            {hintNodes}
          </Box>
        )}
        {!overlayActive && attachments.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            <Text color="#F97316">Attachments queued ({attachments.length})</Text>
            {attachments.map((attachment) => (
              <Text key={attachment.id} color="gray">
                • {attachment.mime} — {formatBytes(attachment.size)}
              </Text>
            ))}
            <Text color="#FACC15">Attachments upload automatically when you submit; they remain queued here until then.</Text>
          </Box>
        )}
        {!overlayActive && fileMentions.length > 0 && (
          <Box marginTop={1} flexDirection="column">
            <Text color="#7CF2FF">Files queued ({fileMentions.length})</Text>
            {fileMentions.map((entry) => (
              <Text key={entry.id} color="gray" wrap="truncate-end">
                • {entry.path}
                {entry.size != null ? ` — ${formatBytes(entry.size)}` : ""}
                {entry.requestedMode !== "auto" ? ` — ${entry.requestedMode}` : ""}
              </Text>
            ))}
            <Text color="#FACC15">Files are attached as context on submit; oversized files are truncated.</Text>
          </Box>
        )}
      </Box>
    </Box>
  )

  const modalStack: ModalDescriptor[] = []

  if (confirmState.status === "prompt") {
    modalStack.push({
      id: "confirm",
      render: () => (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor="#F97316"
          paddingX={2}
          paddingY={1}
          width={Math.min(80, PANEL_WIDTH)}
          alignSelf="center"
          marginTop={2}
        >
          <Text color="#FACC15">{confirmState.message ?? "Confirm action?"}</Text>
          <Text color="gray">Enter to confirm • Esc to cancel</Text>
        </Box>
      ),
    })
  }

  if (shortcutsOpen) {
    modalStack.push({
      id: "shortcuts",
      render: () => (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor="#7CF2FF"
          paddingX={2}
          paddingY={1}
          width={PANEL_WIDTH}
          alignSelf="center"
          marginTop={2}
        >
          <Text color="#7CF2FF">{chalk.bold("Shortcuts")}</Text>
          <Text color="dim">Press ? or Esc to close</Text>
          <Box flexDirection="column" marginTop={1}>
            {shortcutLines.map((line, idx) => (
              <Text key={`shortcut-${idx}`} wrap="truncate-end">
                {line}
              </Text>
            ))}
          </Box>
        </Box>
      ),
    })
  }

  if (paletteState.status === "open") {
    modalStack.push({
      id: "palette",
      render: () => (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor="#C084FC"
          paddingX={2}
          paddingY={1}
          width={PANEL_WIDTH}
          alignSelf="center"
          marginTop={2}
        >
          <Text color="#C084FC">Command palette</Text>
          <Text color="dim">Search: {paletteState.query.length > 0 ? paletteState.query : chalk.dim("<type to filter>")}</Text>
          <Box flexDirection="column" marginTop={1}>
            {paletteItems.length === 0 ? (
              <Text color="dim">No commands match.</Text>
            ) : (
              paletteItems.slice(0, MAX_VISIBLE_MODELS).map((item: SlashCommandInfo, idx: number) => {
                const isActive = idx === Math.min(paletteState.index, paletteItems.length - 1)
                const label = `/${item.name}${item.usage ? ` ${item.usage}` : ""} — ${item.summary}`
                return (
                  <Text key={item.name} backgroundColor={isActive ? "#C084FC" : undefined} color={isActive ? "#0F172A" : undefined}>
                    {isActive ? "›" : " "} {label}
                  </Text>
                )
              })
            )}
          </Box>
        </Box>
      ),
    })
  }

  if (modelMenu.status !== "hidden") {
    modalStack.push({
      id: "model-picker",
      render: () => (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor="#7CF2FF"
          paddingX={2}
          paddingY={1}
          width={PANEL_WIDTH}
          alignSelf="center"
          marginTop={2}
        >
          {modelMenu.status === "loading" && <Text color="cyan">Loading model catalog…</Text>}
          {modelMenu.status === "error" && <Text color="red">{modelMenu.message}</Text>}
          {modelMenu.status === "ready" && (
            <>
              <Text color="green">Select a model (Enter to confirm, Esc to cancel)</Text>
              <Text color="dim">Search: {modelSearch.length > 0 ? modelSearch : chalk.dim("<type to filter>")}</Text>
              <Text color="dim">Current: {chalk.cyan(stats.model)}</Text>
              <Text color="dim">Navigate: ↑/↓ or Tab • Shift+Tab up • Legend: ● current · ★ default</Text>
              <Box flexDirection="column" marginTop={1}>
                {filteredModels.length === 0 ? (
                  <Text color="dim">No models match.</Text>
                ) : (
                  <>
                    <Text color="gray" wrap="truncate-end">
                      {modelMenuHeaderText}
                    </Text>
                    {visibleModelRows.map((row, idx) => {
                      if (row.kind === "header") {
                        return (
                          <Text key={`model-header-${row.label}-${idx}`} color="dim">
                            {row.label.toUpperCase()}
                          </Text>
                        )
                      }
                      const isActive = row.index === modelIndex
                      const rowText = formatModelRowText(row.item)
                      return (
                        <Text
                          key={row.item.value}
                          color={isActive ? "#0F172A" : undefined}
                          backgroundColor={isActive ? "#7CF2FF" : undefined}
                          wrap="truncate-end"
                        >
                          {isActive ? "› " : "  "}
                          {rowText}
                        </Text>
                      )
                    })}
                  </>
                )}
              </Box>
              {filteredModels.length > MAX_VISIBLE_MODELS && (
                <Text color="dim">
                  {modelOffset + 1}-{Math.min(modelOffset + MAX_VISIBLE_MODELS, filteredModels.length)} of {filteredModels.length}
                </Text>
              )}
            </>
          )}
        </Box>
      ),
    })
  }

  if (rewindMenu.status !== "hidden") {
    modalStack.push({
      id: "rewind",
      render: () => {
        const isLoading = rewindMenu.status === "loading"
        const isError = rewindMenu.status === "error"
        return (
          <Box
            flexDirection="column"
            borderStyle="round"
            borderColor="#60A5FA"
            paddingX={2}
            paddingY={1}
            width={PANEL_WIDTH}
            alignSelf="center"
            marginTop={2}
          >
            <Text color="#93C5FD">{chalk.bold("Rewind checkpoints")}</Text>
            {isLoading && <Text color="cyan">Loading checkpoints…</Text>}
            {isError && <Text color="red">{rewindMenu.message}</Text>}
            <Text color="gray">↑/↓ select • 1 convo • 2 code • 3 both • Esc close</Text>
            <Box flexDirection="column" marginTop={1}>
              {rewindCheckpoints.length === 0 ? (
                <Text color="dim">No checkpoints yet. (/rewind again after the next tool run.)</Text>
              ) : (
                rewindVisible.map((entry, idx) => {
                  const listIndex = rewindOffset + idx
                  const isActive = listIndex === rewindSelectedIndex
                  const statsParts: string[] = []
                  if (entry.trackedFiles != null) statsParts.push(`${entry.trackedFiles} files`)
                  if (entry.additions != null || entry.deletions != null) {
                    statsParts.push(`+${entry.additions ?? 0} -${entry.deletions ?? 0}`)
                  }
                  if (entry.hasUntrackedChanges) statsParts.push("untracked")
                  const meta = [formatIsoTimestamp(entry.createdAt), ...statsParts].filter(Boolean).join(" · ")
                  const label = `${entry.preview}`
                  return (
                    <Box key={entry.checkpointId} flexDirection="column" marginBottom={0}>
                      <Text
                        color={isActive ? "#0F172A" : undefined}
                        backgroundColor={isActive ? "#93C5FD" : undefined}
                        wrap="truncate-end"
                      >
                        {isActive ? "› " : "  "}
                        {label}
                      </Text>
                      <Text color={isActive ? "#0F172A" : "dim"} backgroundColor={isActive ? "#93C5FD" : undefined} wrap="truncate-end">
                        {isActive ? "  " : "  "}
                        {chalk.dim(meta || entry.checkpointId)}
                      </Text>
                    </Box>
                  )
                })
              )}
              {rewindCheckpoints.length > rewindVisibleLimit && (
                <Text color="dim">
                  {rewindOffset + 1}-{Math.min(rewindOffset + rewindVisibleLimit, rewindCheckpoints.length)} of {rewindCheckpoints.length}
                </Text>
              )}
            </Box>
            <Text color="dim">
              Restores conversation and tracked files; external shell changes aren't tracked. Code-only restores do not prune history.
            </Text>
          </Box>
        )
      },
    })
  }

  if (todosOpen) {
    modalStack.push({
      id: "todos",
      render: () => {
        const scroll = Math.max(0, Math.min(todoScroll, todoMaxScroll))
        const visible = todoRows.slice(scroll, scroll + todoViewportRows)
        const colorForStatus = (status?: string) => {
          switch (status) {
            case "in_progress":
              return "#38BDF8"
            case "done":
              return "#34D399"
            case "blocked":
              return "#F87171"
            case "canceled":
              return "#A8A29E"
            default:
              return "#FACC15"
          }
        }
        return (
          <Box
            flexDirection="column"
            borderStyle="round"
            borderColor="#7CF2FF"
            paddingX={2}
            paddingY={1}
            width={PANEL_WIDTH}
            alignSelf="center"
            marginTop={2}
          >
            <Text color="#7CF2FF">{chalk.bold("Todos")}</Text>
            <Text color="gray">
              {todos.length === 0
                ? "No todos yet."
                : `${todos.length} item${todos.length === 1 ? "" : "s"} • ↑/↓ scroll • PgUp/PgDn page • Esc close`}
            </Text>
            <Box flexDirection="column" marginTop={1}>
              {todoRows.length === 0 ? (
                <Text color="dim">TodoWrite output will appear here once the agent updates the board.</Text>
              ) : (
                visible.map((row, idx) =>
                  row.kind === "header" ? (
                    <Text key={`todo-h-${scroll}-${idx}`} color={colorForStatus(row.status)}>
                      {row.label}
                    </Text>
                  ) : (
                    <Text key={`todo-i-${scroll}-${idx}`} wrap="truncate-end">
                      {"  "}• {row.label}
                    </Text>
                  ),
                )
              )}
            </Box>
            {todoRows.length > todoViewportRows && (
              <Text color="dim">
                {scroll + 1}-{Math.min(scroll + todoViewportRows, todoRows.length)} of {todoRows.length}
              </Text>
            )}
          </Box>
        )
      },
    })
  }

  if (permissionRequest) {
    modalStack.push({
      id: "permission",
      render: () => {
        const queue = Math.max(0, permissionQueueDepth ?? 0)
        const queueText = queue > 0 ? ` • +${queue} queued` : ""
        const rewindableText = permissionRequest.rewindable ? "rewindable" : "not rewindable"
        const diffAvailable = Boolean(permissionRequest.diffText)
        const activeTabLabel = (tab: "summary" | "diff" | "rules" | "note", label: string) =>
          permissionTab === tab ? chalk.bold.cyan(label) : chalk.gray(label)
        const tabLine = [
          activeTabLabel("summary", "Summary"),
          activeTabLabel("diff", diffAvailable ? "Diff" : "Diff (none)"),
          activeTabLabel("rules", "Rules"),
          activeTabLabel("note", "Note"),
        ].join(chalk.gray("  |  "))

        const diffScrollMax = Math.max(0, permissionDiffLines.length - permissionViewportRows)
        const diffScroll = Math.max(0, Math.min(permissionScroll, diffScrollMax))
        const visibleDiff = permissionDiffLines.slice(diffScroll, diffScroll + permissionViewportRows)

        const scopeLabel = (scope: PermissionRuleScope, label: string) =>
          permissionScope === scope ? chalk.bold.cyan(label) : chalk.gray(label)

        return (
          <Box
            flexDirection="column"
            borderStyle="round"
            borderColor="#F97316"
            paddingX={2}
            paddingY={1}
            width={PANEL_WIDTH}
            alignSelf="center"
            marginTop={2}
          >
            <Text color="#FACC15">
              {chalk.bold("Permission required")} {chalk.dim(`(${permissionRequest.tool})`)}
            </Text>
            <Text color="dim">
              {permissionRequest.kind} • {rewindableText}
              {queueText}
            </Text>
            <Text>{tabLine}</Text>

            {permissionTab === "summary" && (
              <Box flexDirection="column" marginTop={1}>
                <Text wrap="wrap">{permissionRequest.summary}</Text>
                {permissionDiffPreview && (
                  <Text color="dim">
                    Diff: +{permissionDiffPreview.additions} -{permissionDiffPreview.deletions}
                    {permissionDiffPreview.files.length > 0 ? ` • ${permissionDiffPreview.files.join(", ")}` : ""}
                  </Text>
                )}
                {!permissionDiffPreview && diffAvailable && <Text color="dim">Diff attached.</Text>}
                {!diffAvailable && <Text color="dim">No diff attached.</Text>}
              </Box>
            )}

            {permissionTab === "diff" && (
              <Box flexDirection="column" marginTop={1}>
                {!diffAvailable ? (
                  <Text color="dim">No diff attached.</Text>
                ) : (
                  <>
                    <Text color="gray" wrap="truncate-end">
                      {permissionSelectedSection
                        ? `File ${permissionSelectedFileIndex + 1}/${permissionDiffSections.length}: ${permissionSelectedSection.file}`
                        : "Diff"}
                    </Text>
                    <Text color="dim">
                      Use ↑/↓ or PgUp/PgDn to scroll{permissionDiffSections.length > 1 ? " • ←/→ to switch files" : ""}.
                      {permissionDiffLines.length > 0
                        ? ` Lines ${diffScroll + 1}-${Math.min(diffScroll + permissionViewportRows, permissionDiffLines.length)} of ${permissionDiffLines.length}.`
                        : ""}
                    </Text>
                    <Box flexDirection="column" marginTop={1}>
                      {visibleDiff.length === 0 ? (
                        <Text color="dim">Diff is empty.</Text>
                      ) : (
                        visibleDiff.map((line, index) => (
                          <Text key={`perm-diff-${diffScroll}-${index}`} wrap="truncate-end">
                            {line}
                          </Text>
                        ))
                      )}
                    </Box>
                  </>
                )}
              </Box>
            )}

            {permissionTab === "rules" && (
              <Box flexDirection="column" marginTop={1}>
                <Text color="gray">Default scope for always-allow: project</Text>
                <Text>
                  Scope: {scopeLabel("session", "Session")} {scopeLabel("project", "Project")} {scopeLabel("global", "Global")} {chalk.dim("(←/→)")}
                </Text>
                <Text color="dim">
                  Suggested rule: {permissionRequest.ruleSuggestion ? chalk.italic(permissionRequest.ruleSuggestion) : chalk.dim("<none>")}
                </Text>
                <Text color="dim">Press 2 or Shift+Tab to allow always at the selected scope.</Text>
              </Box>
            )}

            {permissionTab === "note" && (
              <Box flexDirection="column" marginTop={1}>
                <Text color="gray">Tell me what to do differently:</Text>
                <Text wrap="truncate-end">{renderPermissionNoteLine(permissionNote, permissionNoteCursor)}</Text>
                <Text color="dim">Type to edit • Enter deny once with note • Tab switch panels</Text>
              </Box>
            )}

            <Box flexDirection="column" marginTop={1}>
              <Text color="gray">
                Tab switch panel • Enter allow once • Shift+Tab allow always • 1 allow once • 2 allow always • 3 feedback • d deny once • D deny always
              </Text>
              <Text color="#FB7185">Esc deny and stop run</Text>
            </Box>
          </Box>
        )
      },
    })
  }

  return (
    <Box flexDirection="column">
      {SCROLLBACK_MODE && (
        <Static items={staticFeed}>
          {(item) => <React.Fragment key={item.id}>{item.node}</React.Fragment>}
        </Static>
      )}
      {transcriptViewerOpen ? (
        <TranscriptViewer
          lines={transcriptViewerLines}
          cols={columnWidth}
          rows={rowCount}
          scroll={transcriptViewerEffectiveScroll}
          searchQuery={transcriptSearchOpen ? transcriptSearchQuery : ""}
          matchLines={transcriptSearchOpen ? transcriptSearchLineMatches : undefined}
          matchCount={transcriptSearchOpen ? transcriptSearchMatches.length : undefined}
          activeMatchIndex={transcriptSearchOpen && transcriptSearchMatches.length > 0 ? transcriptSearchSafeIndex : undefined}
          activeMatchLine={transcriptSearchOpen ? transcriptSearchActiveLine : null}
          toggleHint={keymap === "claude" ? "Ctrl+Shift+T transcript" : "Ctrl+T transcript"}
          detailLabel={verboseOutput ? "Showing detailed transcript · Ctrl+O to toggle" : undefined}
        />
      ) : (
        <ModalHost stack={modalStack}>{baseContent}</ModalHost>
      )}
    </Box>
  )
}
