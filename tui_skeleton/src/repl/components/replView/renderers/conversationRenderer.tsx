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
import { blocksToLines, renderMarkdownFallbackLines } from "./markdown/streamMdxAdapter.js"
import type { MarkdownRenderOptions } from "./markdown/streamMdxAdapter.js"
import { stripAnsiCodes, stringWidth } from "../utils/ansi.js"
import { CHALK, COLORS, DASH_SEPARATOR, DELTA_GLYPH, GLYPHS, HOLLOW_DOT, STAR_GLYPH } from "../theme.js"

interface ConversationMeasureOptions {
  readonly viewPrefs: TranscriptPreferences
  readonly verboseOutput: boolean
  readonly collapsedEntriesRef: React.MutableRefObject<Map<string, boolean>>
  readonly collapsedVersion: number
  /** Inner content width (excludes outer padding), in terminal columns. */
  readonly contentWidth: number
  readonly markdownRenderOptions?: Omit<MarkdownRenderOptions, "width">
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
  const { viewPrefs, verboseOutput, collapsedEntriesRef, collapsedVersion, contentWidth, markdownRenderOptions } = options

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
      const hasRichBlocks = Boolean(entry.richBlocks && entry.richBlocks.length > 0)
      const useRich = viewPrefs.richMarkdown && hasRichBlocks && !entry.markdownError
      const normalizedText = stripInlineThinkingMarker(entry.text)
      const fallback = viewPrefs.richMarkdown
        ? renderMarkdownFallbackLines(normalizedText, { ...markdownRenderOptions, width: markdownWidth })
        : normalizedText.split(/\r?\n/)
      const lines = (useRich
        ? blocksToLines(entry.richBlocks, { ...markdownRenderOptions, width: markdownWidth })
        : fallback) as string[]
      const plainLines = lines.map(stripAnsiCodes)

      const speakerGlyph =
        entry.speaker === "user"
          ? GLYPHS.chevron
          : entry.speaker === "assistant"
            ? GLYPHS.assistantDot
            : GLYPHS.systemDot
      const label = speakerGlyph
      const padLabel = "  "

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
        const instruction = "use [ / ] to target, then press e"
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
  } = options

  const markdownWidth = Math.max(1, Math.floor(contentWidth - 2))

  const renderConversationEntry = useCallback(
    (entry: ConversationEntry, key?: string) => {
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
            ? GLYPHS.assistantDot
            : GLYPHS.systemDot
      const label = CHALK.hex(speakerColor(entry.speaker))(speakerGlyph)
      const padLabel = "  "
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
      const renderLine = (line: string, index: number) => (
        <Text key={`${entry.id}-ln-${index}`}>
          {index === 0 ? `${label} ` : padLabel}
          {colorizeContent(line)}
        </Text>
      )
      const fallback = viewPrefs.richMarkdown
        ? renderMarkdownFallbackLines(normalizedText, { ...markdownRenderOptions, width: markdownWidth })
        : normalizedText.split(/\r?\n/)
      const lines = (useRich
        ? blocksToLines(entry.richBlocks, { ...markdownRenderOptions, width: markdownWidth })
        : fallback) as string[]
      const plainLines = useRich ? lines.map(stripAnsiCodes) : lines.map(stripAnsiCodes)
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
      const selectionGlyph = isSelected ? CHALK.hex(COLORS.info)(GLYPHS.select) : CHALK.dim(GLYPHS.bullet)
      const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
      const summaryParts = [
        `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
        meta ? `block ${meta.index + 1}/${meta.total}` : null,
        diffPreview ? `${DELTA_GLYPH} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
      ].filter(Boolean)
      const summary = summaryParts.join(` ${GLYPHS.bullet} `)
      const instruction = isSelected ? "press e to expand/collapse" : "use [ / ] to target, then press e"
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
      viewPrefs.richMarkdown,
    ],
  )

  return { renderConversationEntry }
}
