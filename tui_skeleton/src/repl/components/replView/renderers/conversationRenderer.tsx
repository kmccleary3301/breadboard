import React, { useCallback } from "react"
import { Box, Text } from "ink"
import type { ConversationEntry, TranscriptPreferences } from "../../../types.js"
import { speakerColor } from "../../../viewUtils.js"
import {
  computeDiffPreview,
  ENTRY_COLLAPSE_HEAD,
  ENTRY_COLLAPSE_TAIL,
  stripInlineThinkingMarker,
  shouldAutoCollapseEntry,
} from "../../../transcriptUtils.js"
import { blocksToLinesWithRawFallback, renderMarkdownFallbackLines } from "./markdown/streamMdxAdapter.js"
import type { MarkdownRenderOptions } from "./markdown/streamMdxAdapter.js"
import { cachedBlocksToLines, TerminalLineChunkCache } from "./markdown/terminalLineChunkCache.js"
import { stripAnsiCodes, stringWidth } from "../utils/ansi.js"
import { CHALK, COLORS, DASH_SEPARATOR, DELTA_GLYPH, GLYPHS, HOLLOW_DOT, STAR_GLYPH } from "../theme.js"

const MARKDOWN_BLOCK_CACHE_ENABLED = process.env.BREADBOARD_MARKDOWN_BLOCK_CACHE === "1"
const markdownLineChunkCache = new TerminalLineChunkCache()

const padVisibleEnd = (value: string, width: number): string => {
  const safeWidth = Math.max(1, Math.floor(width))
  const visibleLength = stringWidth(stripAnsiCodes(value))
  return visibleLength >= safeWidth ? value : `${value}${" ".repeat(safeWidth - visibleLength)}`
}

const renderRichMarkdownLines = (
  entry: ConversationEntry,
  options: MarkdownRenderOptions,
): string[] => {
  const blocks = entry.richBlocks ?? []
  if (!MARKDOWN_BLOCK_CACHE_ENABLED) return blocksToLinesWithRawFallback(blocks, entry.text, options)
  const cached = cachedBlocksToLines(markdownLineChunkCache, blocks, options, { messageId: entry.id })
  const fallback = blocksToLinesWithRawFallback(blocks, entry.text, options)
  return fallback.length > cached.length ? fallback : cached
}

const trimTrailingBlankLines = (lines: readonly string[]): string[] => {
  let end = lines.length
  while (end > 0 && stripAnsiCodes(lines[end - 1] ?? "").trim().length === 0) {
    end -= 1
  }
  return lines.slice(0, end)
}

const trimOuterBlankLines = (lines: readonly string[]): string[] => {
  const trailingTrimmed = trimTrailingBlankLines(lines)
  let start = 0
  while (start < trailingTrimmed.length && stripAnsiCodes(trailingTrimmed[start] ?? "").trim().length === 0) {
    start += 1
  }
  return trailingTrimmed.slice(start)
}

const buildAssistantActivePreviewLines = (
  text: string,
  maxLines: number,
  markdownWidth: number,
  label: string,
): string[] => {
  const meaningful = text
    .split(/\r?\n/)
    .map((line) => stripAnsiCodes(line).trim())
    .filter(Boolean)
  const selected = meaningful.find((line) => /^Verification:/i.test(line)) ?? meaningful[meaningful.length - 1] ?? ""
  const suffix = selected.length > 0 ? ` ${selected.slice(0, Math.max(12, markdownWidth - 24))}` : ""
  return [CHALK.dim(`${label}${suffix}`)].slice(0, Math.max(1, maxLines))
}

const tailRenderLines = (lines: readonly string[], maxLines: number): string[] => {
  const trimmed = trimOuterBlankLines(lines)
  const budget = Math.max(1, Math.floor(maxLines))
  if (trimmed.length <= budget) return trimmed
  return trimmed.slice(-budget)
}

