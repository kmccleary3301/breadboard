import path from "node:path"
import React, { useCallback } from "react"
import { Box, Text } from "ink"
import type { ToolLogEntry } from "../../../types.js"
import { TOOL_EVENT_COLOR } from "../../../viewUtils.js"
import { CHALK, COLORS, DASH_SEPARATOR, DELTA_GLYPH, GLYPHS } from "../theme.js"
import { computeDiffPreview } from "../../../transcriptUtils.js"
import { maybeHighlightCode } from "../../../shikiHighlighter.js"
import { computeInlineDiffSpans, shiftSpans, type InlineSpan } from "../../../diff/inlineDiff.js"

interface ToolRendererOptions {
  readonly claudeChrome: boolean
  readonly verboseOutput: boolean
  readonly collapseThreshold: number
  readonly collapseHead: number
  readonly collapseTail: number
  readonly labelWidth: number
  /** Inner content width (excludes outer padding), in terminal columns. */
  readonly contentWidth: number
  readonly diffLineNumbers?: boolean
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null
const toLineArray = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.map((entry) => String(entry)).filter((line) => line.trim().length > 0)
  }
  if (typeof value === "string") {
    return value.split(/\r?\n/).filter((line) => line.trim().length > 0)
  }
  return []
}

const DIFF_BG_COLORS = {
  add: "#123225",
  del: "#3a1426",
  hunk: "#2c2436",
} as const

const DIFF_FG_COLORS = {
  add: "#2dbd8d",
  del: COLORS.error,
  hunk: COLORS.accent,
  meta: COLORS.info,
} as const

type DiffLineKind = "meta" | "hunk" | "add" | "del" | "context"
type AnnotatedDiffLine = { line: string; oldNo: number | null; newNo: number | null; kind: DiffLineKind }

const DIFF_HUNK_PATTERN = /@@\s*-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@/

const DIFF_INLINE_BG_COLORS = {
  add: "#1a4d33",
  del: "#6d1c3a",
  hunk: "#3b2f4d",
} as const

const MAX_TOKENIZED_DIFF_LINES = 400
const TOOL_PREVIEW_MAX_LINES = 24

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
const backgroundAnsiCode = (hex: string): string => {
  const rgb = hexToRgb(hex)
  return rgb ? `\u001b[48;2;${rgb[0]};${rgb[1]};${rgb[2]}m` : ""
}

