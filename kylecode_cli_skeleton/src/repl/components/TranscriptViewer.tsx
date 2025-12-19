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
  readonly matchCount?: number
  readonly activeMatchIndex?: number
  readonly activeMatchLine?: number | null
}

const horizontalRule = (width: number): string => "─".repeat(Math.max(1, width))

const findFuzzyPositions = (value: string, query: string): number[] | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  const haystack = value.toLowerCase()
  const positions: number[] = []
  let lastIndex = -1
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    if (!ch) continue
    const index = haystack.indexOf(ch, lastIndex + 1)
    if (index === -1) return null
    positions.push(index)
    lastIndex = index
  }
  return positions
}

const highlightFuzzy = (value: string, query: string): string => {
  const positions = findFuzzyPositions(value, query)
  if (!positions || positions.length === 0) return value
  const set = new Set<number>(positions)
  let out = ""
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i] ?? ""
    out += set.has(i) ? chalk.bgHex("#1e293b").hex("#7CF2FF")(ch) : ch
  }
  return out
}

export const TranscriptViewer: React.FC<TranscriptViewerProps> = ({
  lines,
  cols,
  rows,
  scroll,
  searchQuery,
  matchCount,
  activeMatchIndex,
  activeMatchLine,
}) => {
  const width = cols && Number.isFinite(cols) ? cols : DEFAULT_COLS
  const height = rows && Number.isFinite(rows) ? rows : DEFAULT_ROWS
  const chromeRows = 4
  const bodyRows = Math.max(1, height - chromeRows)
  const maxScroll = Math.max(0, lines.length - bodyRows)
  const start = Math.max(0, Math.min(scroll, maxScroll))
  const end = Math.min(lines.length, start + bodyRows)
  const visible = lines.slice(start, end)
  const padded = visible.length < bodyRows ? [...visible, ...Array(bodyRows - visible.length).fill("")] : visible

  return (
    <Box flexDirection="column" width={width} height={height}>
      <Text wrap="truncate-end">{chalk.cyan("kylecode")} {chalk.dim("transcript viewer")}</Text>
      <Text color="dim" wrap="truncate-end">
        {searchQuery && searchQuery.trim().length > 0
          ? `Search: ${searchQuery}  ${typeof matchCount === "number" ? `• ${matchCount} match${matchCount === 1 ? "" : "es"}` : ""}${
              typeof activeMatchIndex === "number" && typeof matchCount === "number" && matchCount > 0
                ? ` • ${activeMatchIndex + 1}/${matchCount}`
                : ""
            }`
          : "Press / to search"}
        {"  "}
        {chalk.dim("Esc back • ↑/↓ scroll • PgUp/PgDn page • Ctrl+T toggle")}
      </Text>
      <Text color="dim" wrap="truncate">
        {horizontalRule(width)}
      </Text>
      <Box flexDirection="column" height={bodyRows}>
        {padded.map((line, index) => {
          const absoluteLine = start + index
          const isActive = activeMatchLine != null && absoluteLine === activeMatchLine
          const rendered =
            searchQuery && searchQuery.trim().length > 0 ? highlightFuzzy(line, searchQuery) : line
          return (
            <Text
              key={`tx-${start}-${index}`}
              wrap="truncate-end"
              backgroundColor={isActive ? "#334155" : undefined}
              color={isActive ? "#e2e8f0" : undefined}
            >
              {isActive ? chalk.hex("#7CF2FF")("› ") : "  "}
              {rendered}
            </Text>
          )
        })}
      </Box>
      <Text color="dim" wrap="truncate-end">
        {lines.length === 0
          ? "No transcript entries."
          : `Lines ${start + 1}-${end} of ${lines.length}${maxScroll > 0 ? ` • scroll ${start}/${maxScroll}` : ""}`}
      </Text>
    </Box>
  )
}
