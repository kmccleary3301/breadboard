import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import { NEUTRAL_COLORS, SEMANTIC_COLORS, resolveAsciiOnly, resolveColorMode, resolveIcons } from "../designSystem.js"

const DEFAULT_COLS = 80
const DEFAULT_ROWS = 40
const ASCII_ONLY = resolveAsciiOnly()
const ICONS = resolveIcons(ASCII_ONLY)
const COLOR_MODE = resolveColorMode()
if (COLOR_MODE === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}
const CHALK = chalk
const GLYPHS = {
  bullet: ICONS.bullet,
  chevron: ICONS.userChevron,
  hline: ASCII_ONLY ? "-" : "─",
} as const
const STAR_GLYPH = ASCII_ONLY ? "*" : "★"
const HOLLOW_DOT = ASCII_ONLY ? "o" : "○"
const DASH_GLYPH = ASCII_ONLY ? "-" : "—"
const uiText = (value: string): string => {
  if (!ASCII_ONLY) return value
  return value
    .replaceAll("•", GLYPHS.bullet)
    .replaceAll("·", ".")
    .replaceAll("●", GLYPHS.bullet)
    .replaceAll("○", HOLLOW_DOT)
    .replaceAll("★", STAR_GLYPH)
    .replaceAll("—", DASH_GLYPH)
    .replaceAll("–", "-")
    .replaceAll("←", "<-")
    .replaceAll("→", "->")
    .replaceAll("↑", "^")
    .replaceAll("↓", "v")
    .replaceAll("×", "x")
    .replaceAll("⏎", "Enter")
    .replaceAll("…", "...")
}

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

const horizontalRule = (width: number): string => GLYPHS.hline.repeat(Math.max(1, width))

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
    out += CHALK.bgHex(NEUTRAL_COLORS.darkGray).hex(SEMANTIC_COLORS.info)(value.slice(found, found + needle.length))
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
            backgroundColor={isActive ? NEUTRAL_COLORS.darkGray : undefined}
            color={isActive ? NEUTRAL_COLORS.nearWhite : undefined}
          >
            {variant === "claude"
              ? ""
              : isActive
                ? CHALK.hex(SEMANTIC_COLORS.info)(`${GLYPHS.chevron} `)
                : isMatch
                  ? CHALK.dim(`${GLYPHS.bullet} `)
                  : "  "}
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
    const searchLine = hasSearch ? uiText(`Search: ${trimmedQuery}${matchSummary}${indexSummary}`) : ""
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
      <Text wrap="truncate-end">{CHALK.hex(SEMANTIC_COLORS.info)("breadboard")} {CHALK.dim("transcript viewer")}</Text>
      {detailLabel ? (
        <Text color="dim" wrap="truncate-end">
          {detailLabel}
        </Text>
      ) : null}
      <Text color="dim" wrap="truncate-end">
        {searchQuery && searchQuery.trim().length > 0
          ? uiText(
              `Search: ${searchQuery}  ${typeof matchCount === "number" ? `• ${matchCount} match${matchCount === 1 ? "" : "es"}` : ""}${
                typeof activeMatchIndex === "number" && typeof matchCount === "number" && matchCount > 0
                  ? ` • ${activeMatchIndex + 1}/${matchCount}`
                  : ""
              }`,
            )
          : "Press / to search"}
        {"  "}
        {CHALK.dim(
          uiText(
            `Esc back • ↑/↓ scroll • PgUp/PgDn page${searchQuery && searchQuery.trim().length > 0 ? " • n/p match" : ""} • t tools • s save • ${
              toggleHint ?? "Ctrl+T toggle"
            }`,
          ),
        )}
      </Text>
      <Text color="dim" wrap="truncate">
        {horizontalRule(width)}
      </Text>
      {body}
      <Text color="dim" wrap="truncate-end">
        {lines.length === 0
          ? "No transcript entries."
          : uiText(
              `Lines ${start + 1}-${end} of ${lines.length}${maxScroll > 0 ? ` • scroll ${start}/${maxScroll}` : ""}`,
            )}
      </Text>
    </Box>
  )
}