export const resolveConversationEntryDisplayLines = (
  entry: ConversationEntry,
  options: {
    readonly viewPrefs: TranscriptPreferences
    readonly markdownWidth: number
    readonly markdownRenderOptions?: Omit<MarkdownRenderOptions, "width">
    readonly streamingAssistantPreviewLines?: number
  },
): string[] => {
  const { viewPrefs, markdownWidth, markdownRenderOptions, streamingAssistantPreviewLines } = options
  const hasRichBlocks = Boolean(entry.richBlocks && entry.richBlocks.length > 0)
  const useRich = viewPrefs.richMarkdown && hasRichBlocks && !entry.markdownError
  const normalizedText = stripInlineThinkingMarker(entry.text)
  const activePreviewLines =
    entry.speaker === "assistant" &&
    typeof entry.activePreviewLines === "number" &&
    entry.activePreviewLines > 0
  const streamingPreview =
    entry.speaker === "assistant" &&
    entry.phase === "streaming" &&
    typeof streamingAssistantPreviewLines === "number"
  const fallback = viewPrefs.richMarkdown
    ? renderMarkdownFallbackLines(normalizedText, { ...markdownRenderOptions, width: markdownWidth })
    : normalizedText.split(/\r?\n/)
  if (streamingPreview) {
    const budget = Math.max(1, streamingAssistantPreviewLines)
    if (useRich) {
      return tailRenderLines(renderRichMarkdownLines(entry, { ...markdownRenderOptions, width: markdownWidth }), budget)
    }
    const fallbackTail = tailRenderLines(fallback, budget)
    return fallbackTail.some((line) => stripAnsiCodes(line).trim().length > 0)
      ? fallbackTail
      : buildAssistantActivePreviewLines(normalizedText, budget, markdownWidth, "Assistant streaming…")
  }
  if (activePreviewLines) {
    const budget = Math.max(1, entry.activePreviewLines ?? 1)
    return tailRenderLines(useRich
      ? renderRichMarkdownLines(entry, { ...markdownRenderOptions, width: markdownWidth })
      : fallback,
    budget)
  }
  return trimOuterBlankLines(useRich
    ? renderRichMarkdownLines(entry, { ...markdownRenderOptions, width: markdownWidth })
    : fallback)
}

interface ConversationMeasureOptions {
  readonly viewPrefs: TranscriptPreferences
  readonly verboseOutput: boolean
  readonly collapsedEntriesRef: React.MutableRefObject<Map<string, boolean>>
  readonly collapsedVersion: number
  /** Inner content width (excludes outer padding), in terminal columns. */
  readonly contentWidth: number
  readonly markdownRenderOptions?: Omit<MarkdownRenderOptions, "width">
  readonly streamingAssistantPreviewLines?: number
}

interface ConversationRenderOptions {
  readonly viewPrefs: TranscriptPreferences
  readonly collapsedEntriesRef: React.MutableRefObject<Map<string, boolean>>
  readonly collapsedVersion: number
  readonly collapsibleMeta: Map<string, { index: number; total: number }>
  readonly selectedCollapsibleEntryId: string | null
  readonly labelWidth: number
  /** Inner content width (excludes outer padding), in terminal columns. */
  readonly contentWidth: number
  readonly isEntryCollapsible: (entry: ConversationEntry) => boolean
  readonly markdownRenderOptions?: Omit<MarkdownRenderOptions, "width">
  readonly padLineEnds?: boolean
  readonly streamingAssistantPreviewLines?: number
}

const splitTokenByWidth = (token: string, maxWidth: number): string[] => {
  if (maxWidth <= 0) return [token]
  const parts: string[] = []
  let current = ""
  let width = 0
  for (const char of token) {
    const charWidth = stringWidth(char)
    if (width + charWidth > maxWidth && current.length > 0) {
      parts.push(current)
      current = ""
      width = 0
    }
    current += char
    width += charWidth
  }
  if (current.length > 0) parts.push(current)
  return parts
}

const countWrappedTerminalLines = (value: string, maxWidth: number): number => {
  const width = Math.max(1, Math.floor(maxWidth))
  const plain = stripAnsiCodes(value)
  const logicalLines = plain.split(/\r?\n/)
  let total = 0
  for (const logicalLine of logicalLines) {
    if (logicalLine.length === 0) {
      total += 1
      continue
    }
    const tokens = logicalLine.split(/(\s+)/)
    let currentWidth = 0
    let lineCount = 1
    for (const token of tokens) {
      if (token.length === 0) continue
      if (/^\s+$/.test(token)) {
        if (currentWidth === 0) continue
        const tokenWidth = stringWidth(token)
        if (currentWidth + tokenWidth <= width) {
          currentWidth += tokenWidth
        } else {
          lineCount += 1
          currentWidth = 0
        }
        continue
      }
      const tokenWidth = stringWidth(token)
      if (tokenWidth <= width) {
        if (currentWidth + tokenWidth <= width) {
          currentWidth += tokenWidth
        } else {
          lineCount += 1
          currentWidth = tokenWidth
        }
        continue
      }
      const pieces = splitTokenByWidth(token, width)
      for (const piece of pieces) {
        const pieceWidth = stringWidth(piece)
        if (currentWidth > 0) {
          lineCount += 1
          currentWidth = 0
        }
        currentWidth = Math.min(pieceWidth, width)
      }
    }
    total += lineCount
  }
  return total
}

