import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Box, Static, Text, useStdout } from "ink"
import chalk from "chalk"
import path from "node:path"
import { promises as fs } from "node:fs"
import type { Block, InlineNode } from "@stream-mdx/core/types"
import type {
  ConversationEntry,
  LiveSlotEntry,
  StreamStats,
  ModelMenuState,
  ModelMenuItem,
  SkillsMenuState,
  SkillEntry,
  SkillSelection,
  CTreeSnapshot,
  GuardrailNotice,
  QueuedAttachment,
  TranscriptPreferences,
  ToolLogEntry,
  TodoItem,
  TaskEntry,
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
import { SelectPanel, type SelectPanelRow, type SelectPanelLine } from "./SelectPanel.js"
import { useAnimationClock } from "../hooks/useAnimationClock.js"
import type { ClipboardImage } from "../../util/clipboard.js"
import type { SessionFileInfo, SessionFileContent } from "../../api/types.js"
import { loadFileMentionConfig, type FileMentionMode } from "../fileMentions.js"
import { loadFilePickerConfig, loadFilePickerResources, type FilePickerMode, type FilePickerResource } from "../filePicker.js"
import { rankFuzzyFileItems, scoreFuzzyMatch } from "../fileRanking.js"
import { computeModelColumns, CONTEXT_COLUMN_WIDTH, PRICE_COLUMN_WIDTH } from "../modelMenu/layout.js"
import { GuardrailBanner } from "./GuardrailBanner.js"
import { loadKeymapConfig } from "../keymap.js"
import { loadChromeMode } from "../chrome.js"
import { ensureShikiLoaded, maybeHighlightCode, subscribeShiki } from "../shikiHighlighter.js"
import { getSessionDraft, updateSessionDraft } from "../../cache/sessionCache.js"
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
const SKILL_GROUP_ORDER = ["language", "repo", "lint", "search", "docs", "misc"]
const ALWAYS_ALLOW_SCOPE: PermissionRuleScope = "project"
const DOUBLE_CTRL_C_WINDOW_MS = 1500
const CLI_VERSION = (process.env.BREADBOARD_TUI_VERSION ?? "0.2.0").trim()
const formatBytes = (bytes: number): string => {
  if (bytes < 1_000) return `${bytes} B`
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)} KB`
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`
  return `${(bytes / 1_000_000_000).toFixed(2)} GB`
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
const formatCtreeSummary = (snapshot?: CTreeSnapshot | null): string | null => {
  if (!snapshot) return null
  const snap = snapshot.snapshot as Record<string, unknown> | null | undefined
  const compiler = snapshot.compiler as Record<string, unknown> | null | undefined
  const collapse = snapshot.collapse as Record<string, unknown> | null | undefined
  const runner = snapshot.runner as Record<string, unknown> | null | undefined
  const nodeCount = typeof snap?.node_count === "number" ? snap.node_count : null
  const nodeHash = typeof snap?.node_hash === "string" ? snap.node_hash : null
  const policy = typeof collapse?.policy === "string" ? collapse.policy : null
  const dropped = typeof collapse?.dropped === "number" ? collapse.dropped : null
  const compilerHash =
    typeof compiler?.z3 === "string"
      ? compiler.z3
      : typeof compiler?.z2 === "string"
        ? compiler.z2
        : typeof compiler?.z1 === "string"
          ? compiler.z1
          : null
  const runnerEnabled = typeof runner?.enabled === "boolean" ? runner.enabled : null
  const branches = Array.isArray(runner?.branches) ? runner?.branches.length : null
  const parts: string[] = []
  if (policy) parts.push(`policy ${policy}`)
  if (nodeCount != null) parts.push(`${nodeCount} nodes`)
  if (nodeHash) parts.push(`hash ${nodeHash.slice(0, 8)}`)
  if (compilerHash) parts.push(`compiler ${compilerHash.slice(0, 8)}`)
  if (dropped != null && dropped > 0) parts.push(`dropped ${dropped}`)
  if (runnerEnabled != null) {
    parts.push(runnerEnabled ? `branches ${branches ?? 0}` : "runner off")
  }
  if (parts.length === 0) return null
  return `CTree: ${parts.join(" · ")}`
}
const normalizeModeLabel = (value?: string | null): { label: string; color: string } => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "plan") return { label: "PLAN", color: "#f97316" }
  if (normalized === "auto") return { label: "AUTO", color: "#a855f7" }
  if (normalized && normalized !== "build") return { label: normalized.toUpperCase(), color: "#60a5fa" }
  return { label: "NORMAL", color: "#60a5fa" }
}

const normalizePermissionLabel = (value?: string | null): { label: string; color: string } => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (!normalized || ["auto", "allow", "auto-accept", "auto_accept"].includes(normalized)) {
    return { label: "AUTO-ACCEPT", color: "#22c55e" }
  }
  if (["prompt", "ask", "interactive"].includes(normalized)) {
    return { label: "PROMPT", color: "#f59e0b" }
  }
  return { label: normalized.toUpperCase(), color: "#f59e0b" }
}
const COLUMN_SEPARATOR = "  ·  "
const clearToEnd = (value: string): string => `${value}\u001b[K`

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
      out += chalk.bold.hex("#7CF2FF")(ch)
    } else {
      out += ch
    }
  }
  return out
}

const normalizeProviderKey = (value: string | null | undefined): string => {
  const normalized = value?.trim().toLowerCase() ?? ""
  if (!normalized) return "other"
  if (normalized === "x-ai") return "xai"
  return normalized
}

const formatProviderLabel = (value: string | null | undefined): string => {
  const normalized = normalizeProviderKey(value)
  switch (normalized) {
    case "openai":
      return "OpenAI"
    case "openrouter":
      return "OpenRouter"
    case "anthropic":
      return "Anthropic"
    case "google":
      return "Google"
    case "xai":
      return "xAI"
    case "mistral":
      return "Mistral"
    case "meta":
      return "Meta"
    case "cohere":
      return "Cohere"
    case "deepseek":
      return "DeepSeek"
    case "local":
      return "Local"
    case "other":
      return "Other"
    default: {
      const raw = value?.trim()
      if (!raw) return "Other"
      return raw
        .split(/[^a-z0-9]+/i)
        .filter((part) => part.length > 0)
        .map((part) => part[0].toUpperCase() + part.slice(1))
        .join(" ")
    }
  }
}

const buildSkillKey = (skill: SkillEntry): string => `${skill.id}@${skill.version}`
const isSkillSelected = (selected: Set<string>, skill: SkillEntry): boolean =>
  selected.has(skill.id) || selected.has(buildSkillKey(skill))

const stripProviderPrefix = (label: string, providerLabel: string): string => {
  const trimmed = label.trim()
  if (!providerLabel) return trimmed
  const escaped = providerLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const pattern = new RegExp(`^${escaped}\\s*[·:-]\\s+`, "i")
  return trimmed.replace(pattern, "")
}

const formatProviderCell = (item: ModelMenuItem, width: number, query: string): string => {
  const currentGlyph = item.isCurrent ? "● " : "  "
  const defaultGlyph = item.isDefault ? " ★" : ""
  const providerLabel = formatProviderLabel(item.provider)
  const displayLabel = stripProviderPrefix(item.label, providerLabel)
  const formatted = formatCell(`${currentGlyph}${displayLabel}${defaultGlyph}`, width, "left")
  return query.trim().length > 0 ? highlightFuzzyLabel(formatted, formatted, query) : formatted
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

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

const extractToolPayload = (
  entry: ToolLogEntry,
): { name: string; args?: Record<string, unknown>; output?: string; status?: string } | null => {
  const trimmed = entry.text.trim()
  const bracket = trimmed.indexOf("]")
  if (bracket === -1) return null
  const jsonText = trimmed.slice(bracket + 1).trim()
  if (!jsonText.startsWith("{")) return null
  try {
    const payload = JSON.parse(jsonText) as unknown
    const tool = isRecord(payload) && isRecord(payload.tool) ? payload.tool : (payload as Record<string, unknown>)
    const getString = (value: unknown): string | undefined => (typeof value === "string" ? value : undefined)
    const name =
      getString(tool.name) ||
      getString(tool.tool) ||
      getString(tool.command) ||
      "Tool"
    const args = isRecord(tool.args) ? (tool.args as Record<string, unknown>) : undefined
    const output =
      getString(tool.output) ||
      getString(tool.stdout) ||
      getString(tool.content) ||
      getString(isRecord(payload) ? payload.output : undefined) ||
      getString(isRecord(payload) ? payload.stdout : undefined) ||
      getString(isRecord(payload) ? payload.content : undefined)
    const status =
      getString(tool.status) || getString(isRecord(payload) ? payload.status : undefined)
    return { name, args, output, status }
  } catch {
    return null
  }
}

const normalizeToolName = (raw: string): string => {
  const lower = raw.toLowerCase()
  const map: Record<string, string> = {
    bash: "Bash",
    shell: "Bash",
    apply_patch: "ApplyPatch",
    write_file: "Write",
    read_file: "Read",
    edit: "Edit",
    search: "Search",
  }
  if (map[lower]) return map[lower]
  return raw.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())
}

const formatToolArgsSummary = (args?: Record<string, unknown>): string | null => {
  if (!args) return null
  const preferredKeys = ["command", "cmd", "path", "file", "query", "pattern", "url", "target", "input"]
  for (const key of preferredKeys) {
    const value = args[key]
    if (typeof value === "string" && value.trim().length > 0) {
      const trimmed = value.replace(/\s+/g, " ").trim()
      const clipped = trimmed.length > 80 ? `${trimmed.slice(0, 77)}…` : trimmed
      return clipped
    }
  }
  if (typeof args.diff === "string" || typeof args.patch === "string") return "diff"
  return null
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
  const content = (code || raw).replace(/\r\n?/g, "\n")
  const isDiff = finalLang ? finalLang.toLowerCase().includes("diff") : false
  if (!isDiff) {
    const shikiLines = maybeHighlightCode(content, finalLang)
    if (shikiLines) return shikiLines
  }
  const lines = content.split("\n")
  return lines.map((line) => {
    if (isDiff) return colorDiffLine(line)
    if (line.startsWith("+")) return chalk.hex("#34d399")(line)
    if (line.startsWith("-")) return chalk.hex("#fb7185")(line)
    if (line.startsWith("@@")) return chalk.hex("#22d3ee")(line)
    return chalk.hex("#cbd5e1")(line)
  })
}

