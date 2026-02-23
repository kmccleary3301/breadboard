import chalk from "chalk"
import {
  BRAND_COLORS,
  NEUTRAL_COLORS,
  SEMANTIC_COLORS,
  resolveAsciiOnly,
  resolveColorMode,
  resolveIcons,
} from "../../designSystem.js"

export const CLI_VERSION = (process.env.BREADBOARD_TUI_VERSION ?? "0.2.0").trim()

export const COLORS = {
  accent: BRAND_COLORS.duneOrange,
  info: SEMANTIC_COLORS.info,
  success: SEMANTIC_COLORS.success,
  error: SEMANTIC_COLORS.error,
  warning: SEMANTIC_COLORS.warning,
  muted: NEUTRAL_COLORS.midGray,
  dim: NEUTRAL_COLORS.dimGray,
  text: NEUTRAL_COLORS.offWhite,
  textBright: NEUTRAL_COLORS.nearWhite,
  textMuted: NEUTRAL_COLORS.dimGray,
  textSoft: NEUTRAL_COLORS.midGray,
  footerHint: NEUTRAL_COLORS.lightGray,
  footerMeta: NEUTRAL_COLORS.midGray,
  panel: NEUTRAL_COLORS.darkGray,
  selectionBg: BRAND_COLORS.duneOrange,
  selectionFg: NEUTRAL_COLORS.nearBlack,
} as const

export const COLOR_MODE = resolveColorMode()
export const ASCII_ONLY = resolveAsciiOnly()
export const ICONS = resolveIcons(ASCII_ONLY)

if (COLOR_MODE === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}

export const CHALK = chalk

export const GLYPHS = {
  bullet: ICONS.bullet,
  chevron: ICONS.userChevron,
  assistantDot: ICONS.assistantDot,
  systemDot: ICONS.systemDot,
  select: ASCII_ONLY ? ">" : "▶",
  treeBranch: ICONS.treeBranch,
  treeLine: ICONS.verticalLine,
  ellipsis: ICONS.ellipsis,
} as const

export const STAR_GLYPH = ASCII_ONLY ? "*" : "★"
export const HOLLOW_DOT = ASCII_ONLY ? "o" : "○"
export const DASH_GLYPH = ASCII_ONLY ? "-" : "—"
export const DASH_SEPARATOR = ` ${DASH_GLYPH} `
export const DELTA_GLYPH = ASCII_ONLY ? "d" : "Δ"
export const DOT_SEPARATOR = ASCII_ONLY ? " . " : " · "
export const COLUMN_SEPARATOR = `  ${ASCII_ONLY ? "." : "·"}  `

export const uiText = (value: string): string => {
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