export const useConversationMeasure = (options: ConversationMeasureOptions) => {
  const { viewPrefs, verboseOutput, collapsedEntriesRef, collapsedVersion, contentWidth, markdownRenderOptions, streamingAssistantPreviewLines } = options

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

  const markdownWidth = Math.max(1, Math.floor(contentWidth - 2))

  const measureConversationEntryLines = useCallback(
    (entry: ConversationEntry): number => {
      if (entry.speaker === "system") return 0
      const lines = resolveConversationEntryDisplayLines(entry, {
        viewPrefs,
        markdownWidth,
        markdownRenderOptions,
        streamingAssistantPreviewLines,
      })
      const plainLines = lines.map(stripAnsiCodes)

      const speakerGlyph =
        entry.speaker === "user"
          ? GLYPHS.chevron
          : entry.speaker === "assistant"
            ? ""
            : GLYPHS.systemDot
      const label = speakerGlyph
      const padLabel = entry.speaker === "assistant" ? "" : "  "

      let rowCount = 0

      if (entry.markdownError && entry.speaker === "assistant") {
        rowCount += countWrappedTerminalLines(
          `${label} rich markdown disabled: ${entry.markdownError}`,
          contentWidth,
        )
      }

      const countRenderedLine = (line: string, index: number) => {
        const prefix = index === 0 ? `${label} ` : padLabel
        rowCount += countWrappedTerminalLines(`${prefix}${line}`, contentWidth)
      }
      const collapsible = isEntryCollapsible(entry)
      const collapsed = collapsible ? collapsedEntriesRef.current.get(entry.id) !== false : false
      if (!collapsible || !collapsed) {
        if (lines.length === 0) {
          rowCount += 1
        } else {
          lines.forEach((line, idx) => countRenderedLine(line, idx))
        }
        if (entry.speaker === "user") rowCount += 1
        return Math.max(1, rowCount)
      }
      const head = lines.slice(0, ENTRY_COLLAPSE_HEAD)
      const tail = lines.slice(-ENTRY_COLLAPSE_TAIL)
      const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
      head.forEach((line, idx) => countRenderedLine(line, idx))
      if (hiddenCount > 0) {
        const diffPreview = computeDiffPreview(plainLines)
        const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
        const blockPlaceholder = "block 99/99"
        const summaryParts = [
          `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
          blockPlaceholder,
          diffPreview ? `${DELTA_GLYPH} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
        ].filter(Boolean)
        const summary = summaryParts.join(` ${GLYPHS.bullet} `)
        const instruction = "use [ / ] to target, then press e expand / o inspect"
        const summaryLine = `${padLabel} ${GLYPHS.bullet} ${summary}${DASH_SEPARATOR}${instruction}`
        rowCount += countWrappedTerminalLines(summaryLine, contentWidth)
      }
      tail.forEach((line, idx) => countRenderedLine(line, head.length + idx + 1))
      return Math.max(1, rowCount)
    },
    [collapsedVersion, contentWidth, isEntryCollapsible, markdownRenderOptions, markdownWidth, viewPrefs.richMarkdown],
  )

  return { isEntryCollapsible, measureConversationEntryLines }
}

