import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"

const DEFAULT_COLS = 80
const DEFAULT_ROWS = 40

export interface TranscriptViewerProps {
  readonly lines: ReadonlyArray<string>
  readonly cols?: number
  readonly rows?: number
  readonly scroll: number
  readonly searchQuery?: string
  readonly matchLines?: ReadonlyArray<number>
  readonly matchCount?: number
  readonly activeMatchIndex?: number
  readonly activeMatchLine?: number | null
  readonly toggleHint?: string
  readonly detailLabel?: string
  readonly variant?: "default" | "claude"
}

const horizontalRule = (width: number): string => "─".repeat(Math.max(1, width))

const highlightSubstring = (value: string, query: string): string => {
  const needle = query.trim()
  if (!needle) return value
  const lowerValue = value.toLowerCase()
  const lowerNeedle = needle.toLowerCase()
  let out = ""
  let index = 0
  while (index < value.length) {
    const found = lowerValue.indexOf(lowerNeedle, index)
    if (found === -1) {
      out += value.slice(index)
      break
    }
    out += value.slice(index, found)
    out += chalk.bgHex("#1e293b").hex("#7CF2FF")(value.slice(found, found + needle.length))
    index = found + needle.length
  }
  return out
}

export const TranscriptViewer: React.FC<TranscriptViewerProps> = ({
  lines,
  cols,
  rows,
  scroll,
  searchQuery,
  matchLines,
  matchCount,
  activeMatchIndex,
  activeMatchLine,
  toggleHint,
  detailLabel,
  variant = "default",
}) => {
  const width = cols && Number.isFinite(cols) ? cols : DEFAULT_COLS
  const height = rows && Number.isFinite(rows) ? rows : DEFAULT_ROWS
  const trimmedQuery = (searchQuery ?? "").trim()
  const hasSearch = trimmedQuery.length > 0
  const chromeRows =
    variant === "claude"
      ? (detailLabel ? 2 : 1) + (hasSearch ? 1 : 0)
      : detailLabel
        ? 5
        : 4
  const bodyRows = Math.max(1, height - chromeRows)
  const maxScroll = Math.max(0, lines.length - bodyRows)
  const start = Math.max(0, Math.min(scroll, maxScroll))
  const end = Math.min(lines.length, start + bodyRows)
  const visible = lines.slice(start, end)
  const padded = visible.length < bodyRows ? [...visible, ...Array(bodyRows - visible.length).fill("")] : visible
  const matchLineSet = new Set(matchLines ?? [])

  const body = (
    <Box flexDirection="column" height={bodyRows}>
      {padded.map((line, index) => {
        const absoluteLine = start + index
        const isActive = activeMatchLine != null && absoluteLine === activeMatchLine
        const rendered =
          searchQuery && searchQuery.trim().length > 0 ? highlightSubstring(line, searchQuery) : line
        const isMatch = matchLineSet.has(absoluteLine)
        return (
          <Text
            key={`tx-${start}-${index}`}
            wrap="truncate-end"
            backgroundColor={isActive ? "#334155" : undefined}
            color={isActive ? "#e2e8f0" : undefined}
          >
            {variant === "claude" ? "" : isActive ? chalk.hex("#7CF2FF")("› ") : isMatch ? chalk.dim("• ") : "  "}
            {rendered}
          </Text>
        )
      })}
    </Box>
  )

  if (variant === "claude") {
    const matchSummary =
      typeof matchCount === "number"
        ? ` • ${matchCount} match${matchCount === 1 ? "" : "es"}`
        : ""
    const indexSummary =
      typeof activeMatchIndex === "number" && typeof matchCount === "number" && matchCount > 0
        ? ` • ${activeMatchIndex + 1}/${matchCount}`
        : ""
    const searchLine = hasSearch ? `Search: ${trimmedQuery}${matchSummary}${indexSummary}` : ""
    return (
      <Box flexDirection="column" width={width} height={height}>
        {body}
        {hasSearch ? (
          <Text color="dim" wrap="truncate-end">
            {searchLine}
          </Text>
        ) : null}
        {detailLabel ? (
          <>
            <Text color="dim" wrap="truncate">
              {horizontalRule(width)}
            </Text>
            <Text color="dim" wrap="truncate-end">
              {detailLabel}
            </Text>
          </>
        ) : null}
      </Box>
    )
  }

  return (
    <Box flexDirection="column" width={width} height={height}>
      <Text wrap="truncate-end">{chalk.cyan("breadboard")} {chalk.dim("transcript viewer")}</Text>
      {detailLabel ? (
        <Text color="dim" wrap="truncate-end">
          {detailLabel}
        </Text>
      ) : null}
      <Text color="dim" wrap="truncate-end">
        {searchQuery && searchQuery.trim().length > 0
          ? `Search: ${searchQuery}  ${typeof matchCount === "number" ? `• ${matchCount} match${matchCount === 1 ? "" : "es"}` : ""}${
              typeof activeMatchIndex === "number" && typeof matchCount === "number" && matchCount > 0
                ? ` • ${activeMatchIndex + 1}/${matchCount}`
                : ""
            }`
          : "Press / to search"}
        {"  "}
        {chalk.dim(
          `Esc back • ↑/↓ scroll • PgUp/PgDn page${searchQuery && searchQuery.trim().length > 0 ? " • n/p match" : ""} • t tools • s save • ${
            toggleHint ?? "Ctrl+T toggle"
          }`,
        )}
      </Text>
      <Text color="dim" wrap="truncate">
        {horizontalRule(width)}
      </Text>
      {body}
      <Text color="dim" wrap="truncate-end">
        {lines.length === 0
          ? "No transcript entries."
          : `Lines ${start + 1}-${end} of ${lines.length}${maxScroll > 0 ? ` • scroll ${start}/${maxScroll}` : ""}`}
      </Text>
    </Box>
  )
}
