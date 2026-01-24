import React, { useCallback } from "react"
import { Box, Text } from "ink"
import type { ConversationEntry, TranscriptPreferences } from "../../../types.js"
import { speakerColor } from "../../../viewUtils.js"
import {
  computeDiffPreview,
  ENTRY_COLLAPSE_HEAD,
  ENTRY_COLLAPSE_TAIL,
  shouldAutoCollapseEntry,
} from "../../../transcriptUtils.js"
import { blocksToLines } from "./markdown/streamMdxAdapter.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import { CHALK, COLORS, DASH_SEPARATOR, DELTA_GLYPH, GLYPHS } from "../theme.js"

interface ConversationMeasureOptions {
  readonly viewPrefs: TranscriptPreferences
  readonly verboseOutput: boolean
  readonly collapsedEntriesRef: React.MutableRefObject<Map<string, boolean>>
  readonly collapsedVersion: number
}

interface ConversationRenderOptions {
  readonly viewPrefs: TranscriptPreferences
  readonly collapsedEntriesRef: React.MutableRefObject<Map<string, boolean>>
  readonly collapsedVersion: number
  readonly collapsibleMeta: Map<string, { index: number; total: number }>
  readonly selectedCollapsibleEntryId: string | null
  readonly labelWidth: number
  readonly isEntryCollapsible: (entry: ConversationEntry) => boolean
}

export const useConversationMeasure = (options: ConversationMeasureOptions) => {
  const { viewPrefs, verboseOutput, collapsedEntriesRef, collapsedVersion } = options

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
      const lines = (useRich ? blocksToLines(entry.richBlocks) : entry.text.split(/\r?\n/)) as string[]
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
    isEntryCollapsible,
  } = options

  const renderConversationEntry = useCallback(
    (entry: ConversationEntry, key?: string) => {
      const useRich =
        viewPrefs.richMarkdown && entry.richBlocks && entry.richBlocks.length > 0 && !entry.markdownError
      const label = CHALK.hex(speakerColor(entry.speaker))(entry.speaker.toUpperCase().padEnd(labelWidth, " "))
      const padLabel = CHALK.dim(" ".repeat(labelWidth))
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
          {index === 0 ? label : padLabel} {colorizeContent(line)}
        </Text>
      )
      const lines = (useRich ? blocksToLines(entry.richBlocks) : entry.text.split(/\r?\n/)) as string[]
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
      selectedCollapsibleEntryId,
      viewPrefs.richMarkdown,
    ],
  )

  return { renderConversationEntry }
}