export const useConversationRenderer = (options: ConversationRenderOptions) => {
  const {
    viewPrefs,
    collapsedEntriesRef,
    collapsedVersion,
    collapsibleMeta,
    selectedCollapsibleEntryId,
    labelWidth,
    contentWidth,
    isEntryCollapsible,
    markdownRenderOptions,
    padLineEnds = true,
    streamingAssistantPreviewLines,
  } = options

  const markdownWidth = Math.max(1, Math.floor(contentWidth - 2))

  const renderConversationEntry = useCallback(
    (entry: ConversationEntry, key?: string) => {
      if (entry.speaker === "system") {
        return null
      }
      const hasRichBlocks = Boolean(entry.richBlocks && entry.richBlocks.length > 0)
      const useRich = viewPrefs.richMarkdown && hasRichBlocks && !entry.markdownError
      const normalizedText = stripInlineThinkingMarker(entry.text)
      const rawText = normalizedText.trim()
      if (!hasRichBlocks && !entry.markdownError && (!rawText || rawText.toLowerCase() === "none")) {
        return null
      }
      const speakerGlyph =
        entry.speaker === "user"
          ? GLYPHS.chevron
          : entry.speaker === "assistant"
            ? ""
            : GLYPHS.systemDot
      const label = speakerGlyph ? CHALK.hex(speakerColor(entry.speaker))(speakerGlyph) : ""
      const padLabel = entry.speaker === "assistant" ? "" : "  "
      const errorLine =
        entry.markdownError && entry.speaker === "assistant"
          ? (
              <Text key={`${entry.id}-md-error`} color={COLORS.error}>
                {label} rich markdown disabled: {entry.markdownError}
              </Text>
            )
          : null
      const colorizeContent = useRich
        ? (line: string) => line
        : (line: string) => {
            if (line.startsWith("diff --git") || line.startsWith("index ")) return CHALK.hex(COLORS.info)(line)
            if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
            if (line.startsWith("---") || line.startsWith("+++")) return CHALK.hex(COLORS.info)(line)
            if (line.startsWith("+") && !line.startsWith("+++")) return CHALK.hex(COLORS.success)(line)
            if (line.startsWith("-") && !line.startsWith("---")) return CHALK.hex(COLORS.error)(line)
            return line
      }
      const renderLine = (line: string, index: number) => {
        const prefix = index === 0 ? (label ? `${label} ` : "") : padLabel
        const renderedLine = `${prefix}${colorizeContent(line)}`
        return (
          <Text key={`${entry.id}-ln-${index}`} wrap="truncate-end">
            {padLineEnds ? padVisibleEnd(renderedLine, contentWidth) : renderedLine}
          </Text>
        )
      }
      const lines = resolveConversationEntryDisplayLines(entry, {
        viewPrefs,
        markdownWidth,
        markdownRenderOptions,
        streamingAssistantPreviewLines,
      })
      const plainLines = useRich ? lines.map(stripAnsiCodes) : lines.map(stripAnsiCodes)
      const collapsible = isEntryCollapsible(entry)
      const collapsed = collapsible ? collapsedEntriesRef.current.get(entry.id) !== false : false
      if (!collapsible || !collapsed) {
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {errorLine}
            {lines.map((line, idx) => renderLine(line, idx))}
            {entry.speaker === "user" ? <Text key={`${entry.id}-after-user`}> </Text> : null}
          </Box>
        )
      }
      const head = lines.slice(0, ENTRY_COLLAPSE_HEAD)
      const tail = lines.slice(-ENTRY_COLLAPSE_TAIL)
      const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
      const diffPreview = computeDiffPreview(plainLines)
      const meta = collapsibleMeta.get(entry.id)
      const isSelected = selectedCollapsibleEntryId === entry.id
      const selectionGlyph = isSelected ? CHALK.hex(COLORS.info)(GLYPHS.select) : CHALK.dim(GLYPHS.bullet)
      const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
      const summaryParts = [
        `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
        meta ? `block ${meta.index + 1}/${meta.total}` : null,
        diffPreview ? `${DELTA_GLYPH} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
      ].filter(Boolean)
      const summary = summaryParts.join(` ${GLYPHS.bullet} `)
      const instruction = isSelected ? "press e to expand/collapse, o inspect" : "use [ / ] to target, then press e expand / o inspect"
      return (
        <Box key={key ?? entry.id} flexDirection="column">
          {errorLine}
          {head.map((line, idx) => renderLine(line, idx))}
          {hiddenCount > 0 && (
            <Text color={isSelected ? COLORS.info : "gray"}>
              {`${padLabel} ${selectionGlyph} ${summary}${DASH_SEPARATOR}${instruction}`}
            </Text>
          )}
          {tail.map((line, idx) => renderLine(line, head.length + idx + 1))}
        </Box>
      )
    },
    [
      collapsedVersion,
      collapsibleMeta,
      isEntryCollapsible,
      labelWidth,
      markdownRenderOptions,
      markdownWidth,
      selectedCollapsibleEntryId,
      streamingAssistantPreviewLines,
      padLineEnds,
      viewPrefs.richMarkdown,
    ],
  )

  return { renderConversationEntry }
}