const readAnsiToken = (value: string, start: number): string | null => {
  if (value[start] !== "\u001b" || value[start + 1] !== "[") return null
  const end = value.indexOf("m", start + 2)
  if (end === -1) return null
  return value.slice(start, end + 1)
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

const applyDiffBackground = (line: string, kind: DiffLineKind): string => {
  if (kind === "add") return CHALK.bgHex(DIFF_BG_COLORS.add)(line)
  if (kind === "del") return CHALK.bgHex(DIFF_BG_COLORS.del)(line)
  if (kind === "hunk") return CHALK.bgHex(DIFF_BG_COLORS.hunk)(line)
  return line
}

const styleDiffLine = (line: string, kind: DiffLineKind, withBackground = true): string => {
  if (!line) return line
  const hasAnsi = line.includes("\u001b")
  let styled = line
  if (!hasAnsi) {
    if (kind === "add") styled = CHALK.hex(DIFF_FG_COLORS.add)(line)
    else if (kind === "del") styled = CHALK.hex(DIFF_FG_COLORS.del)(line)
    else if (kind === "hunk") styled = CHALK.hex(DIFF_FG_COLORS.hunk)(line)
    else if (kind === "meta") styled = CHALK.hex(DIFF_FG_COLORS.meta)(line)
  }
  if (!withBackground) return styled
  return applyDiffBackground(styled, kind)
}

const extractToolPayload = (
  entry: ToolLogEntry,
): { name: string; args?: Record<string, unknown>; output?: string; status?: string; display?: Record<string, unknown> } | null => {
  const trimmed = entry.text.trim()
  const bracket = trimmed.indexOf("]")
  if (bracket === -1) return null
  const jsonText = trimmed.slice(bracket + 1).trim()
  if (!jsonText.startsWith("{")) return null
  try {
    const payload = JSON.parse(jsonText) as unknown
    const tool = isRecord(payload) && isRecord(payload.tool) ? payload.tool : (payload as Record<string, unknown>)
    const getString = (value: unknown): string | undefined => (typeof value === "string" ? value : undefined)
    const name = getString(tool.name) || getString(tool.tool) || getString(tool.command) || "Tool"
    const args = isRecord(tool.args) ? (tool.args as Record<string, unknown>) : undefined
    const output =
      getString(tool.output) ||
      getString(tool.stdout) ||
      getString(tool.content) ||
      getString(isRecord(payload) ? payload.output : undefined) ||
      getString(isRecord(payload) ? payload.stdout : undefined) ||
      getString(isRecord(payload) ? payload.content : undefined)
    const status = getString(tool.status) || getString(isRecord(payload) ? payload.status : undefined)
    const display = isRecord(payload) && isRecord(payload.display) ? (payload.display as Record<string, unknown>) : undefined
    return { name, args, output, status, display }
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

const formatStatusTag = (value: string): string => {
  return value.replace(/[_-]+/g, " ").trim().replace(/\b\w/g, (char) => char.toUpperCase())
}

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
    if (tagLower === "status") {
      header = remainderTrimmed || rest.shift() || ""
    } else if (!remainderTrimmed) {
      header = formatStatusTag(tagLabel)
    } else if (remainderTrimmed.toLowerCase().startsWith(tagLower)) {
      header = remainderTrimmed
    } else {
      header = `${formatStatusTag(tagLabel)} · ${remainderTrimmed}`
    }
  }
  const lines = [header, ...rest]
  return lines.filter((line, index) => index === 0 || line.trim().length > 0)
}

const formatToolArgsSummary = (args?: Record<string, unknown>): string | null => {
  if (!args) return null
  const preferredKeys = ["command", "cmd", "path", "file", "query", "pattern", "url", "target", "input"]
  for (const key of preferredKeys) {
    const value = args[key]
    if (typeof value === "string" && value.trim().length > 0) {
      const trimmed = value.replace(/\s+/g, " ").trim()
      const clipped = trimmed.length > 80 ? `${trimmed.slice(0, 77)}${GLYPHS.ellipsis}` : trimmed
      return clipped
    }
  }
  if (typeof args.diff === "string" || typeof args.patch === "string") return "diff"
  return null
}

const stripDiffPrefix = (line: string): string => {
  if (!line) return line
  const first = line[0]
  if (first === "+" || first === "-" || first === " ") {
    return line.slice(1)
  }
  return line
}

const applyWriteBackground = (line: string): string => {
  const bg = backgroundAnsiCode(DIFF_INLINE_BG_COLORS.add)
  if (!bg) return line
  let out = ""
  let index = 0
  while (index < line.length) {
    const token = readAnsiToken(line, index)
    if (token) {
      out += token
      out += bg
      index += token.length
      continue
    }
    out += line[index]
    index += 1
  }
  return `${bg}${out}${ANSI_BG_RESET}`
}

const resolveWriteLanguage = (pathValue: string | null): string | null => {
  if (!pathValue) return null
  return inferLanguageFromPath(pathValue)
}

const buildWriteBlockLines = (
  lines: string[],
  diffLineNumbers: boolean,
  maxLines = TOOL_PREVIEW_MAX_LINES,
  language?: string | null,
): string[] => {
  const trimmed = lines.map((line) => line.replace(/\r?\n/g, ""))
  const highlighted =
    language && trimmed.length > 0 && trimmed.length <= MAX_TOKENIZED_DIFF_LINES
      ? maybeHighlightCode(trimmed.join("\n"), language)
      : null
  const visible = trimmed.slice(0, maxLines)
  const hidden = Math.max(0, trimmed.length - visible.length)
  const width = diffLineNumbers ? Math.max(2, String(visible.length).length) : 0
  const output = visible.map((line, index) => {
    const number = diffLineNumbers ? String(index + 1).padStart(width, " ") : ""
    const prefix = diffLineNumbers ? `${number} + ` : "+ "
    const baseLine = highlighted?.[index] ?? line
    const prefixStyled = baseLine.includes("\u001b") ? CHALK.hex(DIFF_FG_COLORS.add)(prefix) : prefix
    const combined = `${prefixStyled}${baseLine}`.trimEnd()
    const styled = baseLine.includes("\u001b") ? combined : CHALK.hex(DIFF_FG_COLORS.add)(combined)
    return applyWriteBackground(styled)
  })
  if (hidden > 0) {
    const spacer = diffLineNumbers ? " ".repeat(width) : ""
    const prefix = diffLineNumbers ? `${spacer}   ` : "  "
    output.push(CHALK.gray(`${prefix}… (${hidden} lines hidden)`))
  }
  return output
}

const buildDiffBlockLines = (
  block: Record<string, unknown>,
  diffLineNumbers: boolean,
): string[] => {
  const unified = typeof block.unified === "string" ? block.unified : ""
  if (!unified.trim()) return []
  const rawLines = unified.replace(/\r\n?/g, "\n").split("\n")
  const annotated = annotateUnifiedDiffLines(rawLines)
  const allVisible = annotated.filter((entry) => entry.kind !== "meta" && entry.kind !== "hunk")
  const hidden = Math.max(0, allVisible.length - TOOL_PREVIEW_MAX_LINES)
  const visible = hidden > 0 ? allVisible.slice(0, TOOL_PREVIEW_MAX_LINES) : allVisible
  const baseLines = visible.map((entry) => stripDiffPrefix(entry.line))
  const diffLang = resolveDiffLanguage(block)
  const highlightedLines =
    diffLang && baseLines.length > 0 && baseLines.length <= MAX_TOKENIZED_DIFF_LINES
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
  if (diffLineNumbers) {
    const maxNum = Math.max(0, ...visible.map((line) => line.newNo ?? line.oldNo ?? 0))
    width = Math.max(2, String(maxNum).length)
  }
  const output = visible.map((entry, index) => {
    const baseLine = highlightedLines?.[index] ?? baseLines[index] ?? stripDiffPrefix(entry.line)
    const marker = entry.kind === "add" ? "+" : entry.kind === "del" ? "-" : " "
    const number =
      diffLineNumbers && (entry.newNo != null || entry.oldNo != null)
        ? String((entry.newNo ?? entry.oldNo) as number).padStart(width, " ")
        : diffLineNumbers
          ? " ".repeat(width)
          : ""
    const prefix = diffLineNumbers ? `${number} ${marker} ` : `${marker} `
    const combined = `${prefix}${baseLine}`.trimEnd()
    const spans = inlineMap.get(index) ?? []
    if (spans.length > 0 && (entry.kind === "add" || entry.kind === "del")) {
      const baseFg = styleDiffLine(combined, entry.kind, false)
      const baseBg = backgroundAnsiCode(DIFF_BG_COLORS[entry.kind])
      const inlineBg = backgroundAnsiCode(DIFF_INLINE_BG_COLORS[entry.kind])
      if (baseBg && inlineBg) {
        const shifted = shiftSpans(spans, prefix.length)
        const withInline = applyInlineBackground(baseFg, shifted, baseBg, inlineBg)
        return `${baseBg}${withInline}${ANSI_BG_RESET}`
      }
    }
    return styleDiffLine(combined, entry.kind, true)
  })
  if (hidden > 0) {
    const spacer = diffLineNumbers ? " ".repeat(width) : ""
    const prefix = diffLineNumbers ? `${spacer}   ` : "  "
    output.push(CHALK.gray(`${prefix}… (${hidden} lines hidden)`))
  }
  return output
}

const countDisplayLines = (
  display: Record<string, unknown>,
  kind: ToolLogEntry["kind"],
  collapseThreshold: number,
  collapseHead: number,
  collapseTail: number,
  verboseOutput: boolean,
): number => {
  const summaryLines = toLineArray(display.summary)
  const detailLines = toLineArray(display.detail)
  const truncated = isRecord(display.detail_truncated) ? (display.detail_truncated as Record<string, unknown>) : null
  const hidden = truncated && typeof truncated.hidden === "number" ? truncated.hidden : null
  const mode = truncated && typeof truncated.mode === "string" ? truncated.mode : null
  const tailCount = truncated && typeof truncated.tail === "number" ? truncated.tail : null
  const diffBlocks = Array.isArray(display.diff_blocks) ? (display.diff_blocks as Array<Record<string, unknown>>) : []
  let outputCount = summaryLines.length + detailLines.length
  diffBlocks.forEach((block) => {
    const unified = typeof block.unified === "string" ? block.unified : ""
    const diffLines = unified ? unified.replace(/\r\n?/g, "\n").split("\n").length : 0
    outputCount += (diffLines > 0 ? diffLines : 0) + 1
  })
  let visibleCount = outputCount
  if (!verboseOutput && !hidden && outputCount > collapseThreshold) {
    visibleCount = collapseHead + 1 + collapseTail
  } else if (hidden) {
    if (mode === "head_tail" && tailCount && tailCount > 0 && tailCount < outputCount) {
      const headCount = outputCount - tailCount
      visibleCount = headCount + 1 + tailCount
    } else {
      visibleCount = outputCount + 1
    }
  }
  return 1 + visibleCount
}

export const useToolRenderer = (options: ToolRendererOptions) => {
  const { claudeChrome, verboseOutput, collapseThreshold, collapseHead, collapseTail, labelWidth, diffLineNumbers, contentWidth } =
    options
  const wrapWidth = Math.max(1, Math.floor(contentWidth - 2))

  const splitToWidth = useCallback(
    (text: string) => {
      if (!text) return [""]
      if (text.length <= wrapWidth) return [text]
      const chunks: string[] = []
      for (let index = 0; index < text.length; index += wrapWidth) {
        chunks.push(text.slice(index, index + wrapWidth))
      }
      return chunks
    },
    [wrapWidth],
  )

  const measureToolEntryLines = useCallback(
    (entry: ToolLogEntry): number => {
      const displayPayload = entry.display && isRecord(entry.display) ? entry.display : null
      if (displayPayload && (entry.kind === "call" || entry.kind === "result")) {
        const title =
          (typeof displayPayload.title === "string" && displayPayload.title.trim().length > 0
            ? displayPayload.title
            : null) ?? entry.text.split(/\r?\n/)[0] ?? "Tool"
        const wrappedHeader = Math.max(1, Math.ceil(title.length / wrapWidth))
        const baseCount = countDisplayLines(
          displayPayload,
          entry.kind,
          collapseThreshold,
          collapseHead,
          collapseTail,
          verboseOutput,
        )
        return baseCount + Math.max(0, wrappedHeader - 1)
      }
      const lines = entry.text.split(/\r?\n/)
      if (lines.length > 0) {
        const first = lines[0] ?? ""
        const wrappedFirst = Math.max(1, Math.ceil(first.length / wrapWidth))
        const total = lines.length - 1 + wrappedFirst
        if (total <= collapseThreshold) return total
      } else if (lines.length <= collapseThreshold) {
        return lines.length
      }
      const head = lines.slice(0, collapseHead)
      const tail = lines.slice(-collapseTail)
      return head.length + 1 + tail.length
    },
    [collapseHead, collapseTail, collapseThreshold, verboseOutput, wrapWidth],
  )

  const renderToolEntry = useCallback(
    (entry: ToolLogEntry, key?: string) => {
      if (entry.kind === "status") {
        const lines = formatStatusLines(entry.text)
        const safeLines = lines.length > 0 ? lines : [entry.text]
        const statusTone =
          entry.status === "error"
            ? { bullet: COLORS.error, text: COLORS.error }
            : entry.status === "success"
              ? { bullet: COLORS.success, text: COLORS.textMuted }
              : entry.status === "pending"
                ? { bullet: COLORS.warning, text: COLORS.text }
                : { bullet: COLORS.muted, text: COLORS.textMuted }
        const glyph = CHALK.hex(statusTone.bullet)(GLYPHS.bullet)
        const colorizeStatusLine = (line: string) => {
          if (!line) return line
          if (line.includes("\u001b")) return line
          const normalized = line
          if (entry.status === "error") return CHALK.hex(COLORS.error)(normalized)
          if (entry.status === "pending") return CHALK.hex(COLORS.text)(normalized)
          if (entry.status === "success") return CHALK.hex(COLORS.textMuted)(normalized)
          return CHALK.hex(COLORS.textMuted)(normalized)
        }
        const renderStatusLine = (line: string, index: number) => (
          <Text key={`${entry.id}-status-${index}`}>
            {index === 0 ? `${glyph} ${colorizeStatusLine(line)}` : `${" ".repeat(2)}${colorizeStatusLine(line)}`}
          </Text>
        )
        if (verboseOutput || safeLines.length <= collapseThreshold) {
          return (
            <Box key={key ?? entry.id} flexDirection="column">
              {safeLines.map((line, idx) => renderStatusLine(line, idx))}
            </Box>
          )
        }
        const head = safeLines.slice(0, collapseHead)
        const tail = safeLines.slice(-collapseTail)
        const hiddenCount = safeLines.length - head.length - tail.length
        const summary = `${GLYPHS.ellipsis} ${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden (ctrl+o to expand)`
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {head.map((line, idx) => renderStatusLine(line, idx))}
            <Text color="gray">{`${" ".repeat(2)}${summary}`}</Text>
            {tail.map((line, idx) => renderStatusLine(line, head.length + idx + 1))}
          </Box>
        )
      }
      const isError = entry.status === "error" || entry.kind === "error"
      const glyph =
        entry.status === "success"
          ? CHALK.hex(COLORS.success)(GLYPHS.bullet)
          : entry.status === "error"
            ? CHALK.hex(COLORS.error)(GLYPHS.bullet)
            : entry.kind === "call"
              ? CHALK.hex(COLORS.info)(GLYPHS.bullet)
              : CHALK.hex(TOOL_EVENT_COLOR)(GLYPHS.bullet)
      const lines: string[] = entry.text.split(/\r?\n/)
      const colorizeToolLine = (line: string) => {
        if (!line) return line
        if (line.includes("\u001b")) return line
        if (isError) return CHALK.hex(COLORS.error)(line)
        if (line.startsWith("diff --git") || line.startsWith("index ")) return CHALK.hex(COLORS.info)(line)
        if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
        if (line.startsWith("---") || line.startsWith("+++")) return CHALK.hex(COLORS.info)(line)
        if (line.startsWith("+") && !line.startsWith("+++")) return CHALK.hex(COLORS.success)(line)
        if (line.startsWith("-") && !line.startsWith("---")) return CHALK.hex(COLORS.error)(line)
        return CHALK.gray(line)
      }
      const renderLine = (line: string, index: number) => {
        const segments = index === 0 ? splitToWidth(line) : [line]
        return segments.map((segment, segmentIndex) => (
          <Text key={`${entry.id}-ln-${index}-${segmentIndex}`} wrap="truncate-end">
            {index === 0 && segmentIndex === 0 ? `${glyph} ` : "  "}
            {index === 0 ? CHALK.hex(COLORS.textBright).bold(segment) : colorizeToolLine(segment)}
          </Text>
        ))
      }
      const parsed = extractToolPayload(entry)
      const renderDisplayLines = (display: Record<string, unknown>) => {
        const category = typeof display.category === "string" ? display.category : null
        const categoryColor =
          category === "patch"
            ? COLORS.accent
            : category === "exec"
              ? COLORS.warning
              : category === "fs"
                ? COLORS.info
                : category === "web"
                  ? COLORS.info
                  : category === "model"
                    ? COLORS.info
                    : category === "meta"
                      ? COLORS.muted
                      : null
        const displayGlyph = categoryColor ? CHALK.hex(categoryColor)(GLYPHS.bullet) : glyph
        const title = typeof display.title === "string" ? display.title : null
        let summaryLines = toLineArray(display.summary)
        const detailLines = toLineArray(display.detail)
        const truncated = isRecord(display.detail_truncated) ? (display.detail_truncated as Record<string, unknown>) : null
        const hidden = truncated && typeof truncated.hidden === "number" ? truncated.hidden : null
        const hint = truncated && typeof truncated.hint === "string" ? truncated.hint : null
        const tailCount = truncated && typeof truncated.tail === "number" ? truncated.tail : null
        const mode = truncated && typeof truncated.mode === "string" ? truncated.mode : null
        const debugId = typeof display.debug_rule_id === "string" ? display.debug_rule_id : null
        const debugSuffixRaw = debugId ? ` ⟪${debugId}⟫` : ""
        const debugSuffixColored = debugId ? CHALK.hex(COLORS.muted)(` ⟪${debugId}⟫`) : ""
        const outputLines = summaryLines.length || detailLines.length ? [...summaryLines, ...detailLines] : []
        const colorizeLine = (line: string) => {
          if (!line) return line
          if (line.includes("\u001b")) return line
          if (line.startsWith("diff --git") || line.startsWith("index ")) return CHALK.hex(COLORS.info)(line)
          if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
          if (line.startsWith("---") || line.startsWith("+++")) return CHALK.hex(COLORS.info)(line)
          if (line.startsWith("+") && !line.startsWith("+++")) return CHALK.hex(COLORS.success)(line)
          if (line.startsWith("-") && !line.startsWith("---")) return CHALK.hex(COLORS.error)(line)
          return CHALK.hex(COLORS.textMuted)(line)
        }
        const diffBlocks = Array.isArray(display.diff_blocks)
          ? (display.diff_blocks as Array<Record<string, unknown>>)
          : []
        const titleLower = (title ?? "").toLowerCase()
        const isWrite = titleLower.startsWith("write(")
        const writePath =
          typeof parsed?.args?.path === "string"
            ? parsed.args.path
            : typeof title === "string"
              ? title.match(/\((.+)\)$/)?.[1] ?? null
              : null
        const writeLanguage = isWrite ? resolveWriteLanguage(writePath) : null
        const isPatch =
          titleLower.startsWith("patch(") ||
          parsed?.name === "apply_patch" ||
          parsed?.name === "patch"
        if (isPatch && diffBlocks.length > 0) {
          summaryLines = []
          outputLines.length = 0
          outputLines.push(...detailLines)
        }
        if (diffBlocks.length > 0) {
          diffBlocks.forEach((block) => {
            outputLines.push(...buildDiffBlockLines(block, diffLineNumbers === true))
          })
        }
        if (diffBlocks.length === 0 && isWrite && detailLines.length > 0) {
          outputLines.length = 0
          outputLines.push(...summaryLines)
          outputLines.push(
            ...buildWriteBlockLines(detailLines, diffLineNumbers === true, TOOL_PREVIEW_MAX_LINES, writeLanguage),
          )
        }
        const derivedTitle =
          !title && parsed?.name && (parsed.name === "apply_patch" || parsed.name === "patch") && parsed?.args?.path
            ? `Patch(${String(parsed.args.path)})`
            : null
        const header = title ?? derivedTitle ?? normalizeToolName(parsed?.name ?? "Tool")
        const headerSegments = splitToWidth(`${header}${debugSuffixRaw}`)
        const headerNodes = headerSegments.map((segment, index) => (
          <Text key={`${entry.id}-header-${index}`} wrap="truncate-end">
            {index === 0 ? `${displayGlyph} ` : "  "}
            {CHALK.hex(COLORS.textBright).bold(segment)}
          </Text>
        ))
        if (entry.kind === "call" && outputLines.length === 0) {
          return (
            <Box key={key ?? entry.id} flexDirection="column">
              {headerNodes}
            </Box>
          )
        }
        const shouldCollapse =
          !verboseOutput && !hidden && outputLines.length > collapseThreshold
        const collapseHeadLines = shouldCollapse ? outputLines.slice(0, collapseHead) : outputLines
        const collapseTailLines = shouldCollapse ? outputLines.slice(-collapseTail) : []
        const hiddenCount = shouldCollapse ? outputLines.length - collapseHeadLines.length - collapseTailLines.length : 0
        let head = collapseHeadLines
        let tail = collapseTailLines
        if (!shouldCollapse && hidden && mode === "head_tail" && tailCount && tailCount > 0 && tailCount < outputLines.length) {
          head = outputLines.slice(0, outputLines.length - tailCount)
          tail = outputLines.slice(-tailCount)
        }
        if (debugSuffixColored && head.length > 0) {
          head = head.map((line, index) => (index === 0 ? `${line}${debugSuffixColored}` : line))
        }
        const renderDisplayLine = (line: string, index: number, isLast: boolean) => (
          <Text key={`${entry.id}-display-${index}`} wrap="truncate-end">
            {`  ${isLast ? GLYPHS.treeBranch : GLYPHS.treeLine} ${colorizeLine(line)}`}
          </Text>
        )
        const nodes: React.ReactNode[] = []
        head.forEach((line, index) => {
          const hasTail = tail.length > 0
          const hasHidden = (shouldCollapse && hiddenCount > 0) || (hidden && mode === "head_tail")
          const isLast = !hasTail && !hasHidden && index === head.length - 1
          nodes.push(renderDisplayLine(line, index, isLast))
        })
        if (shouldCollapse) {
          const hasTail = tail.length > 0
          const summaryLine = `${GLYPHS.ellipsis} ${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden (ctrl+o to expand)`
          nodes.push(
            <Text key={`${entry.id}-display-hidden`} color="gray" wrap="truncate-end">
              {`  ${hasTail ? GLYPHS.treeLine : GLYPHS.treeBranch} ${summaryLine}`}
            </Text>,
          )
        } else if (hidden && mode === "head_tail") {
          const hasTail = tail.length > 0
          const summaryLine = `${GLYPHS.ellipsis} ${hidden} ${hidden === 1 ? "line" : "lines"} hidden${hint ? ` ${hint}` : ""}`
          nodes.push(
            <Text key={`${entry.id}-display-hidden`} color="gray" wrap="truncate-end">
              {`  ${hasTail ? GLYPHS.treeLine : GLYPHS.treeBranch} ${summaryLine}`}
            </Text>,
          )
        }
        tail.forEach((line, index) => {
          const isLast = index === tail.length - 1
          const keyIndex = head.length + index + (shouldCollapse || (hidden && mode === "head_tail") ? 1 : 0)
          nodes.push(renderDisplayLine(line, keyIndex, isLast))
        })
        if (hidden && mode !== "head_tail") {
          nodes.push(
            <Text key={`${entry.id}-display-trunc`} color="gray" wrap="truncate-end">
              {`  ${GLYPHS.treeBranch} ${GLYPHS.ellipsis} ${hidden} ${hidden === 1 ? "line" : "lines"}${hint ? ` ${hint}` : ""}`}
            </Text>,
          )
        }
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {headerNodes}
            {nodes}
          </Box>
        )
      }
      const displayPayload = entry.display ?? (parsed?.display as Record<string, unknown> | undefined)
      if (displayPayload && (entry.kind === "call" || entry.kind === "result")) {
        return renderDisplayLines(displayPayload as Record<string, unknown>)
      }
      if (claudeChrome && (entry.kind === "call" || entry.kind === "result")) {
        if (parsed) {
          const toolName = normalizeToolName(parsed.name)
          const argsSummary = formatToolArgsSummary(parsed.args)
          const header = argsSummary ? `${toolName}(${argsSummary})` : toolName
          if (entry.kind === "call") {
            const headerSegments = splitToWidth(header)
            return (
              <Box key={key ?? entry.id} flexDirection="column">
                {headerSegments.map((segment, index) => (
                  <Text key={`${entry.id}-claude-header-${index}`} wrap="truncate-end">
                    {index === 0 ? `${glyph} ` : "  "}
                    {CHALK.hex(COLORS.textBright).bold(segment)}
                  </Text>
                ))}
              </Box>
            )
          }
          const outputText = parsed.output ?? parsed.status ?? "Done"
          const outputLines = outputText.split(/\r?\n/)
          const colorizeClaudeLine = (line: string) => {
            if (!line) return line
            if (line.includes("\u001b")) return line
            if (line.startsWith("diff --git") || line.startsWith("index ")) return CHALK.hex(COLORS.info)(line)
            if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
            if (line.startsWith("---") || line.startsWith("+++")) return CHALK.hex(COLORS.info)(line)
            if (line.startsWith("+") && !line.startsWith("+++")) return CHALK.hex(COLORS.success)(line)
            if (line.startsWith("-") && !line.startsWith("---")) return CHALK.hex(COLORS.error)(line)
            return line
          }
          return (
            <Box key={key ?? entry.id} flexDirection="column">
              {outputLines.map((line, index) => (
                <Text key={`${entry.id}-out-${index}`} wrap="truncate-end">
                  {`  ${index === outputLines.length - 1 ? GLYPHS.treeBranch : GLYPHS.treeLine} ${colorizeClaudeLine(line)}`}
                </Text>
              ))}
            </Box>
          )
        }
      }
      if (verboseOutput || lines.length <= collapseThreshold) {
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {lines.flatMap((line, idx) => renderLine(line, idx))}
          </Box>
        )
      }
      const head = lines.slice(0, collapseHead)
      const tail = lines.slice(-collapseTail)
      const hiddenCount = lines.length - head.length - tail.length
      const diffPreview = computeDiffPreview(lines)
      const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
      const summaryParts = [
        `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden (ctrl+o to expand)`,
        diffPreview ? `${DELTA_GLYPH} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
      ].filter(Boolean)
      return (
        <Box key={key ?? entry.id} flexDirection="column">
          {head.flatMap((line, idx) => renderLine(line, idx))}
          <Text color="gray">
            {`  ${GLYPHS.ellipsis} ${summaryParts.join(DASH_SEPARATOR)}`}
          </Text>
          {tail.flatMap((line, idx) => renderLine(line, head.length + idx + 1))}
        </Box>
      )
    },
    [
      claudeChrome,
      collapseHead,
      collapseTail,
      collapseThreshold,
      labelWidth,
      splitToWidth,
      verboseOutput,
    ],
  )

  return { renderToolEntry, measureToolEntryLines }
}
