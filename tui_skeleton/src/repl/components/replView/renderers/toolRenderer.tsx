import React, { useCallback } from "react"
import { Box, Text } from "ink"
import type { ToolLogEntry } from "../../../types.js"
import { TOOL_EVENT_COLOR } from "../../../viewUtils.js"
import { CHALK, COLORS, DASH_SEPARATOR, DELTA_GLYPH, GLYPHS } from "../theme.js"
import { computeDiffPreview } from "../../../transcriptUtils.js"

interface ToolRendererOptions {
  readonly claudeChrome: boolean
  readonly verboseOutput: boolean
  readonly collapseThreshold: number
  readonly collapseHead: number
  readonly collapseTail: number
  readonly labelWidth: number
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

export const useToolRenderer = (options: ToolRendererOptions) => {
  const { claudeChrome, verboseOutput, collapseThreshold, collapseHead, collapseTail, labelWidth } = options

  const measureToolEntryLines = useCallback(
    (entry: ToolLogEntry): number => {
      const lines = entry.text.split(/\r?\n/)
      if (lines.length <= collapseThreshold) return lines.length
      const head = lines.slice(0, collapseHead)
      const tail = lines.slice(-collapseTail)
      return head.length + 1 + tail.length
    },
    [collapseHead, collapseTail, collapseThreshold],
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
        const summary = `${GLYPHS.ellipsis} ${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {head.map((line, idx) => renderStatusLine(line, idx))}
            <Text color="gray">{`${" ".repeat(2)}${summary}`}</Text>
            {tail.map((line, idx) => renderStatusLine(line, head.length + idx + 1))}
          </Box>
        )
      }
      const label = `[${entry.kind}]`.padEnd(labelWidth, " ")
      const labelPad = " ".repeat(labelWidth)
      const kindToken = `[${entry.kind}]`
      const isError = entry.status === "error" || entry.kind === "error"
      const glyph =
        entry.status === "success"
          ? CHALK.hex(COLORS.success)(GLYPHS.bullet)
          : entry.status === "error"
            ? CHALK.hex(COLORS.error)(GLYPHS.bullet)
            : entry.kind === "call"
              ? CHALK.hex(COLORS.info)(GLYPHS.bullet)
              : CHALK.hex(TOOL_EVENT_COLOR)(GLYPHS.bullet)
      const rawText = entry.text.startsWith(kindToken)
        ? entry.text.slice(kindToken.length).trimStart()
        : entry.text
      const lines: string[] = rawText.split(/\r?\n/)
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
      const renderLine = (line: string, index: number) => (
        <Text key={`${entry.id}-ln-${index}`}>
          {index === 0
            ? `${glyph} ${CHALK.dim(label)} ${colorizeToolLine(line)}`
            : `${" ".repeat(2)}${CHALK.dim(labelPad)} ${colorizeToolLine(line)}`}
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
                  {index === 0 ? `  ${GLYPHS.treeBranch} ${colorizeClaudeLine(line)}` : `    ${colorizeClaudeLine(line)}`}
                </Text>
              ))}
            </Box>
          )
        }
      }
      if (verboseOutput || lines.length <= collapseThreshold) {
        return (
          <Box key={key ?? entry.id} flexDirection="column">
            {lines.map((line, idx) => renderLine(line, idx))}
          </Box>
        )
      }
      const head = lines.slice(0, collapseHead)
      const tail = lines.slice(-collapseTail)
      const hiddenCount = lines.length - head.length - tail.length
      const diffPreview = computeDiffPreview(lines)
      const filesPart = diffPreview && diffPreview.files.length > 0 ? ` in ${diffPreview.files.join(", ")}` : ""
      const summaryParts = [
        `${hiddenCount} ${hiddenCount === 1 ? "line" : "lines"} hidden`,
        diffPreview ? `${DELTA_GLYPH} +${diffPreview.additions}/-${diffPreview.deletions}${filesPart}` : null,
      ].filter(Boolean)
      return (
        <Box key={key ?? entry.id} flexDirection="column">
          {head.map((line, idx) => renderLine(line, idx))}
          <Text color="gray">
            {`${" ".repeat(2)}${CHALK.dim(labelPad)} ${GLYPHS.ellipsis} ${summaryParts.join(DASH_SEPARATOR)}`}
          </Text>
          {tail.map((line, idx) => renderLine(line, head.length + idx + 1))}
        </Box>
      )
    },
    [
      claudeChrome,
      collapseHead,
      collapseTail,
      collapseThreshold,
      labelWidth,
      verboseOutput,
    ],
  )

  return { renderToolEntry, measureToolEntryLines }
}