const renderTableLines = (raw: string): string[] => {
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  if (lines.length === 0) return []
  const rows = lines.map((line) => {
    const parts = line.split("|").map((part) => part.trim())
    if (parts.length > 1 && parts[0] === "") parts.shift()
    if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop()
    return parts
  })
  const isSeparator = (cells: string[]) => cells.every((cell) => /^:?-{2,}:?$/.test(cell))
  const bodyRows = rows.filter((cells) => !isSeparator(cells))
  if (bodyRows.length === 0) return lines
  const colCount = Math.max(...bodyRows.map((cells) => cells.length))
  const widths = new Array<number>(colCount).fill(0)
  for (const cells of bodyRows) {
    for (let i = 0; i < colCount; i += 1) {
      widths[i] = Math.max(widths[i], stripAnsiCodes(cells[i] ?? "").length)
    }
  }
  const formatRow = (cells: string[], style?: (value: string) => string): string => {
    const padded = cells.map((cell, idx) => {
      const rawCell = cell ?? ""
      const pad = Math.max(0, widths[idx] - stripAnsiCodes(rawCell).length)
      const text = `${rawCell}${" ".repeat(pad)}`
      return style ? style(text) : text
    })
    return `| ${padded.join(" | ")} |`
  }
  const output: string[] = []
  let headerRendered = false
  for (const cells of rows) {
    if (isSeparator(cells)) {
      const divider = widths.map((width) => "-".repeat(Math.max(3, width))).join("-|-")
      output.push(chalk.dim(`|- ${divider} -|`.replace(/\s+/g, " ")))
      headerRendered = true
      continue
    }
    const styled = !headerRendered ? (value: string) => chalk.bold.hex("#e2e8f0")(value) : undefined
    output.push(formatRow(cells, styled))
  }
  return output.length > 0 ? output : lines
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

const trimTailByLineCount = <T,>(
  entries: ReadonlyArray<T>,
  linesToTrim: number,
  measure: (entry: T) => number,
): ReadonlyArray<T> => {
  const trim = Math.max(0, Math.floor(linesToTrim))
  if (trim === 0 || entries.length === 0) return entries
  let remaining = trim
  let end = entries.length
  while (end > 0 && remaining > 0) {
    const cost = Math.max(1, measure(entries[end - 1]))
    remaining -= cost
    end -= 1
  }
  return entries.slice(0, Math.max(0, end))
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
      return (block.payload.raw ?? "").split(/\r?\n/)
    case "table": {
      const raw = block.payload.raw ?? ""
      return renderTableLines(raw)
    }
    case "mdx":
    case "html": {
      const raw = block.payload.raw ?? ""
      const lines = raw.split(/\r?\n/)
      return lines.map((line) => chalk.dim(line))
    }
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
  readonly disconnected: boolean
  readonly mode?: string | null
  readonly permissionMode?: string | null
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly skillsMenu: SkillsMenuState
  readonly guardrailNotice?: GuardrailNotice | null
  readonly viewClearAt?: number | null
  readonly viewPrefs: TranscriptPreferences
  readonly todos: TodoItem[]
  readonly tasks: TaskEntry[]
  readonly ctreeSnapshot?: CTreeSnapshot | null
  readonly permissionRequest?: PermissionRequest | null
  readonly permissionError?: string | null
  readonly permissionQueueDepth?: number
  readonly rewindMenu: RewindMenuState
  readonly onSubmit: (value: string, attachments?: ReadonlyArray<QueuedAttachment>) => Promise<void>
  readonly onModelMenuOpen: () => Promise<void>
  readonly onModelSelect: (item: ModelMenuItem) => Promise<void>
  readonly onModelMenuCancel: () => void
  readonly onSkillsMenuOpen: () => Promise<void>
  readonly onSkillsMenuCancel: () => void
  readonly onSkillsApply: (selection: SkillSelection) => Promise<void>
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
  conversation: conversationProp,
  toolEvents: toolEventsProp,
  liveSlots,
  status,
  pendingResponse,
  disconnected,
  mode,
  permissionMode,
  hints,
  stats,
  modelMenu,
  skillsMenu,
  guardrailNotice,
  viewClearAt,
  viewPrefs,
  todos,
  tasks,
  ctreeSnapshot,
  permissionRequest,
  permissionError,
  permissionQueueDepth,
  rewindMenu,
  onSubmit,
  onModelMenuOpen,
  onModelSelect,
  onModelMenuCancel,
  onSkillsMenuOpen,
  onSkillsMenuCancel,
  onSkillsApply,
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
  const [suppressSuggestions, setSuppressSuggestions] = useState(false)
  const [attachments, setAttachments] = useState<QueuedAttachment[]>([])
  const [fileMentions, setFileMentions] = useState<QueuedFileMention[]>([])
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
  const removeLastAttachment = useCallback(() => {
    setAttachments((prev) => prev.slice(0, -1))
  }, [])
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
  const shortcutsOpenedAtRef = useRef<number | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [modelSearch, setModelSearch] = useState("")
  const [modelIndex, setModelIndex] = useState(0)
  const [modelOffset, setModelOffset] = useState(0)
  const [modelProviderFilter, setModelProviderFilter] = useState<string | null>(null)
  const [skillsSearch, setSkillsSearch] = useState("")
  const [skillsIndex, setSkillsIndex] = useState(0)
  const [skillsOffset, setSkillsOffset] = useState(0)
  const [skillsMode, setSkillsMode] = useState<"allowlist" | "blocklist">("blocklist")
  const [skillsSelected, setSkillsSelected] = useState<Set<string>>(new Set())
  const [skillsDirty, setSkillsDirty] = useState(false)
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
  const [tasksOpen, setTasksOpen] = useState(false)
  const [taskScroll, setTaskScroll] = useState(0)
  const [taskIndex, setTaskIndex] = useState(0)
  const [taskNotice, setTaskNotice] = useState<string | null>(null)
  const [taskSearchQuery, setTaskSearchQuery] = useState("")
  const [taskStatusFilter, setTaskStatusFilter] = useState<"all" | "running" | "completed" | "failed">("all")
  const [taskTailLines, setTaskTailLines] = useState<string[]>([])
  const [taskTailPath, setTaskTailPath] = useState<string | null>(null)
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
  const fileMenuRowsRef = useRef<FileMenuRow[]>([])
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
  const [transcriptExportNotice, setTranscriptExportNotice] = useState<string | null>(null)
  const [transcriptToolIndex, setTranscriptToolIndex] = useState(0)
  const [transcriptNudge, setTranscriptNudge] = useState(0)
  const draftAppliedRef = useRef(false)
  const draftLoadSeq = useRef(0)
  const draftSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSavedDraftRef = useRef<{ text: string; cursor: number } | null>(null)
  const { stdout } = useStdout()
  const [, forceShikiRefresh] = useState(0)
  const [staticFeed, setStaticFeed] = useState<StaticFeedItem[]>([])
  const conversation = useMemo(() => {
    if (!viewClearAt) return conversationProp
    return conversationProp.filter((entry) => entry.createdAt >= viewClearAt)
  }, [conversationProp, viewClearAt])
  const toolEvents = useMemo(() => {
    if (!viewClearAt) return toolEventsProp
    return toolEventsProp.filter((entry) => entry.createdAt >= viewClearAt)
  }, [toolEventsProp, viewClearAt])
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
  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    headerPrintedRef.current = false
    printedConversationIdsRef.current.clear()
    printedToolIdsRef.current.clear()
    setStaticFeed([])
  }, [sessionId, viewClearAt])
  const fileMentionConfig = useMemo(() => loadFileMentionConfig(), [])
  const filePickerConfig = useMemo(() => loadFilePickerConfig(), [])
  const keymap = useMemo(() => loadKeymapConfig(), [])
  const chromeMode = useMemo(() => loadChromeMode(keymap), [keymap])
  const claudeChrome = chromeMode === "claude"
  const filePickerResources = useMemo(() => loadFilePickerResources(), [])
  const [, forceRedraw] = useState(0)
  useEffect(() => {
    if (!viewPrefs.richMarkdown) return
    const unsubscribe = subscribeShiki(() => {
      forceShikiRefresh((value) => value + 1)
    })
    ensureShikiLoaded()
    return unsubscribe
  }, [forceShikiRefresh, viewPrefs.richMarkdown])
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
  const modelPanelInnerWidth = useMemo(() => Math.max(0, PANEL_WIDTH - 4), [PANEL_WIDTH])
  const modelColumnLayout = useMemo(() => computeModelColumns(columnWidth), [columnWidth])
  const modelMenuCompact = useMemo(() => rowCount <= 20 || columnWidth <= 70, [columnWidth, rowCount])
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
      const cells = [formatProviderCell(item, modelColumnLayout.providerWidth, modelSearch)]
      if (modelColumnLayout.showContext) cells.push(formatContextCell(item.contextTokens ?? null, CONTEXT_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceIn) cells.push(formatPriceCell(item.priceInPerM ?? null, PRICE_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceOut) cells.push(formatPriceCell(item.priceOutPerM ?? null, PRICE_COLUMN_WIDTH))
      return buildModelRowText(cells)
    },
    [modelColumnLayout, modelSearch],
  )
  const CONTENT_PADDING = 6
  const MAX_VISIBLE_MODELS = 8
  const MODEL_VISIBLE_ROWS = useMemo(() => {
    const base = Math.floor(rowCount * 0.45)
    const capped = Math.max(6, Math.min(14, base))
    return Math.max(4, Math.min(capped, rowCount - 10))
  }, [rowCount])
  const SKILLS_VISIBLE_ROWS = useMemo(() => {
    const base = Math.floor(rowCount * 0.5)
    const capped = Math.max(8, Math.min(18, base))
    return Math.max(6, Math.min(capped, rowCount - 8))
  }, [rowCount])
  const animationActive = pendingResponse || liveSlots.length > 0
  const animationTick = useAnimationClock(animationActive, 120)
  const spinner = useSpinner(pendingResponse, animationActive ? animationTick : undefined)
  const suggestions = useMemo(
    () => (suppressSuggestions ? [] : buildSuggestions(input, MAX_SUGGESTIONS)),
    [input, suppressSuggestions],
  )
  const activeSlashQuery = useMemo(() => {
    if (!input.startsWith("/")) return ""
    const [lookup] = input.slice(1).split(/\s+/)
    return lookup ?? ""
  }, [input])
  const maxVisibleSuggestions = useMemo(() => {
    if (claudeChrome) {
      return Math.max(12, Math.min(20, Math.floor(rowCount * 0.6)))
    }
    return Math.max(8, Math.min(14, Math.floor(rowCount * 0.4)))
  }, [claudeChrome, rowCount])
  const inputMaxVisibleLines = useMemo(
    () => Math.max(3, Math.min(8, Math.floor(rowCount * 0.25))),
    [rowCount],
  )
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
  const suggestionPrefix = useMemo(() => (claudeChrome ? "  " : ""), [claudeChrome])
  const suggestionCommandWidth = useMemo(() => {
    if (!claudeChrome) return null
    const maxLabel = suggestions.reduce((max, row) => {
      const label = `${row.command}${row.usage ? ` ${row.usage}` : ""}`
      return Math.max(max, stripAnsiCodes(label).length)
    }, 0)
    const padded = maxLabel > 0 ? maxLabel + 2 : 18
    const maxAllowed = Math.max(18, Math.min(32, Math.floor((columnWidth - 6) * 0.45)))
    return Math.min(Math.max(16, padded), maxAllowed)
  }, [claudeChrome, columnWidth, suggestions])
  const suggestionLayout = useMemo(() => {
    const maxWidth = claudeChrome ? columnWidth - suggestionPrefix.length - 2 : Math.min(columnWidth - 4, 72)
    const totalWidth = Math.max(24, maxWidth)
    const baseCommandWidth = Math.max(14, Math.min(28, Math.floor(totalWidth * 0.35)))
    const commandWidth = claudeChrome && suggestionCommandWidth ? suggestionCommandWidth : baseCommandWidth
    const summaryWidth = Math.max(8, totalWidth - commandWidth - 2)
    return { totalWidth, commandWidth, summaryWidth }
  }, [claudeChrome, columnWidth, suggestionCommandWidth, suggestionPrefix.length])
  const buildSuggestionLines = useCallback(
    (row: SlashSuggestion, multiline: boolean): Array<{ label: string; summary: string }> => {
      const label = `${row.command}${row.usage ? ` ${row.usage}` : ""}`
      const summaryText = row.shortcut ? `${row.summary} · ${row.shortcut}` : row.summary
      if (!multiline) {
        return [{ label, summary: summaryText }]
      }
      const summaryMaxLines = 2
      const summaryLines = wrapSuggestionText(summaryText, suggestionLayout.summaryWidth, summaryMaxLines)
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
    skillsMenu.status !== "hidden" ||
    paletteState.status === "open" ||
    confirmState.status === "prompt" ||
    shortcutsOpen ||
    usageOpen ||
    Boolean(permissionRequest) ||
    rewindMenu.status !== "hidden" ||
    todosOpen ||
    tasksOpen ||
    transcriptViewerOpen

  const overlayActive =
    modelMenu.status !== "hidden" ||
    skillsMenu.status !== "hidden" ||
    paletteState.status === "open" ||
    confirmState.status === "prompt" ||
    (shortcutsOpen && !claudeChrome) ||
    usageOpen ||
    Boolean(permissionRequest) ||
    rewindMenu.status !== "hidden" ||
    todosOpen ||
    tasksOpen ||
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
  const rawFilePickerNeedle = filePickerQueryParts.needle.trim()
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
    if (trimmed === filePickerNeedle) return
    const seq = (filePickerNeedleSeq.current += 1)
    const timer = setTimeout(() => {
      if (filePickerNeedleSeq.current !== seq) return
      setFilePickerNeedle(trimmed)
    }, 80)
    return () => clearTimeout(timer)
  }, [filePickerActive, filePickerConfig.mode, filePickerNeedle, filePickerQueryParts.needle])

  const transcriptViewerLines = useMemo(() => {
    const lines: string[] = []
    const normalizeNewlines = (text: string) => text.replace(/\r\n?/g, "\n")
    const detailedTranscriptActive = verboseOutput || (keymap === "claude" && transcriptViewerOpen)
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
        if (detailedTranscriptActive || toolLines.length <= TOOL_COLLAPSE_THRESHOLD) {
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
  }, [conversation, keymap, toolEvents, transcriptViewerOpen, verboseOutput])
  const transcriptToolLines = useMemo(() => {
    const indices: number[] = []
    for (let line = 0; line < transcriptViewerLines.length; line += 1) {
      const value = transcriptViewerLines[line] ?? ""
      if (value.startsWith("  [")) {
        indices.push(line)
      }
    }
    return indices
  }, [transcriptViewerLines])
  useEffect(() => {
    if (!transcriptViewerOpen) return
    setTranscriptToolIndex(0)
  }, [transcriptToolLines.length, transcriptViewerOpen])

  const transcriptViewerBodyRows = useMemo(
    () => Math.max(1, rowCount - (verboseOutput ? 5 : 4)),
    [rowCount, verboseOutput],
  )
  const transcriptViewerMaxScroll = useMemo(
    () => Math.max(0, transcriptViewerLines.length - transcriptViewerBodyRows),
    [transcriptViewerBodyRows, transcriptViewerLines.length],
  )
  const transcriptViewerEffectiveScroll = useMemo(() => {
    if (transcriptViewerFollowTail) return transcriptViewerMaxScroll
    return Math.max(0, Math.min(transcriptViewerScroll, transcriptViewerMaxScroll))
  }, [transcriptViewerFollowTail, transcriptViewerMaxScroll, transcriptViewerScroll])

  const transcriptSearchMatches = useMemo<ReadonlyArray<TranscriptMatch>>(() => {
    const query = transcriptSearchQuery.trim()
    if (!query) return []
    const matches: TranscriptMatch[] = []
    for (let line = 0; line < transcriptViewerLines.length; line += 1) {
      const candidate = stripAnsiCodes(transcriptViewerLines[line] ?? "")
      const score = scoreFuzzyMatch(candidate, query)
      if (score == null) continue
      matches.push({ line, start: 0, end: candidate.length })
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
  const sortedTasks = useMemo(
    () => [...tasks].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0)),
    [tasks],
  )
  const filteredTasks = useMemo(() => {
    const query = taskSearchQuery.trim().toLowerCase()
    const statusFilter = taskStatusFilter
    return sortedTasks.filter((task) => {
      if (statusFilter !== "all") {
        const normalized = (task.status ?? "").toLowerCase()
        if (!normalized || !normalized.includes(statusFilter)) {
          return false
        }
      }
      if (!query) return true
      const haystack = [
        task.id,
        task.description ?? "",
        task.subagentType ?? "",
        task.status ?? "",
        task.kind ?? "",
        task.sessionId ?? "",
      ]
        .join(" ")
        .toLowerCase()
      return haystack.includes(query)
    })
  }, [sortedTasks, taskSearchQuery, taskStatusFilter])
  const taskRows = useMemo(() => {
    return filteredTasks.map((task) => {
      const status = (task.status ?? "").toLowerCase()
      const labelParts = []
      const description = task.description || task.kind || "task"
      labelParts.push(description)
      if (task.subagentType) {
        labelParts.push(`(${task.subagentType})`)
      }
      return {
        id: task.id,
        status,
        label: labelParts.join(" "),
        task,
      }
    })
  }, [filteredTasks])
  const taskViewportRows = useMemo(() => Math.max(8, Math.min(16, Math.floor(rowCount * 0.45))), [rowCount])
  const taskMaxScroll = Math.max(0, taskRows.length - taskViewportRows)
  const selectedTaskIndex = useMemo(() => {
    if (taskRows.length === 0) return 0
    return Math.max(0, Math.min(taskIndex, taskRows.length - 1))
  }, [taskIndex, taskRows.length])
  const selectedTask = useMemo(() => taskRows[selectedTaskIndex]?.task ?? null, [selectedTaskIndex, taskRows])

  const requestTaskTail = useCallback(async () => {
    if (!selectedTask) {
      setTaskNotice("No task selected.")
      return
    }
    const candidates: string[] = []
    if (selectedTask.artifactPath) candidates.push(selectedTask.artifactPath)
    const taskId = selectedTask.id
    candidates.push(`.kyle/subagents/agent-${taskId}.jsonl`)
    candidates.push(`.kyle/subagents/${taskId}.jsonl`)
    candidates.push(`.kyle/subagents/${taskId}.json`)
    let lastError: string | null = null
    for (const pathCandidate of candidates) {
      try {
        const content = await onReadFile(pathCandidate, { mode: "snippet", headLines: 4, tailLines: 24, maxBytes: 40_000 })
        const lines = content.content.replace(/\r\n?/g, "\n").split("\n")
        setTaskTailLines(lines)
        setTaskTailPath(content.path)
        const truncated = content.truncated ? " (truncated)" : ""
        setTaskNotice(`Loaded ${content.path}${truncated}`)
        return
      } catch (error) {
        lastError = (error as Error).message
      }
    }
    setTaskNotice(lastError ?? "Unable to load task output.")
  }, [onReadFile, selectedTask])

  const skillsCatalog = useMemo(() => (skillsMenu.status === "ready" ? skillsMenu.catalog : null), [skillsMenu])
  const skillsSelection = useMemo(
    () => (skillsMenu.status === "ready" ? skillsMenu.selection : null),
    [skillsMenu],
  )
  const skillsSources = useMemo(
    () => (skillsMenu.status === "ready" ? skillsMenu.sources ?? null : null),
    [skillsMenu],
  )

  const skillsData = useMemo(() => {
    const entries = skillsCatalog?.skills ?? []
    const query = skillsSearch.trim().toLowerCase()
    const normalizeGroup = (value?: string | null) => {
      const normalized = value?.trim().toLowerCase() ?? ""
      return normalized || "misc"
    }
    const formatGroupLabel = (group: string) =>
      group
        .split(/[^a-z0-9]+/i)
        .filter(Boolean)
        .map((part) => part[0]?.toUpperCase() + part.slice(1))
        .join(" ")
    type SkillRow =
      | { kind: "header"; label: string }
      | { kind: "item"; entry: SkillEntry; index: number }
    if (!query) {
      const grouped = new Map<string, SkillEntry[]>()
      for (const entry of entries) {
        const key = normalizeGroup(entry.group)
        if (!grouped.has(key)) grouped.set(key, [])
        grouped.get(key)!.push(entry)
      }
      const order: string[] = []
      for (const group of SKILL_GROUP_ORDER) {
        if (grouped.has(group)) order.push(group)
      }
      for (const group of grouped.keys()) {
        if (!order.includes(group)) order.push(group)
      }
      const items: SkillEntry[] = []
      const rows: SkillRow[] = []
      for (const group of order) {
        const groupEntries = grouped.get(group) ?? []
        groupEntries.sort((a, b) => {
          const al = (a.label ?? a.id).toLowerCase()
          const bl = (b.label ?? b.id).toLowerCase()
          if (al !== bl) return al.localeCompare(bl)
          return a.version.localeCompare(b.version)
        })
        if (groupEntries.length === 0) continue
        rows.push({ kind: "header", label: formatGroupLabel(group) })
        for (const entry of groupEntries) {
          const index = items.length
          items.push(entry)
          rows.push({ kind: "item", entry, index })
        }
      }
      return { items, rows }
    }
    const scored = entries
      .map((entry) => {
        const candidate = [
          entry.id,
          entry.label ?? "",
          entry.group ?? "",
          entry.description ?? "",
          ...(entry.tags ?? []),
        ].join(" ")
        const score = scoreFuzzyMatch(candidate, query)
        return score == null ? null : { entry, score }
      })
      .filter((item): item is { entry: SkillEntry; score: number } => item != null)
    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score
      const al = (a.entry.label ?? a.entry.id).toLowerCase()
      const bl = (b.entry.label ?? b.entry.id).toLowerCase()
      if (al !== bl) return al.localeCompare(bl)
      return a.entry.version.localeCompare(b.entry.version)
    })
    const items = scored.map((item) => item.entry)
    const rows: SkillRow[] = items.map((entry, index) => ({ kind: "item", entry, index }))
    return { items, rows }
  }, [skillsCatalog, skillsSearch])

  const skillsSelectedEntry = useMemo(() => {
    if (skillsData.items.length === 0) return null
    const safeIndex = Math.max(0, Math.min(skillsIndex, skillsData.items.length - 1))
    return skillsData.items[safeIndex] ?? null
  }, [skillsData.items, skillsIndex])

  const skillsDisplayRows = useMemo(() => skillsData.rows, [skillsData.rows])

  const skillsDisplayIndex = useMemo(() => {
    if (!skillsSelectedEntry) return 0
    const target = skillsData.items.indexOf(skillsSelectedEntry)
    if (target < 0) return 0
    const rowIndex = skillsDisplayRows.findIndex((row) => row.kind === "item" && row.index === target)
    return rowIndex >= 0 ? rowIndex : 0
  }, [skillsData.items, skillsDisplayRows, skillsSelectedEntry])

  useEffect(() => {
    if (skillsMenu.status === "hidden") return
    if (skillsData.items.length === 0) {
      setSkillsIndex(0)
      setSkillsOffset(0)
      return
    }
    if (skillsIndex >= skillsData.items.length) {
      setSkillsIndex(0)
    }
    if (skillsDisplayIndex < skillsOffset) {
      setSkillsOffset(skillsDisplayIndex)
    } else if (skillsDisplayIndex >= skillsOffset + SKILLS_VISIBLE_ROWS) {
      setSkillsOffset(Math.max(0, skillsDisplayIndex - SKILLS_VISIBLE_ROWS + 1))
    }
  }, [skillsData.items.length, skillsDisplayIndex, skillsIndex, skillsMenu.status, skillsOffset, SKILLS_VISIBLE_ROWS])

  const resetSkillsSelection = useCallback(
    (modeOverride?: "allowlist" | "blocklist") => {
      const mode = modeOverride ?? skillsMode
      const base = skillsSelection ?? {}
      const source = mode === "allowlist" ? base.allowlist ?? [] : base.blocklist ?? []
      const normalized = source.filter((item): item is string => typeof item === "string" && item.length > 0)
      setSkillsMode(mode)
      setSkillsSelected(new Set(normalized))
      setSkillsDirty(false)
    },
    [skillsMode, skillsSelection],
  )

  const deriveSelectionFromCatalog = useCallback(
    (mode: "allowlist" | "blocklist") => {
      const entries = skillsCatalog?.skills ?? []
      const next = new Set<string>()
      for (const entry of entries) {
        const enabled = entry.enabled !== false
        if (mode === "allowlist") {
          if (enabled) next.add(buildSkillKey(entry))
        } else {
          if (!enabled) next.add(buildSkillKey(entry))
        }
      }
      return next
    },
    [skillsCatalog],
  )

  const toggleSkillsMode = useCallback(() => {
    const nextMode = skillsMode === "allowlist" ? "blocklist" : "allowlist"
    const derived = deriveSelectionFromCatalog(nextMode)
    setSkillsMode(nextMode)
    setSkillsSelected(derived)
    setSkillsDirty(true)
  }, [deriveSelectionFromCatalog, skillsMode])

  const toggleSkillSelection = useCallback(
    (entry: SkillEntry) => {
      setSkillsSelected((prev) => {
        const next = new Set(prev)
        const key = buildSkillKey(entry)
        if (next.has(entry.id) || next.has(key)) {
          next.delete(entry.id)
          next.delete(key)
        } else {
          next.add(key)
        }
        return next
      })
      setSkillsDirty(true)
    },
    [],
  )

  const applySkillsSelection = useCallback(async () => {
    if (skillsMenu.status !== "ready") return
    const list = Array.from(skillsSelected)
    const selection: SkillSelection =
      skillsMode === "allowlist" ? { mode: skillsMode, allowlist: list } : { mode: skillsMode, blocklist: list }
    await onSkillsApply(selection)
    onSkillsMenuCancel()
  }, [onSkillsApply, onSkillsMenuCancel, skillsMenu.status, skillsMode, skillsSelected])
  const filteredModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const query = modelSearch.trim().toLowerCase()
    const base = modelProviderFilter
      ? modelMenu.items.filter((item) => normalizeProviderKey(item.provider) === modelProviderFilter)
      : modelMenu.items
    const groups = new Map<string, ModelMenuItem[]>()
    const order: string[] = []
    if (query.length === 0) {
      for (const item of base) {
        const key = normalizeProviderKey(item.provider)
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
        const key = normalizeProviderKey(item.provider)
        if (!scored.has(key)) scored.set(key, [])
        scored.get(key)?.push({ item, score })
      }
      for (const item of base) {
        const key = normalizeProviderKey(item.provider)
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
  }, [modelMenu, modelSearch, modelProviderFilter])

  const modelProviderOrder = useMemo(() => {
    const items = modelMenu.status === "ready" ? modelMenu.items : []
    const present = new Set<string>()
    for (const item of items) {
      present.add(normalizeProviderKey(item.provider))
    }
    const ordered: string[] = []
    for (const provider of MODEL_PROVIDER_ORDER) {
      if (present.has(provider)) ordered.push(provider)
    }
    for (const provider of present) {
      if (!ordered.includes(provider)) ordered.push(provider)
    }
    return ordered
  }, [modelMenu])

  const modelProviderLabel = useMemo(() => {
    if (!modelProviderFilter) return "All"
    return formatProviderLabel(modelProviderFilter)
  }, [modelProviderFilter])

  const modelProviderCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of filteredModels) {
      const key = normalizeProviderKey(item.provider)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [filteredModels])

  useEffect(() => {
    if (modelMenu.status === "hidden") {
      setModelSearch("")
      setModelIndex(0)
      setModelOffset(0)
      setModelProviderFilter(null)
    }
  }, [modelMenu.status])

  useEffect(() => {
    if (skillsMenu.status === "hidden") {
      setSkillsSearch("")
      setSkillsIndex(0)
      setSkillsOffset(0)
      setSkillsDirty(false)
      return
    }
    if (skillsMenu.status !== "ready") return
    const selection = skillsSelection ?? {}
    const mode = selection.mode === "allowlist" ? "allowlist" : "blocklist"
    setSkillsMode(mode)
    const baseItems =
      mode === "allowlist"
        ? selection.allowlist ?? []
        : selection.blocklist ?? []
    const normalized = baseItems.filter((item) => typeof item === "string") as string[]
    setSkillsSelected(new Set(normalized))
    setSkillsSearch("")
    setSkillsIndex(0)
    setSkillsOffset(0)
    setSkillsDirty(false)
  }, [skillsMenu.status, skillsSelection])

  useEffect(() => {
    if (modelMenu.status !== "ready") return
    if (modelSearch.trim().length > 0) return
    const currentIndex = filteredModels.findIndex((item) => item.isCurrent)
    if (currentIndex < 0) return
    setModelIndex((prev) => (prev === currentIndex ? prev : currentIndex))
    setModelOffset((prev) => {
      if (currentIndex < prev) return currentIndex
    if (currentIndex >= prev + MODEL_VISIBLE_ROWS) {
        return Math.max(0, currentIndex - MODEL_VISIBLE_ROWS + 1)
      }
      return prev
    })
  }, [filteredModels, modelMenu.status, modelSearch, MODEL_VISIBLE_ROWS])

  useEffect(() => {
    if (!permissionRequest) return
    setPermissionScope(ALWAYS_ALLOW_SCOPE)
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
    if (!tasksOpen) return
    setTaskIndex(0)
    setTaskScroll(0)
    setTaskNotice(null)
    setTaskSearchQuery("")
    setTaskStatusFilter("all")
    setTaskTailLines([])
    setTaskTailPath(null)
  }, [tasksOpen])

  useEffect(() => {
    if (!tasksOpen) return
    setTaskIndex((prev) => Math.max(0, Math.min(prev, Math.max(0, taskRows.length - 1))))
    setTaskScroll((prev) => Math.max(0, Math.min(prev, taskMaxScroll)))
  }, [taskMaxScroll, taskRows.length, tasksOpen])

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
        skillsMenu.status !== "hidden" ||
        shortcutsOpen ||
        usageOpen ||
        permissionRequest ||
        rewindMenu.status !== "hidden" ||
        todosOpen ||
        tasksOpen ||
        transcriptViewerOpen
      )
        return "modal"
      if (paletteState.status === "open") return "palette"
      return "editor"
    },
    [
      confirmState.status,
      modelMenu.status,
      skillsMenu.status,
      shortcutsOpen,
      usageOpen,
      paletteState.status,
      permissionRequest,
      rewindMenu.status,
      todosOpen,
      tasksOpen,
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
    setTranscriptExportNotice(null)
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
    setTranscriptExportNotice(null)
    setEscPrimedAt(null)
  }, [stdout, transcriptViewerOpen])

  const saveTranscriptExport = useCallback(async () => {
    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-")
      const dirPath = path.join(process.cwd(), "artifacts", "transcripts")
      await fs.mkdir(dirPath, { recursive: true })
      const filePath = path.join(dirPath, `transcript-${timestamp}.txt`)
      await fs.writeFile(filePath, transcriptViewerLines.join("\n"), "utf8")
      setTranscriptExportNotice(`Saved to ${filePath}`)
      pushCommandResult("Transcript saved", [filePath])
    } catch (error) {
      const message = (error as Error).message || String(error)
      pushCommandResult("Transcript export failed", [message])
    }
  }, [pushCommandResult, transcriptViewerLines])

  const handleLineEdit = useCallback(
    (nextValue: string, nextCursor: number) => {
      const prevValue = inputValueRef.current
      inputValueRef.current = nextValue
      if (nextValue !== prevValue) {
        setInputTextVersion((prev) => prev + 1)
        if (suppressSuggestions) {
          setSuppressSuggestions(false)
        }
      }
      setInput(nextValue)
      setCursor(Math.max(0, Math.min(nextCursor, nextValue.length)))
      if (historyPos === historyEntries.length) {
        historyDraftRef.current = nextValue
      }
    },
    [historyEntries.length, historyPos, suppressSuggestions],
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
    if (!sessionId) return
    let cancelled = false
    const seq = ++draftLoadSeq.current
    draftAppliedRef.current = false
    void (async () => {
      try {
        const draft = await getSessionDraft(sessionId)
        if (cancelled || seq !== draftLoadSeq.current) return
        if (!draft || draftAppliedRef.current) return
        if (inputValueRef.current.trim().length > 0) return
        const nextCursor = Math.max(0, Math.min(draft.cursor, draft.text.length))
        handleLineEdit(draft.text, nextCursor)
        draftAppliedRef.current = true
      } catch {
        // ignore draft load errors
      }
    })()
    return () => {
      cancelled = true
    }
  }, [handleLineEdit, sessionId])

  useEffect(() => {
    if (!sessionId) return
    if (draftSaveTimerRef.current) {
      clearTimeout(draftSaveTimerRef.current)
      draftSaveTimerRef.current = null
    }
    const text = inputValueRef.current
    const cursorNow = Math.max(0, Math.min(cursor, text.length))
    draftSaveTimerRef.current = setTimeout(() => {
      if (!sessionId) return
      const trimmed = text.trim()
      if (!trimmed) {
        if (lastSavedDraftRef.current) {
          lastSavedDraftRef.current = null
          void updateSessionDraft(sessionId, null)
        }
        return
      }
      const prev = lastSavedDraftRef.current
      if (prev && prev.text === text && prev.cursor === cursorNow) return
      lastSavedDraftRef.current = { text, cursor: cursorNow }
      void updateSessionDraft(sessionId, {
        text,
        cursor: cursorNow,
        updatedAt: new Date().toISOString(),
      })
    }, 450)
    return () => {
      if (draftSaveTimerRef.current) {
        clearTimeout(draftSaveTimerRef.current)
        draftSaveTimerRef.current = null
      }
    }
  }, [cursor, sessionId, input])

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
    if (rawFilePickerNeedle.length === 0) return "tree"
    return "fuzzy"
  }, [filePickerActive, filePickerConfig.mode, rawFilePickerNeedle])

  const fileMenuNeedle = fileMenuMode === "fuzzy" ? filePickerNeedle : filePickerQueryParts.needle
  const fileMenuNeedlePending =
    fileMenuMode === "fuzzy" && rawFilePickerNeedle.length > 0 && rawFilePickerNeedle !== filePickerNeedle

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
    if (fileMenuNeedlePending) {
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
    fileMenuNeedlePending,
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

  useEffect(() => {
    fileMenuRowsRef.current = fileMenuRows
  }, [fileMenuRows])

  const fileMenuHasLarge = useMemo(
    () =>
      fileMenuRows.some(
        (row) =>
          row.kind === "file" &&
          row.item.size != null &&
          row.item.size > fileMentionConfig.maxInlineBytesPerFile,
      ),
    [fileMenuRows, fileMentionConfig.maxInlineBytesPerFile],
  )

  const fileMenuIndex = useMemo(() => {
    if (!filePickerActive) return 0
    if (fileMenuRows.length === 0) return 0
    return Math.max(0, Math.min(filePicker.index, fileMenuRows.length - 1))
  }, [fileMenuRows.length, filePicker.index, filePickerActive])

  const selectedFileRow = useMemo(() => {
    if (!filePickerActive) return null
    if (fileMenuRows.length === 0) return null
    return fileMenuRows[fileMenuIndex] ?? null
  }, [fileMenuIndex, fileMenuRows, filePickerActive])

  const selectedFileIsLarge = useMemo(() => {
    if (!selectedFileRow || selectedFileRow.kind !== "file") return false
    const size = selectedFileRow.item.size
    return size != null && size > fileMentionConfig.maxInlineBytesPerFile
  }, [fileMentionConfig.maxInlineBytesPerFile, selectedFileRow])

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

  const clearScreen = useCallback(() => {
    if (!stdout?.isTTY) return
    try {
      stdout.write("\u001b[2J")
      stdout.write("\u001b[H")
    } catch {
      // ignore
    }
    forceRedraw((value) => value + 1)
  }, [forceRedraw, stdout])

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
      const isCtrlB = key.ctrl && lowerChar === "b"
      const isCtrlG = (key.ctrl && lowerChar === "g") || char === "\u0007"
      if (key.ctrl && lowerChar === "l") {
        clearScreen()
        return true
      }
      if (shortcutsOpen && (char === "?" || key.escape)) {
        if (char === "?" && process.env.BREADBOARD_SHORTCUTS_STICKY === "1") {
          return true
        }
        const openedAt = shortcutsOpenedAtRef.current
        if (char === "?" && openedAt && Date.now() - openedAt < 200) {
          return true
        }
        shortcutsOpenedAtRef.current = null
        setShortcutsOpen(false)
        return true
      }
      if (usageOpen && (key.escape || char === "\u001b")) {
        setUsageOpen(false)
        return true
      }
      if (isCtrlShiftT && keymap !== "claude") {
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
      if (isCtrlB) {
        setTasksOpen((prev) => !prev)
        return true
      }
      if (isCtrlG) {
        if (skillsMenu.status === "hidden") {
          void onSkillsMenuOpen()
        } else {
          onSkillsMenuCancel()
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
        const cycleTranscriptMatch = (direction: -1 | 1) => {
          if (transcriptSearchMatches.length === 0) return
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
        if (!transcriptSearchOpen && lowerChar === "s" && !key.ctrl && !key.meta) {
          void saveTranscriptExport()
          return true
        }
        if (lowerChar === "n") {
          cycleTranscriptMatch(1)
          return true
        }
        if (lowerChar === "p") {
          cycleTranscriptMatch(-1)
          return true
        }
        if (lowerChar === "t" && !key.ctrl && !key.meta) {
          if (transcriptToolLines.length > 0) {
            const direction = key.shift ? -1 : 1
            setTranscriptToolIndex((prev) => {
              const count = transcriptToolLines.length
              const next = (prev + direction + count) % count
              const line = transcriptToolLines[next]
              if (typeof line === "number") {
                jumpTranscriptToLine(line)
              }
              return next
            })
          }
          return true
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
        const tabOrder: Array<"summary" | "diff" | "rules" | "note"> = ["summary", "diff", "rules", "note"]
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
            const notePayload = latestNote ? { note: latestNote } : {}
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
              scope: ALWAYS_ALLOW_SCOPE,
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
            scope: ALWAYS_ALLOW_SCOPE,
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
            scope: ALWAYS_ALLOW_SCOPE,
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
      if (tasksOpen) {
        const lastIndex = Math.max(0, taskRows.length - 1)
        if (key.escape || char === "\u001b") {
          setTasksOpen(false)
          return true
        }
        if (isCtrlB) {
          setTasksOpen(false)
          return true
        }
        const clampScroll = (value: number) => Math.max(0, Math.min(taskMaxScroll, value))
        if (key.pageUp) {
          setTaskScroll((prev) => clampScroll(prev - taskViewportRows))
          setTaskIndex((prev) => Math.max(0, prev - taskViewportRows))
          return true
        }
        if (key.pageDown) {
          setTaskScroll((prev) => clampScroll(prev + taskViewportRows))
          setTaskIndex((prev) => Math.min(lastIndex, prev + taskViewportRows))
          return true
        }
        if (key.ctrl && lowerChar === "u") {
          setTaskSearchQuery("")
          setTaskIndex(0)
          setTaskScroll(0)
          return true
        }
        if (key.backspace || key.delete) {
          setTaskSearchQuery((prev) => prev.slice(0, -1))
          setTaskIndex(0)
          setTaskScroll(0)
          return true
        }
        if (!key.ctrl && !key.meta) {
          if (lowerChar === "0") {
            setTaskStatusFilter("all")
            setTaskIndex(0)
            setTaskScroll(0)
            return true
          }
          if (lowerChar === "1") {
            setTaskStatusFilter("running")
            setTaskIndex(0)
            setTaskScroll(0)
            return true
          }
          if (lowerChar === "2") {
            setTaskStatusFilter("completed")
            setTaskIndex(0)
            setTaskScroll(0)
            return true
          }
          if (lowerChar === "3") {
            setTaskStatusFilter("failed")
            setTaskIndex(0)
            setTaskScroll(0)
            return true
          }
        }
        if (key.upArrow) {
          setTaskIndex((prev) => Math.max(0, prev - 1))
          if (selectedTaskIndex <= taskScroll) {
            setTaskScroll((prev) => clampScroll(prev - 1))
          }
          return true
        }
        if (key.downArrow) {
          setTaskIndex((prev) => Math.min(lastIndex, prev + 1))
          if (selectedTaskIndex >= taskScroll + taskViewportRows - 1) {
            setTaskScroll((prev) => clampScroll(prev + 1))
          }
          return true
        }
        if (key.return) {
          void requestTaskTail()
          return true
        }
        if (char && char.length > 0 && !key.ctrl && !key.meta && !key.return && !key.escape) {
          setTaskSearchQuery((prev) => prev + char)
          setTaskIndex(0)
          setTaskScroll(0)
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
        if (key.pageUp || key.pageDown) {
          const jump = Math.max(1, rewindVisibleLimit)
          const delta = key.pageUp ? -jump : jump
          setRewindIndex((prev) => {
            if (checkpoints.length === 0) return 0
            const next = Math.max(0, Math.min(checkpoints.length - 1, prev + delta))
            return next
          })
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
      if (skillsMenu.status !== "hidden") {
        if (key.escape || char === "\u001b") {
          onSkillsMenuCancel()
          return true
        }
        if (skillsMenu.status === "loading") {
          return true
        }
        if (skillsMenu.status === "error") {
          if (isReturnKey) onSkillsMenuCancel()
          return true
        }
        if (skillsMenu.status !== "ready") {
          return true
        }
        const count = skillsData.items.length
        if (isReturnKey) {
          void applySkillsSelection()
          return true
        }
        if (key.ctrl && lowerChar === "u") {
          setSkillsSearch("")
          setSkillsIndex(0)
          setSkillsOffset(0)
          return true
        }
        if (key.backspace || key.delete) {
          if (skillsSearch.length > 0) {
            setSkillsSearch((prev) => prev.slice(0, -1))
            setSkillsIndex(0)
            setSkillsOffset(0)
            return true
          }
        }
        if (!key.ctrl && !key.meta) {
          if (lowerChar === "m") {
            toggleSkillsMode()
            return true
          }
          if (lowerChar === "r") {
            resetSkillsSelection()
            return true
          }
          if (char === " " && skillsSelectedEntry) {
            toggleSkillSelection(skillsSelectedEntry)
            return true
          }
        }
        if (count > 0) {
          if (key.pageUp || key.pageDown) {
            const delta = key.pageUp ? -SKILLS_VISIBLE_ROWS : SKILLS_VISIBLE_ROWS
            setSkillsIndex((prev) => Math.max(0, Math.min(count - 1, prev + delta)))
            return true
          }
          if (key.downArrow || isTabKey) {
            setSkillsIndex((prev) => (prev + 1) % count)
            return true
          }
          if (key.upArrow || isShiftTab) {
            setSkillsIndex((prev) => (prev - 1 + count) % count)
            return true
          }
        }
        if (char && char.length > 0 && !key.ctrl && !key.meta && !key.return && !key.escape) {
          setSkillsSearch((prev) => prev + char)
          setSkillsIndex(0)
          setSkillsOffset(0)
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
      if ((key.backspace || key.delete) && modelSearch.length === 0 && modelProviderFilter) {
        setModelProviderFilter(null)
        setModelIndex(0)
        setModelOffset(0)
        return true
      }
      const count = filteredModels.length
      if (count > 0) {
        if (key.leftArrow || key.rightArrow) {
          const providers = modelProviderOrder
          if (providers.length > 0) {
            if (!modelProviderFilter) {
              const currentKey =
                normalizeProviderKey(filteredModels[modelIndex]?.provider) ?? providers[0]
              setModelProviderFilter(currentKey)
            } else {
              const index = Math.max(0, providers.indexOf(modelProviderFilter))
              const nextIndex =
                (index + (key.rightArrow ? 1 : -1) + providers.length) % providers.length
              setModelProviderFilter(providers[nextIndex])
            }
            setModelIndex(0)
            setModelOffset(0)
          }
          return true
        }
        if (key.downArrow || isTabKey) {
          setModelIndex((index) => {
            const next = (index + 1) % count
            setModelOffset((offset) => {
              if (next < offset) return next
              if (next >= offset + MODEL_VISIBLE_ROWS) return Math.max(0, next - MODEL_VISIBLE_ROWS + 1)
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
              if (next >= offset + MODEL_VISIBLE_ROWS) return Math.max(0, next - MODEL_VISIBLE_ROWS + 1)
              return offset
            })
            return next
          })
          return true
        }
        if (key.pageUp || key.pageDown) {
          const delta = key.pageUp ? -MODEL_VISIBLE_ROWS : MODEL_VISIBLE_ROWS
          setModelIndex((index) => {
            const next = Math.max(0, Math.min(count - 1, index + delta))
            setModelOffset((offset) => {
              if (next < offset) return next
              if (next >= offset + MODEL_VISIBLE_ROWS) return Math.max(0, next - MODEL_VISIBLE_ROWS + 1)
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
      modelProviderFilter,
      modelProviderOrder,
      modelSearch.length,
      MODEL_VISIBLE_ROWS,
      applySkillsSelection,
      onSkillsMenuCancel,
      resetSkillsSelection,
      toggleSkillsMode,
      toggleSkillSelection,
      skillsMenu.status,
      skillsData.items,
      skillsSearch.length,
      skillsSelectedEntry,
      SKILLS_VISIBLE_ROWS,
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
      taskMaxScroll,
      taskRows.length,
      taskScroll,
      taskSearchQuery,
      taskStatusFilter,
      taskViewportRows,
      tasksOpen,
      requestTaskTail,
      todoMaxScroll,
      todoViewportRows,
      todosOpen,
      rewindIndex,
      rewindMenu,
      selectedTask,
      selectedTaskIndex,
      runConfirmAction,
      shortcutsOpen,
      onSubmit,
      saveTranscriptExport,
      transcriptToolLines,
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

  const moveCursorVertical = useCallback(
    (direction: -1 | 1) => {
      const text = inputValueRef.current
      if (!text.includes("\n")) return false
      const currentCursor = cursor
      const prevNewline = text.lastIndexOf("\n", Math.max(0, currentCursor - 1))
      const lineStart = prevNewline === -1 ? 0 : prevNewline + 1
      const column = currentCursor - lineStart
      if (direction === -1) {
        if (lineStart === 0) return false
        const prevLineEnd = lineStart - 1
        const prevLineStart = text.lastIndexOf("\n", Math.max(0, prevLineEnd - 1)) + 1
        const prevLineLength = prevLineEnd - prevLineStart
        const target = prevLineStart + Math.min(column, prevLineLength)
        handleLineEdit(text, target)
        return true
      }
      const nextNewline = text.indexOf("\n", currentCursor)
      if (nextNewline === -1) return false
      const nextLineStart = nextNewline + 1
      const nextLineEnd = text.indexOf("\n", nextLineStart)
      const nextLineLength = (nextLineEnd === -1 ? text.length : nextLineEnd) - nextLineStart
      const target = nextLineStart + Math.min(column, nextLineLength)
      handleLineEdit(text, target)
      return true
    },
    [cursor, handleLineEdit],
  )

  const handleEditorKeys = useCallback<KeyHandler>(
    (char, key) => {
      const isTabKey = key.tab || (typeof char === "string" && (char.includes("\t") || char.includes("\u001b[Z")))
      const isReturnKey = key.return || char === "\r" || char === "\n"
      const lowerChar = char?.toLowerCase()
      const isQuestionMark = lowerChar === "?" || (char === "/" && key.shift)
      if (!key.ctrl && !key.meta && isQuestionMark && inputValueRef.current.trim() === "") {
        shortcutsOpenedAtRef.current = Date.now()
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
      if (
        attachments.length > 0 &&
        key.backspace &&
        inputValueRef.current.length === 0 &&
        cursor === 0 &&
        !overlayActive
      ) {
        removeLastAttachment()
        return true
      }
      if (modelMenu.status !== "hidden") {
        return false
      }
      if (filePickerActive) {
        const menuRows = fileMenuRowsRef.current.length > 0 ? fileMenuRowsRef.current : fileMenuRows
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
        const allowSelectionFallback =
          (isTabKey || (isReturnKey && !key.shift)) && menuRows.length === 0 && filePickerFilteredItems.length > 0
        if (menuStatus === "loading" || menuStatus === "scanning") {
          if (menuRows.length === 0 && !allowSelectionFallback) return true
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
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex - 1 + count) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (key.downArrow) {
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex + 1) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (key.pageUp || key.pageDown) {
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const jump = Math.max(1, Math.min(fileMenuMaxRows, count - 1))
            const delta = key.pageUp ? -jump : jump
            const nextIndex = Math.max(0, Math.min(count - 1, baseIndex + delta))
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (isTabKey || (isReturnKey && !key.shift)) {
          if (!activeAtMention) return true
          let rows = menuRows
          if (rows.length === 0) {
            const treeRows: FileMenuRow[] = filePickerFilteredItems.map((item) => ({ kind: "file" as const, item }))
            if (treeRows.length > 0) {
              rows = treeRows
            } else if (fileMenuMode !== "tree") {
              const cwd = filePickerQueryParts.cwd
              const prefix = cwd === "." ? "" : `${cwd}/`
              const candidates = prefix
                ? fileIndexItems.filter((item) => item.path === cwd || item.path.startsWith(prefix))
                : fileIndexItems
              const ranked = rankFuzzyFileItems(
                candidates,
                rawFilePickerNeedle,
                filePickerConfig.maxResults,
                (item) => displayPathForCwd(item.path, cwd),
              )
              rows = ranked.map((item) => ({ kind: "file" as const, item }))
            }
          }
          const count = rows.length
          if (count === 0) return true

          const resolvedIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
          const current = rows[resolvedIndex]
          if (!current) return true
          if (current.kind === "resource") {
            insertResourceMention(current.resource, activeAtMention)
            closeFilePicker()
            return true
          }

          const completionCandidates = rows
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
        if (key.escape) {
          setSuggestIndex(0)
          setSuppressSuggestions(true)
          return true
        }
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
      if (key.upArrow) {
        if (moveCursorVertical(-1)) return true
        recallHistory(-1)
        return true
      }
      if (key.downArrow) {
        if (moveCursorVertical(1)) return true
        recallHistory(1)
        return true
      }
      if (key.ctrl && lowerChar === "p" && keymap !== "claude") {
        recallHistory(-1)
        return true
      }
      if (key.ctrl && lowerChar === "n") {
        recallHistory(1)
        return true
      }
      return false
    },
    [
      activeAtMention,
      attachments.length,
      applySuggestion,
      closeFilePicker,
      cursor,
      ensureFileIndexScan,
      fileIndexMeta.status,
      fileMenuIndex,
      fileMenuMaxRows,
      fileMenuRows,
      fileMenuMode,
      filePicker.status,
      filePickerActive,
      filePickerConfig.maxResults,
      filePickerFilteredItems,
      filePickerQueryParts.cwd,
      fileIndexItems,
      insertDirectoryMention,
      insertFileMention,
      insertResourceMention,
      handleLineEdit,
      inputTextVersion,
      keymap,
      loadFilePickerDirectory,
      modelMenu.status,
      moveCursorVertical,
      overlayActive,
      pendingResponse,
      pushHistoryEntry,
      queueFileMention,
      rawFilePickerNeedle,
      recallHistory,
      removeLastAttachment,
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
      const isCtrlB = key.ctrl && lowerChar === "b"
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
      if (isCtrlB) {
        setTasksOpen((prev) => !prev)
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
        if (command === "usage") {
          setUsageOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "tasks") {
          setTasksOpen(true)
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
      try {
        await onSubmit(segments.join("\n\n"), attachments)
      } catch (error) {
        const message = (error as Error).message || String(error)
        pushCommandResult("Submit failed", [message])
      }
      if (attachments.length > 0) {
        setAttachments([])
      }
      if (fileMentions.length > 0) {
        setFileMentions([])
      }
      pushHistoryEntry(normalized)
      handleLineEdit("", 0)
      void updateSessionDraft(sessionId, null)
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
      pushCommandResult,
      pushHistoryEntry,
      sessionId,
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
    } else if (modelIndex >= modelOffset + MODEL_VISIBLE_ROWS) {
      setModelOffset(modelIndex - MODEL_VISIBLE_ROWS + 1)
    }
  }, [filteredModels.length, modelIndex, modelOffset, MODEL_VISIBLE_ROWS])

  const visibleModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    return filteredModels.slice(modelOffset, modelOffset + MODEL_VISIBLE_ROWS)
  }, [filteredModels, modelMenu.status, modelOffset, MODEL_VISIBLE_ROWS])
  const visibleModelRows = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const rows: Array<
      { kind: "header"; label: string; count: number | null } | { kind: "item"; item: ModelMenuItem; index: number }
    > = []
    for (let idx = 0; idx < visibleModels.length; idx += 1) {
      const item = visibleModels[idx]
      const globalIndex = modelOffset + idx
      const prev = filteredModels[globalIndex - 1]
      const providerKey = normalizeProviderKey(item.provider)
      const prevKey = normalizeProviderKey(prev?.provider)
      if (idx === 0 || !prev || prevKey !== providerKey) {
        const label = formatProviderLabel(item.provider)
        const count = modelProviderCounts.get(providerKey) ?? null
        rows.push({ kind: "header", label, count })
      }
      rows.push({ kind: "item", item, index: globalIndex })
    }
    return rows
  }, [filteredModels, modelMenu.status, modelOffset, modelProviderCounts, visibleModels])

  const headerLines = useMemo(
    () => ["", ...ASCII_HEADER.map((line) => applyForegroundGradient(line, Gradients.crush, true))],
    [],
  )
  const usageSummary = useMemo(() => {
    const usage = stats.usage
    if (!usage) return null
    const parts: string[] = []
    const hasTokenInputs =
      usage.totalTokens != null || usage.promptTokens != null || usage.completionTokens != null
    if (hasTokenInputs) {
      const total =
        usage.totalTokens ??
        (usage.promptTokens != null || usage.completionTokens != null
          ? (usage.promptTokens ?? 0) + (usage.completionTokens ?? 0)
          : undefined)
      if (total != null && Number.isFinite(total)) {
        parts.push(`tok ${Math.round(total)}`)
      }
    }
    if (usage.costUsd != null && Number.isFinite(usage.costUsd)) {
      parts.push(`cost ${formatCostUsd(usage.costUsd)}`)
    }
    if (usage.latencyMs != null && Number.isFinite(usage.latencyMs)) {
      parts.push(`lat ${formatLatency(usage.latencyMs)}`)
    }
    return parts.length > 0 ? parts.join(" · ") : null
  }, [stats.usage])
  const modeBadge = useMemo(() => {
    const { label, color } = normalizeModeLabel(mode)
    return chalk.hex(color)(`[${label}]`)
  }, [mode])
  const permissionBadge = useMemo(() => {
    const { label, color } = normalizePermissionLabel(permissionMode)
    return chalk.hex(color)(`[${label}]`)
  }, [permissionMode])
  const headerSubtitleLines = useMemo(() => {
    if (!claudeChrome) {
      return [chalk.cyan("breadboard — interactive session")]
    }
    const modelLine = stats.model ? `${stats.model} · API Usage Billing` : "Model unknown · API Usage Billing"
    const cwdLine = process.cwd()
    return [
      chalk.cyan(`breadboard v${CLI_VERSION}`),
      chalk.dim(modelLine),
      chalk.dim(`mode ${modeBadge}  perms ${permissionBadge}`),
      chalk.dim(cwdLine),
    ]
  }, [claudeChrome, modeBadge, permissionBadge, stats.model])
  const promptRule = useMemo(() => "─".repeat(contentWidth), [contentWidth])
  const pendingClaudeStatus = useMemo(
    () => (claudeChrome && pendingResponse ? "· Deciphering… (esc to interrupt · thinking)" : null),
    [claudeChrome, pendingResponse],
  )
  const networkBanner = useMemo(() => {
    if (disconnected) {
      return {
        tone: "error" as const,
        label: "Disconnected",
        message: "Lost connection to the engine. Check network or restart the session.",
      }
    }
    if (status.toLowerCase().startsWith("reconnecting")) {
      return {
        tone: "warning" as const,
        label: "Reconnecting",
        message: status,
      }
    }
    return null
  }, [disconnected, status])
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
    const pendingStatusRows = claudeChrome && pendingClaudeStatus ? 1 : 0
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
              (fileIndexMeta.truncated ? 1 : 0) +
              (fileMenuNeedlePending ? 1 : 0)
            : 0
        const largeHintRows = selectedFileIsLarge ? 1 : 0
        return base + fileMenuWindow.lineCount + hiddenRows + fuzzyStatusRows + largeHintRows
      }
      if (suggestions.length === 0) return 1
      const hiddenRows =
        (suggestionWindow.hiddenAbove > 0 ? 1 : 0) + (suggestionWindow.hiddenBelow > 0 ? 1 : 0)
      return 1 + suggestionWindow.lineCount + hiddenRows
    })()
    const hintCount = overlayActive ? 0 : Math.min(4, hints.length)
    const hintRows = overlayActive ? 0 : claudeChrome ? 1 : hintCount > 0 ? 1 + hintCount : 0
    const attachmentRows = overlayActive ? 0 : attachments.length > 0 ? attachments.length + 3 : 0
    const fileMentionRows = overlayActive ? 0 : fileMentions.length > 0 ? fileMentions.length + 3 : 0
    return (
      outerMargin +
      pendingStatusRows +
      promptRuleRows +
      promptLine +
      suggestionRows +
      hintRows +
      attachmentRows +
      fileMentionRows
    )
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
    fileMenuNeedlePending,
    filePicker.status,
    filePickerActive,
    hints.length,
    overlayActive,
    pendingClaudeStatus,
    pendingResponse,
    suggestions.length,
    suggestionWindow.hiddenAbove,
    suggestionWindow.hiddenBelow,
    suggestionWindow.lineCount,
    selectedFileIsLarge,
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

  const toolEventsForWindow = useMemo(() => {
    if (!SCROLLBACK_MODE) {
      if (transcriptNudge > 0) {
        return trimTailByLineCount(toolEvents, transcriptNudge, measureToolEntryLines)
      }
      return toolEvents
    }
    const printed = printedToolIdsRef.current
    return toolEvents.filter((entry) => !printed.has(entry.id))
  }, [measureToolEntryLines, toolEvents, transcriptNudge])

  const toolWindow = useMemo(() => {
    if (toolLineBudget === 0) return { items: [], hiddenCount: toolEventsForWindow.length, usedLines: 0, truncated: false }
    return sliceTailByLineBudget(toolEventsForWindow, toolLineBudget, measureToolEntryLines)
  }, [measureToolEntryLines, toolEventsForWindow, toolLineBudget])

  const toolSectionMargin = !overlayActive && toolWindow.items.length > 0 ? 1 : 0
  const remainingBodyBudgetForTranscript = Math.max(0, bodyBudgetRows - toolWindow.usedLines - toolSectionMargin)

  const unprintedFinalConversationEntries = useMemo(() => {
    if (!SCROLLBACK_MODE) return finalConversationEntries
    const printed = printedConversationIdsRef.current
    return finalConversationEntries.filter((entry) => !printed.has(entry.id))
  }, [finalConversationEntries])

  const conversationEntriesForWindow = useMemo(() => {
    if (!SCROLLBACK_MODE) {
      const base = streamingConversationEntry
        ? [...finalConversationEntries, streamingConversationEntry]
        : finalConversationEntries
      if (transcriptNudge > 0) {
        return trimTailByLineCount(base, transcriptNudge, measureConversationEntryLines)
      }
      return base
    }
    if (!streamingConversationEntry) return unprintedFinalConversationEntries
    return [...unprintedFinalConversationEntries, streamingConversationEntry]
  }, [
    finalConversationEntries,
    measureConversationEntryLines,
    streamingConversationEntry,
    transcriptNudge,
    unprintedFinalConversationEntries,
  ])

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
    const isError = entry.status === "error" || entry.kind === "error"
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
      if (isError) return chalk.hex("#F87171")(line)
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
    if (claudeChrome && (entry.kind === "call" || entry.kind === "result")) {
      const parsed = extractToolPayload(entry)
      if (parsed) {
        const toolName = normalizeToolName(parsed.name)
        const argsSummary = formatToolArgsSummary(parsed.args)
        const header = argsSummary ? `${toolName}(${argsSummary})` : toolName
        if (entry.kind === "call") {
          return (
            <Text key={key ?? entry.id} wrap="truncate-end">
              {glyph} {header}
            </Text>
          )
        }
        const outputText = parsed.output ?? parsed.status ?? "Done"
        const outputLines = outputText.split(/\r?\n/)
        const colorizeClaudeLine = (line: string) => {
          if (!line) return line
          if (line.includes("\u001b")) return line
          if (line.startsWith("diff --git") || line.startsWith("index ")) return chalk.hex("#7CF2FF")(line)
          if (line.startsWith("@@")) return chalk.hex("#FBBF24")(line)
          if (line.startsWith("---") || line.startsWith("+++")) return chalk.hex("#C4B5FD")(line)
          if (line.startsWith("+") && !line.startsWith("+++")) return chalk.hex("#34D399")(line)
          if (line.startsWith("-") && !line.startsWith("---")) return chalk.hex("#FB7185")(line)
          return line
        }
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {outputLines.map((line, index) => (
              <Text key={`${entry.id}-out-${index}`} wrap="truncate-end">
                {index === 0 ? `  ⎿ ${colorizeClaudeLine(line)}` : `    ${colorizeClaudeLine(line)}`}
              </Text>
            ))}
          </Box>
        )
      }
    }
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
  }, [claudeChrome, verboseOutput])

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
      "Ctrl+L clear screen",
      "Ctrl+K model",
      `Ctrl+O ${keymap === "claude" ? "transcript" : "detailed"}`,
      "Ctrl+B tasks",
      "/usage",
    ]
    if (keymap === "claude") {
      hintParts.push("Ctrl+T todos")
    } else {
      hintParts.push("Ctrl+T transcript")
    }
    hintParts.push("Ctrl+G skills")
    return [
      <Text key="meta-slash" color="dim">
        Slash commands: {SLASH_COMMAND_HINT}
      </Text>,
      <Text key="meta-hints" color="dim">
        {hintParts.join(" • ")}
      </Text>,
    ]
  }, [claudeChrome, keymap])

  const shortcutLines = useMemo(() => {
    if (claudeChrome) {
      const rows: Array<[string, string, string?]> = [
        ["! for bash mode", "double tap esc to clear input", "ctrl + _ to undo"],
        ["/ for commands", "shift + tab to auto-accept edits", "ctrl + z to suspend"],
        ["@ for file paths", "ctrl + o for transcript", "ctrl + v to paste images"],
        ["& for background", "ctrl + t to show todos", "alt + p to switch model"],
        ["", "shift + ⏎ for newline", "ctrl + s to stash prompt"],
        ["", "ctrl + g for skills", ""],
      ]
      const colWidth = Math.max(22, Math.floor((contentWidth - 4) / 3))
      return rows.map(([a, b, c]) => {
        const left = formatCell(a, colWidth, "left")
        const mid = formatCell(b, colWidth, "left")
        const right = formatCell(c ?? "", colWidth, "left")
        return `${left}  ${mid}  ${right}`.trimEnd()
      })
    }
    const rows: Array<[string, string]> = [
      ["Ctrl+C ×2", "Exit the REPL"],
      ["Ctrl+D", "Exit immediately"],
      ["Ctrl+Z", "Suspend (empty input)"],
      ["Ctrl+L", "Clear screen (keep transcript)"],
      ["Ctrl+A", "Start of line"],
      ["Ctrl+E", "End of line"],
      ["Home/End", "Start/end of line"],
      ["Ctrl+U", "Delete to start"],
      ["Ctrl+S", "Stash input to history"],
      ["Esc", "Stop streaming"],
      ["Esc Esc", "Clear input"],
      ["Shift+Enter", "Insert newline"],
      ["Alt+Enter", "Insert newline"],
      ["Ctrl+O", keymap === "claude" ? "Transcript viewer" : "Toggle detailed transcript"],
      ["Ctrl+P", "Command palette"],
      ["Ctrl+K", "Model picker"],
      ["Alt+P", "Model picker"],
      ["Ctrl+G", "Skills picker"],
      ["Ctrl+B", "Background tasks"],
      ["/usage", "Usage summary"],
      ["Tab", "Complete @ or / list"],
      ["/", "Slash commands"],
      ["@", "File picker"],
      ["n/p", "Transcript match nav"],
      ["s", "Save transcript (viewer)"],
      ["?", "Toggle shortcuts"],
    ]
    if (keymap === "claude") {
      rows.push(["Ctrl+T", "Todos panel"])
    } else {
      rows.push(["Ctrl+T", "Transcript viewer"])
    }
    const pad = 14
    return rows.map(([key, desc]) => `${chalk.cyan(key.padEnd(pad))} ${desc}`)
  }, [claudeChrome, contentWidth, keymap])

  const hintNodes = useMemo(() => {
    const filtered = claudeChrome ? hints.filter((hint) => !hint.startsWith("Session ")) : hints
    if (claudeChrome) {
      if (shortcutsOpen) {
        return shortcutLines.map((line, index) => (
          <Text key={`hint-shortcut-${index}`} wrap="truncate-end">
            {line}
          </Text>
        ))
      }
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
  }, [claudeChrome, contentWidth, ctrlCPrimedAt, escPrimedAt, hints, pendingResponse, shortcutLines, shortcutsOpen])

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
      const isCtrlB = key.ctrl && lowerChar === "b"
      const isCtrlG = (key.ctrl && lowerChar === "g") || char === "\u0007"
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
      if (isCtrlB) {
        setTasksOpen((prev) => !prev)
        return true
      }
      if (key.ctrl && lowerChar === "l") {
        clearScreen()
        return true
      }
      if (key.ctrl && (lowerChar === "o" || char === "\u000f")) {
        if (keymap === "claude") {
          if (transcriptViewerOpen) {
            exitTranscriptViewer()
          } else {
            enterTranscriptViewer()
          }
        } else {
          setVerboseOutput((prev) => {
            const next = !prev
            pushCommandResult("Detailed transcript", [next ? "ON" : "OFF"])
            return next
          })
        }
        return true
      }
      if (!SCROLLBACK_MODE && !overlayActive && !transcriptViewerOpen) {
        if (key.pageUp) {
          setTranscriptNudge((prev) => prev + transcriptViewerBodyRows)
          return true
        }
        if (key.pageDown) {
          setTranscriptNudge((prev) => Math.max(0, prev - transcriptViewerBodyRows))
          return true
        }
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
          handleLineEdit("", 0)
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
      if (isCtrlG) {
        if (skillsMenu.status === "hidden") {
          void onSkillsMenuOpen()
        } else {
          onSkillsMenuCancel()
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
      skillsMenu.status,
      onModelMenuCancel,
      onModelMenuOpen,
      onSkillsMenuCancel,
      onSkillsMenuOpen,
      openConfirm,
      openPalette,
      onSubmit,
      onGuardrailDismiss,
      onGuardrailToggle,
      overlayActive,
      paletteState.status,
      pendingResponse,
      permissionRequest,
      pushCommandResult,
      rewindMenu.status,
      transcriptViewerBodyRows,
      transcriptViewerOpen,
      usageOpen,
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

      const pending: Array<{ id: string; node: React.ReactNode; order: number; seq: number }> = []
      let seq = 0

      for (const entry of finalConversationEntries) {
        if (printedConversationIdsRef.current.has(entry.id)) continue
        printedConversationIdsRef.current.add(entry.id)
        pending.push({
          id: `conv-${entry.id}`,
          node: renderConversationText(entry, `static-${entry.id}`),
          order: Number.isFinite(entry.createdAt) ? entry.createdAt : Date.now(),
          seq: seq++,
        })
      }

      for (const entry of toolEvents) {
        if (printedToolIdsRef.current.has(entry.id)) continue
        printedToolIdsRef.current.add(entry.id)
        pending.push({
          id: `tool-${entry.id}`,
          node: renderToolEntry(entry, `static-${entry.id}`),
          order: Number.isFinite(entry.createdAt) ? entry.createdAt : Date.now(),
          seq: seq++,
        })
      }

      if (pending.length > 0) {
        pending.sort((a, b) => (a.order - b.order) || (a.seq - b.seq))
        next = [...next, ...pending.map((item) => ({ id: item.id, node: item.node }))]
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
            {stats.toolCount} {modeBadge} {permissionBadge}
            {stats.lastTurn != null ? ` ${turnGlyph} turn ${stats.lastTurn}` : ""}
            {usageSummary ? ` ${chalk.dim(usageSummary)}` : ""}
          </Text>
        )}
        {metaNodes.length > 0 && (
          <Box flexDirection="column" marginTop={1}>
            {metaNodes}
          </Box>
        )}
      {guardrailNotice && <GuardrailBanner notice={guardrailNotice} />}
      {networkBanner && (
        <Box
          flexDirection="column"
          borderStyle="round"
          borderColor={networkBanner.tone === "error" ? "#fb7185" : "#facc15"}
          paddingX={2}
          paddingY={1}
          marginTop={1}
        >
          <Text color={networkBanner.tone === "error" ? "#fb7185" : "#facc15"}>
            {chalk.bold(`${networkBanner.label}:`)} {networkBanner.message}
          </Text>
          <Text color="gray">If this persists, retry the command or restart the session.</Text>
        </Box>
      )}

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

      {!(claudeChrome && modelMenu.status !== "hidden") && (
        <Box marginTop={1} flexDirection="column">
          {claudeChrome && pendingClaudeStatus && <Text color="dim">{pendingClaudeStatus}</Text>}
          {claudeChrome && <Text color="dim">{promptRule}</Text>}
          <Box>
            <Text color={claudeChrome ? undefined : "cyan"}>{claudeChrome ? "> " : "› "}</Text>
            <LineEditor
              value={input}
              cursor={cursor}
              focus={!inputLocked}
              placeholder={claudeChrome ? "Try edit <file> to..." : "Type your request…"}
              placeholderPad={!claudeChrome}
              hideCaretWhenPlaceholder={claudeChrome}
              maxVisibleLines={inputMaxVisibleLines}
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
          (() => {
            const titleLines: SelectPanelLine[] = []
            const hintLines: SelectPanelLine[] = []
            const rows: SelectPanelRow[] = []
            const footerLines: SelectPanelLine[] = []

            if (!claudeChrome) {
              titleLines.push({
                text: `Attach file ${chalk.dim(filePickerQueryParts.cwd === "." ? "(root)" : filePickerQueryParts.cwd)}`,
                color: "#7CF2FF",
              })
              hintLines.push({
                text: `${fileMenuMode === "fuzzy" ? "Fuzzy search • " : ""}Type to filter • ↑/↓ navigate • PgUp/PgDn page • Tab/Enter complete • Esc clear${fileMenuHasLarge ? " • * large file" : ""}`,
                color: "gray",
              })
            }

            if (fileMenuMode === "tree" && (filePicker.status === "loading" || filePicker.status === "hidden")) {
              rows.push({ kind: "empty", text: "Loading…", color: "gray" })
            } else if (fileMenuMode === "tree" && filePicker.status === "error") {
              rows.push({
                kind: "empty",
                text: filePicker.message ? `Error: ${filePicker.message}` : "Error loading files.",
                color: "#fb7185",
              })
              rows.push({ kind: "empty", text: "Tab to retry • Esc to clear", color: "gray" })
            } else if (fileMenuMode === "fuzzy" && fileIndexMeta.status === "error" && fileMenuRows.length === 0) {
              rows.push({
                kind: "empty",
                text: fileIndexMeta.message ? `Error: ${fileIndexMeta.message}` : "Error indexing files.",
                color: "#fb7185",
              })
              rows.push({ kind: "empty", text: "Tab to retry • Esc to clear", color: "gray" })
            } else if (fileMenuRows.length === 0) {
              if (fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning")) {
                rows.push({ kind: "empty", text: `Indexing… (${fileIndexMeta.fileCount} files)`, color: "dim" })
              } else {
                rows.push({ kind: "empty", text: "(no matches)", color: "dim" })
              }
            } else {
              const contentWidth = Math.max(10, columnWidth - 4)
              const windowItems = fileMenuWindow.items
              const hiddenAbove = fileMenuWindow.hiddenAbove
              const hiddenBelow = fileMenuWindow.hiddenBelow
              if (fileMenuMode === "fuzzy" && (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning")) {
                rows.push({ kind: "header", text: `Indexing… (${fileIndexMeta.fileCount} files)`, color: "dim" })
              }
              if (fileMenuMode === "fuzzy" && fileMenuNeedlePending) {
                rows.push({ kind: "header", text: "Searching…", color: "dim" })
              }
              if (fileMenuMode === "fuzzy" && fileIndexMeta.truncated) {
                rows.push({ kind: "header", text: `Index truncated at ${fileIndexMeta.fileCount} files.`, color: "dim" })
              }
              if (hiddenAbove > 0) {
                rows.push({ kind: "header", text: `↑ ${hiddenAbove} more…`, color: "dim" })
              }
              windowItems.forEach((row, index) => {
                const absoluteIndex = fileMenuWindow.start + index
                const selected = absoluteIndex === fileMenuIndex
                if (row.kind === "resource") {
                  rows.push({
                    kind: "item",
                    text: formatCell(row.resource.label, contentWidth, "left"),
                    secondaryText: row.resource.detail ? `  ${row.resource.detail}` : undefined,
                    isActive: selected,
                    color: "white",
                    secondaryColor: "dim",
                    activeColor: "#0F172A",
                    secondaryActiveColor: "#0F172A",
                    activeBackground: "#7CF2FF",
                  })
                  return
                }
                const display = row.item.path
                const suffix = row.item.type === "directory" ? "/" : ""
                const sizeBytes = row.item.size
                const isLarge = sizeBytes != null && sizeBytes > fileMentionConfig.maxInlineBytesPerFile
                const sizeLabel = sizeBytes != null ? formatBytes(sizeBytes) : ""
                const sizeToken = sizeLabel ? `${sizeLabel}${isLarge ? "*" : ""}` : ""
                const leftWidth = sizeToken ? Math.max(0, contentWidth - sizeToken.length - 1) : contentWidth
                const rightWidth = Math.max(0, contentWidth - leftWidth)
                const leftLabel = formatCell(`${display}${suffix}`, leftWidth, "left")
                const rightLabel = sizeToken ? formatCell(sizeToken, rightWidth, "right") : ""
                const label = sizeToken ? `${leftLabel}${rightLabel}` : leftLabel
                rows.push({
                  kind: "item",
                  text: label,
                  isActive: selected,
                  color: row.item.type === "directory" ? "cyan" : "white",
                  activeColor: "#0F172A",
                  activeBackground: "#7CF2FF",
                })
              })
              if (hiddenBelow > 0) {
                rows.push({ kind: "header", text: `↓ ${hiddenBelow} more…`, color: "dim" })
              }
            }

            if (selectedFileIsLarge) {
              footerLines.push({
                text: "Large file selected — will attach a snippet unless explicitly forced to inline.",
                color: "dim",
              })
            }

            return (
              <SelectPanel
                width={columnWidth}
                showBorder={false}
                paddingX={0}
                paddingY={0}
                marginTop={claudeChrome ? 0 : 1}
                alignSelf="flex-start"
                titleLines={titleLines}
                hintLines={hintLines}
                rows={rows}
                footerLines={footerLines}
              />
            )
          })()
        ) : suggestions.length > 0 ? (
          <Box marginTop={claudeChrome ? 0 : 1} flexDirection="column">
            {suggestionWindow.hiddenAbove > 0 && (
              <Text color="dim">{`${suggestionPrefix}↑ ${suggestionWindow.hiddenAbove} more…`}</Text>
            )}
            {suggestionWindow.items.map((row, index) => {
              const globalIndex = suggestionWindow.start + index
              const selected = globalIndex === suggestIndex
              const lines = buildSuggestionLines(row, claudeChrome)
              return lines.map((line, lineIndex) => {
                const left = formatCell(line.label, suggestionLayout.commandWidth)
                const right = formatCell(line.summary, suggestionLayout.summaryWidth)
                if (claudeChrome) {
                  const rendered = `${suggestionPrefix}${left}  ${right}`
                  return (
                    <Text
                      key={`suggestion-${globalIndex}-${lineIndex}`}
                      color={selected ? "#B1B9F9" : "gray"}
                      wrap="wrap"
                    >
                      {rendered}
                    </Text>
                  )
                }
                const styledLeft = selected
                  ? left
                  : highlightFuzzyLabel(left, row.command, activeSlashQuery)
                const styledRight = selected ? right : chalk.dim(right)
                const rendered = `${styledLeft}  ${styledRight}`
                return (
                  <Text
                    key={`suggestion-${globalIndex}-${lineIndex}`}
                    inverse={selected}
                    wrap="truncate-end"
                  >
                    {rendered}
                  </Text>
                )
              })
            })}
            {suggestionWindow.hiddenBelow > 0 && (
              <Text color="dim">{`${suggestionPrefix}↓ ${suggestionWindow.hiddenBelow} more…`}</Text>
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
              <Text color="dim">Backspace removes the most recent attachment when the input is empty.</Text>
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
      )}
    </Box>
  )

  const modalStack: ModalDescriptor[] = []
  const transcriptDetailLabel = useMemo(() => {
    const parts: string[] = []
    if (verboseOutput) {
      parts.push("Showing detailed transcript · Ctrl+O to toggle")
    }
    if (transcriptExportNotice) {
      parts.push(transcriptExportNotice)
    }
    return parts.join(" · ")
  }, [transcriptExportNotice, verboseOutput])

  if (confirmState.status === "prompt") {
    modalStack.push({
      id: "confirm",
      render: () => {
        const titleLines: SelectPanelLine[] = [
          { text: confirmState.message ?? "Confirm action?", color: "#FACC15" },
        ]
        const hintLines: SelectPanelLine[] = [{ text: "Enter to confirm • Esc to cancel", color: "gray" }]
        return (
          <SelectPanel
            width={Math.min(80, PANEL_WIDTH)}
            borderColor="#F97316"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={[]}
          />
        )
      },
    })
  }

  if (shortcutsOpen && !claudeChrome) {
    modalStack.push({
      id: "shortcuts",
      render: () => {
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Shortcuts"), color: "#7CF2FF" }]
        const hintLines: SelectPanelLine[] = [{ text: "Press ? or Esc to close", color: "dim" }]
        const rows: SelectPanelRow[] = shortcutLines.map((line) => ({
          kind: "item",
          text: line,
        }))
        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#7CF2FF"
            paddingX={2}
            paddingY={claudeChrome ? 0 : 1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
          />
        )
      },
    })
  }

  if (paletteState.status === "open") {
    modalStack.push({
      id: "palette",
      render: () => {
        const titleLines: SelectPanelLine[] = [{ text: "Command palette", color: "#C084FC" }]
        const hintLines: SelectPanelLine[] = [
          {
            text: `Search: ${paletteState.query.length > 0 ? paletteState.query : chalk.dim("<type to filter>")}`,
            color: "dim",
          },
        ]
        const rows: SelectPanelRow[] = []
        if (paletteItems.length === 0) {
          rows.push({ kind: "empty", text: "No commands match.", color: "dim" })
        } else {
          paletteItems.slice(0, MAX_VISIBLE_MODELS).forEach((item: SlashCommandInfo, idx: number) => {
            const isActive = idx === Math.min(paletteState.index, paletteItems.length - 1)
            const label = `/${item.name}${item.usage ? ` ${item.usage}` : ""} — ${item.summary}`
            rows.push({
              kind: "item",
              text: `${isActive ? "›" : " "} ${label}`,
              isActive,
              activeColor: "#0F172A",
              activeBackground: "#C084FC",
            })
          })
        }
        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#C084FC"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
          />
        )
      },
    })
  }

  if (modelMenu.status !== "hidden") {
    modalStack.push({
      id: "model-picker",
      render: () => {
        const titleLines: SelectPanelLine[] = []
        const hintLines: SelectPanelLine[] = []
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        if (modelMenu.status === "loading") {
          rows.push({ kind: "empty", text: "Loading model catalog…", color: "cyan" })
        } else if (modelMenu.status === "error") {
          rows.push({ kind: "empty", text: modelMenu.message, color: "red" })
        } else if (modelMenu.status === "ready") {
          if (claudeChrome) {
            titleLines.push({ text: clearToEnd(" ") })
            titleLines.push({
              text: clearToEnd(formatCell("Select model — Switch between models.", modelPanelInnerWidth, "left")),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(formatCell("Use --model for other/previous names.", modelPanelInnerWidth, "left")),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(
                formatCell(
                  `Provider: ${modelProviderLabel} (←/→ filter${modelProviderFilter ? " · Backspace clear" : ""})${modelSearch.trim().length > 0 ? ` • Filter: ${modelSearch.trim()}` : ""}`,
                  modelPanelInnerWidth,
                  "left",
                ),
              ),
              color: "dim",
            })
            titleLines.push({
              text: clearToEnd(formatCell("Enter to confirm · Esc to exit", modelPanelInnerWidth, "left")),
              color: "dim",
            })
          } else {
            titleLines.push({
              text: modelMenuCompact ? "Select a model" : "Select a model (Enter to confirm, Esc to cancel)",
              color: "green",
            })
            hintLines.push({
              text: `Search: ${modelSearch.length > 0 ? modelSearch : chalk.dim("<type to filter>")}`,
              color: "dim",
            })
            hintLines.push({
              text: `Provider: ${modelProviderLabel}${modelProviderFilter ? " (←/→ change · Backspace clear)" : " (←/→ filter)"}`,
              color: "dim",
            })
            if (!modelMenuCompact) {
              hintLines.push({ text: `Current: ${chalk.cyan(stats.model)}`, color: "dim" })
            }
            hintLines.push({
              text: modelMenuCompact
                ? "Nav: ↑/↓ PgUp/PgDn • Enter confirm • Esc cancel"
                : "Navigate: ↑/↓ or Tab • Shift+Tab up • PgUp/PgDn page • Legend: ● current · ★ default",
              color: "dim",
            })
            if (modelMenuCompact) {
              hintLines.push({ text: "Legend: ● current · ★ default", color: "dim" })
            }
          }

          if (filteredModels.length === 0) {
            rows.push({ kind: "empty", text: "No models match.", color: "dim" })
          } else {
            rows.push({ kind: "header", text: modelMenuHeaderText, color: "gray" })
            visibleModelRows.forEach((row, idx) => {
              if (row.kind === "header") {
                const countSuffix = row.count != null ? ` (${row.count})` : ""
                rows.push({ kind: "header", text: `${row.label}${countSuffix}`, color: "dim" })
                return
              }
              const isActive = row.index === modelIndex
              const rowText = formatModelRowText(row.item)
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${rowText}`,
                isActive,
                activeColor: "#0F172A",
                activeBackground: "#7CF2FF",
              })
            })
          }

          if (filteredModels.length > MODEL_VISIBLE_ROWS) {
            footerLines.push({
              text: `${modelOffset + 1}-${Math.min(modelOffset + MODEL_VISIBLE_ROWS, filteredModels.length)} of ${filteredModels.length}`,
              color: "dim",
            })
          }
        }

        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#7CF2FF"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (skillsMenu.status !== "hidden") {
    modalStack.push({
      id: "skills-picker",
      render: () => {
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Skills"), color: "#C084FC" }]
        const hintLines: SelectPanelLine[] = []
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        if (skillsMenu.status === "loading") {
          rows.push({ kind: "empty", text: "Loading skills catalog…", color: "cyan" })
        } else if (skillsMenu.status === "error") {
          rows.push({ kind: "empty", text: skillsMenu.message, color: "red" })
        } else if (skillsMenu.status === "ready") {
          const selectionCount = skillsSelected.size
          const modeLabel = skillsMode === "allowlist" ? "allowlist" : "blocklist"
          hintLines.push({
            text: `Mode: ${modeLabel} (${skillsMode === "allowlist" ? "selected = enabled" : "selected = disabled"})`,
            color: "dim",
          })
          hintLines.push({
            text: `Search: ${skillsSearch.length > 0 ? skillsSearch : chalk.dim("<type to filter>")} • Selected: ${selectionCount}`,
            color: "dim",
          })
          if (skillsSources?.config_path) {
            hintLines.push({ text: `Config: ${skillsSources.config_path}`, color: "dim" })
          }
          if (skillsDisplayRows.length === 0) {
            rows.push({ kind: "empty", text: "No skills match.", color: "dim" })
          } else {
            const start = Math.max(0, Math.min(skillsOffset, Math.max(0, skillsDisplayRows.length - SKILLS_VISIBLE_ROWS)))
            const windowRows = skillsDisplayRows.slice(start, start + SKILLS_VISIBLE_ROWS)
            windowRows.forEach((row) => {
              if (row.kind === "header") {
                rows.push({ kind: "header", text: row.label, color: "dim" })
                return
              }
              const entry = row.entry
              const selected = isSkillSelected(skillsSelected, entry)
              const blocked = skillsMode === "blocklist" && selected
              const disabled = skillsMode === "allowlist" && !selected
              const marker = selected
                ? chalk.hex(blocked ? "#F87171" : "#34D399")("[x]")
                : chalk.dim("[ ]")
              const label = entry.label ?? entry.id
              const versionLabel = entry.version ? `@${entry.version}` : ""
              const line = `${marker} ${label}${versionLabel ? chalk.dim(` ${versionLabel}`) : ""}`
              const metaParts: string[] = []
              if (entry.type) metaParts.push(entry.type)
              if (entry.group) metaParts.push(entry.group)
              if (entry.slot) metaParts.push(entry.slot)
              if (entry.steps != null) metaParts.push(`${entry.steps} steps`)
              if (entry.determinism) metaParts.push(entry.determinism)
              const detail = metaParts.length > 0 ? metaParts.join(" · ") : entry.description ?? ""
              const isActive = row.index === skillsIndex
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${line}`,
                secondaryText: detail ? chalk.dim(detail) : undefined,
                isActive,
                color: disabled || blocked ? "gray" : "white",
                activeColor: "#0F172A",
                secondaryColor: "dim",
                secondaryActiveColor: "#0F172A",
                activeBackground: "#C084FC",
              })
            })
          }
          footerLines.push({
            text: "Space toggle • M mode • R reset • Enter apply • Esc cancel",
            color: "gray",
          })
          if (skillsDirty) {
            footerLines.push({ text: "Unsaved changes", color: "#F59E0B" })
          }
        }

        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#C084FC"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
        )
      },
    })
  }

  if (rewindMenu.status !== "hidden") {
    modalStack.push({
      id: "rewind",
      render: () => {
        const isLoading = rewindMenu.status === "loading"
        const isError = rewindMenu.status === "error"
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Rewind checkpoints"), color: "#93C5FD" }]
        const hintLines: SelectPanelLine[] = [
          { text: "↑/↓ select • PgUp/PgDn page • 1 convo • 2 code • 3 both • Esc close", color: "gray" },
        ]
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = [
          {
            text: "Restores conversation and tracked files; external shell changes aren't tracked. Code-only restores do not prune history.",
            color: "dim",
          },
        ]

        if (isLoading) {
          rows.push({ kind: "empty", text: "Loading checkpoints…", color: "cyan" })
        }
        if (isError) {
          rows.push({ kind: "empty", text: rewindMenu.message, color: "red" })
        }
        if (!isLoading && !isError) {
          if (rewindCheckpoints.length === 0) {
            rows.push({
              kind: "empty",
              text: "No checkpoints yet. (/rewind again after the next tool run.)",
              color: "dim",
            })
          } else {
            rewindVisible.forEach((entry, idx) => {
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
              rows.push({
                kind: "item",
                text: `${isActive ? "› " : "  "}${label}`,
                secondaryText: `${isActive ? "  " : "  "}${chalk.dim(meta || entry.checkpointId)}`,
                isActive,
                color: undefined,
                secondaryColor: "dim",
                activeColor: "#0F172A",
                secondaryActiveColor: "#0F172A",
                activeBackground: "#93C5FD",
              })
            })
          }
        }
        if (rewindCheckpoints.length > rewindVisibleLimit) {
          rows.push({
            kind: "header",
            text: `${rewindOffset + 1}-${Math.min(rewindOffset + rewindVisibleLimit, rewindCheckpoints.length)} of ${rewindCheckpoints.length}`,
            color: "dim",
          })
        }

        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#60A5FA"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
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
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Todos"), color: "#7CF2FF" }]
        const hintLines: SelectPanelLine[] = [
          {
            text:
              todos.length === 0
                ? "No todos yet."
                : `${todos.length} item${todos.length === 1 ? "" : "s"} • ↑/↓ scroll • PgUp/PgDn page • Esc close`,
            color: "gray",
          },
        ]
        const panelRows: SelectPanelRow[] = []
        if (todoRows.length === 0) {
          panelRows.push({ kind: "empty", text: "TodoWrite output will appear here once the agent updates the board.", color: "dim" })
        } else {
          visible.forEach((row, idx) => {
            if (row.kind === "header") {
              panelRows.push({ kind: "header", text: row.label, color: colorForStatus(row.status) })
            } else {
              panelRows.push({ kind: "item", text: `  • ${row.label}`, wrap: "truncate-end" })
            }
          })
          if (todoRows.length > todoViewportRows) {
            panelRows.push({
              kind: "header",
              text: `${scroll + 1}-${Math.min(scroll + todoViewportRows, todoRows.length)} of ${todoRows.length}`,
              color: "dim",
            })
          }
        }
        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#7CF2FF"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
          />
        )
      },
    })
  }

  if (usageOpen) {
    modalStack.push({
      id: "usage",
      render: () => {
        const usage = stats.usage
        const rows: Array<{ label: string; value: string }> = []
        if (usage?.promptTokens != null) rows.push({ label: "Prompt tokens", value: `${Math.round(usage.promptTokens)}` })
        if (usage?.completionTokens != null)
          rows.push({ label: "Completion tokens", value: `${Math.round(usage.completionTokens)}` })
        if (usage?.totalTokens != null) rows.push({ label: "Total tokens", value: `${Math.round(usage.totalTokens)}` })
        if (usage?.cacheReadTokens != null)
          rows.push({ label: "Cache read tokens", value: `${Math.round(usage.cacheReadTokens)}` })
        if (usage?.cacheWriteTokens != null)
          rows.push({ label: "Cache write tokens", value: `${Math.round(usage.cacheWriteTokens)}` })
        if (usage?.costUsd != null) rows.push({ label: "Cost", value: formatCostUsd(usage.costUsd) })
        if (usage?.latencyMs != null) rows.push({ label: "Latency", value: formatLatency(usage.latencyMs) })
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Usage"), color: "#22c55e" }]
        const hintLines: SelectPanelLine[] = [{ text: "Esc close", color: "dim" }]
        const panelRows: SelectPanelRow[] = []
        if (rows.length === 0) {
          panelRows.push({ kind: "empty", text: "No usage metrics reported yet.", color: "dim" })
        } else {
          rows.forEach((row) => {
            panelRows.push({
              kind: "item",
              text: `${chalk.cyan(row.label.padEnd(18, " "))} ${row.value}`,
            })
          })
        }
        return (
          <SelectPanel
            width={Math.min(PANEL_WIDTH, contentWidth + 2)}
            borderColor="#22c55e"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
          />
        )
      },
    })
  }

  if (tasksOpen) {
    modalStack.push({
      id: "tasks",
      render: () => {
        const scroll = Math.max(0, Math.min(taskScroll, taskMaxScroll))
        const visible = taskRows.slice(scroll, scroll + taskViewportRows)
        const colorForStatus = (status?: string) => {
          switch (status) {
            case "running":
              return "#38BDF8"
            case "completed":
              return "#34D399"
            case "failed":
              return "#F87171"
            default:
              return "#FACC15"
          }
        }
        const panelWidth = Math.min(PANEL_WIDTH, contentWidth + 2)
        const lineWidth = Math.max(12, panelWidth - 6)
        const titleLines: SelectPanelLine[] = [{ text: chalk.bold("Background tasks"), color: "#93C5FD" }]
        const hintLines: SelectPanelLine[] = [
          {
            text:
              tasks.length === 0
                ? "No background tasks yet."
                : `${tasks.length} task${tasks.length === 1 ? "" : "s"} • ↑/↓ select • PgUp/PgDn page • Enter tail • Esc close`,
            color: "gray",
          },
          {
            text: `Search: ${taskSearchQuery.length > 0 ? taskSearchQuery : chalk.dim("<type to filter>")} • Filter: ${taskStatusFilter} (0 all · 1 run · 2 done · 3 fail)`,
            color: "dim",
          },
        ]
        const panelRows: SelectPanelRow[] = []
        if (taskRows.length === 0) {
          panelRows.push({ kind: "empty", text: "No tasks match the current filter.", color: "dim" })
        } else {
          visible.forEach((row, idx) => {
            const globalIndex = scroll + idx
            const isActive = globalIndex === selectedTaskIndex
            const statusLabel = (row.status || "update").replace(/[_-]+/g, " ")
            const laneLabel = row.task.subagentType || "primary"
            const idLabel = row.id.slice(0, 8)
            const suffix = chalk.dim(`#${idLabel}`)
            const laneWidth = Math.min(16, Math.max(8, Math.floor(lineWidth * 0.25)))
            const laneCell = formatCell(`[${laneLabel}]`, laneWidth)
            const leftWidth = Math.max(0, lineWidth - stripAnsiCodes(suffix).length - laneWidth - 2)
            const left = formatCell(`${statusLabel} · ${row.label}`, leftWidth)
            const line = `${laneCell} ${left} ${suffix}`
            panelRows.push({
              kind: "item",
              text: `${isActive ? "› " : "  "}${line}`,
              isActive,
              color: colorForStatus(row.status),
              activeColor: "#0F172A",
              activeBackground: "#93C5FD",
            })
          })
          if (taskRows.length > taskViewportRows) {
            panelRows.push({
              kind: "header",
              text: `${scroll + 1}-${Math.min(scroll + taskViewportRows, taskRows.length)} of ${taskRows.length}`,
              color: "dim",
            })
          }
        }

        const footerLines: SelectPanelLine[] = []
        if (selectedTask) {
          footerLines.push({ text: `ID: ${selectedTask.id}`, color: "dim" })
          if (selectedTask.outputExcerpt) {
            footerLines.push({ text: `Output: ${selectedTask.outputExcerpt}`, color: "dim" })
          }
          if (selectedTask.artifactPath) {
            footerLines.push({ text: `Artifact: ${selectedTask.artifactPath}`, color: "dim" })
          }
          if (selectedTask.error) {
            footerLines.push({ text: `Error: ${selectedTask.error}`, color: "#F87171" })
          }
          if (selectedTask.ctreeNodeId) {
            footerLines.push({ text: `CTree node: ${selectedTask.ctreeNodeId}`, color: "dim" })
          }
          footerLines.push({ text: "Enter: load output tail.", color: "dim" })
        }
        const ctreeSummary = formatCtreeSummary(selectedTask?.ctreeSnapshot ?? ctreeSnapshot ?? null)
        if (ctreeSummary) {
          footerLines.push({ text: ctreeSummary, color: "dim" })
        }
        if (taskNotice) {
          footerLines.push({ text: taskNotice, color: "dim" })
        }
        if (taskTailLines.length > 0) {
          footerLines.push({ text: taskTailPath ? `Tail: ${taskTailPath}` : "Tail output", color: "dim" })
          taskTailLines.forEach((line) => footerLines.push({ text: line, color: "dim" }))
        }
        return (
          <SelectPanel
            width={panelWidth}
            borderColor="#60A5FA"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={panelRows}
            footerLines={footerLines}
          />
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
          activeTabLabel("note", "Feedback"),
        ].join(chalk.gray("  |  "))

        const diffScrollMax = Math.max(0, permissionDiffLines.length - permissionViewportRows)
        const diffScroll = Math.max(0, Math.min(permissionScroll, diffScrollMax))
        const visibleDiff = permissionDiffLines.slice(diffScroll, diffScroll + permissionViewportRows)

        const scopeLabel = (scope: PermissionRuleScope, label: string) =>
          permissionScope === scope ? chalk.bold.cyan(label) : chalk.gray(label)

        const titleLines: SelectPanelLine[] = [
          { text: `${chalk.bold("Permission required")} ${chalk.dim(`(${permissionRequest.tool})`)}`, color: "#FACC15" },
        ]
        const hintLines: SelectPanelLine[] = [
          { text: `${permissionRequest.kind} • ${rewindableText}${queueText}`, color: "dim" },
          { text: tabLine },
        ]
        const rows: SelectPanelRow[] = []
        const footerLines: SelectPanelLine[] = []

        if (permissionTab === "summary") {
          rows.push({ kind: "item", text: permissionRequest.summary, wrap: "wrap" })
          if (permissionDiffPreview) {
            rows.push({
              kind: "header",
              text: `Diff: +${permissionDiffPreview.additions} -${permissionDiffPreview.deletions}${permissionDiffPreview.files.length > 0 ? ` • ${permissionDiffPreview.files.join(", ")}` : ""}`,
              color: "dim",
            })
          }
          if (!permissionDiffPreview && diffAvailable) {
            rows.push({ kind: "header", text: "Diff attached.", color: "dim" })
          }
          if (!diffAvailable) {
            rows.push({ kind: "header", text: "No diff attached.", color: "dim" })
          }
        }

        if (permissionTab === "diff") {
          if (!diffAvailable) {
            rows.push({ kind: "empty", text: "No diff attached.", color: "dim" })
          } else {
            rows.push({
              kind: "header",
              text: permissionSelectedSection
                ? `File ${permissionSelectedFileIndex + 1}/${permissionDiffSections.length}: ${permissionSelectedSection.file}`
                : "Diff",
              color: "gray",
            })
            rows.push({
              kind: "header",
              text: `${permissionDiffSections.length > 1 ? "←/→ files • " : ""}↑/↓ scroll • PgUp/PgDn page.${permissionDiffLines.length > 0 ? ` Lines ${diffScroll + 1}-${Math.min(diffScroll + permissionViewportRows, permissionDiffLines.length)} of ${permissionDiffLines.length}.` : ""}`,
              color: "dim",
            })
            if (visibleDiff.length === 0) {
              rows.push({ kind: "empty", text: "Diff is empty.", color: "dim" })
            } else {
              visibleDiff.forEach((line, index) => {
                rows.push({ kind: "item", text: line, wrap: "truncate-end" })
              })
            }
          }
        }

        if (permissionTab === "rules") {
          rows.push({ kind: "header", text: `Default scope: ${permissionRequest.defaultScope}`, color: "gray" })
          rows.push({
            kind: "item",
            text: `Scope: ${scopeLabel("project", "Project")} ${chalk.dim("(fixed)")}`,
          })
          rows.push({
            kind: "header",
            text: `Suggested rule: ${permissionRequest.ruleSuggestion ? chalk.italic(permissionRequest.ruleSuggestion) : chalk.dim("<none>")}`,
            color: "dim",
          })
          rows.push({ kind: "header", text: "Press 2 or Shift+Tab to allow always (project scope).", color: "dim" })
        }

        if (permissionTab === "note") {
          rows.push({ kind: "header", text: "Tell me what to do differently:", color: "gray" })
          rows.push({ kind: "item", text: renderPermissionNoteLine(permissionNote, permissionNoteCursor), wrap: "truncate-end" })
          rows.push({ kind: "header", text: "Type feedback • Enter deny once • Tab switch tabs", color: "dim" })
        }

        if (permissionError) {
          rows.push({ kind: "header", text: `Error: ${permissionError}`, color: "#FB7185" })
        }

        footerLines.push({
          text: "Tab switch panel • Enter allow once • Shift+Tab allow always (project) • 1 allow once • 2 allow always (project) • 3 feedback • d deny once • D deny always",
          color: "gray",
        })
        footerLines.push({ text: "Esc stop (deny)", color: "#FB7185" })

        return (
          <SelectPanel
            width={PANEL_WIDTH}
            borderColor="#F97316"
            paddingX={2}
            paddingY={1}
            titleLines={titleLines}
            hintLines={hintLines}
            rows={rows}
            footerLines={footerLines}
          />
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
          toggleHint={keymap === "claude" ? "Ctrl+O transcript" : "Ctrl+T transcript"}
          detailLabel={transcriptDetailLabel || undefined}
          variant={keymap === "claude" ? "claude" : "default"}
        />
      ) : (
        <ModalHost stack={modalStack}>{baseContent}</ModalHost>
      )}
    </Box>
  )
}
