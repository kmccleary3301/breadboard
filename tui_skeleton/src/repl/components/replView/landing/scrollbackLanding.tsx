import React from "react"
import { Box, Text } from "ink"
import { applyForegroundGradient, Gradients } from "../../../../colors/gradients.js"
import { stripAnsiCodes, stringWidth, visibleWidth } from "../utils/ansi.js"
import { ASCII_HEADER } from "../../../viewUtils.js"
import { BRAND_COLORS } from "../../../designSystem.js"
import { CHALK, COLORS, DOT_SEPARATOR, GLYPHS, CLI_VERSION, ASCII_ONLY } from "../theme.js"

export type LandingVariant = "hero" | "board" | "split" | "compact" | "micro" | "suppressed"

type LandingContext = {
  contentWidth: number
  modelLabel: string
  chromeLabel: string
  configLabel: string
  cwd: string
  sessionLabel?: string | null
  statusLabel?: string | null
  variant: LandingVariant
  borderStyle: "round" | "single"
  showAsciiArt: boolean
}

const resolveBoxChars = (style: LandingContext["borderStyle"]) => {
  if (ASCII_ONLY) {
    return {
      tl: "+",
      tr: "+",
      bl: "+",
      br: "+",
      h: "-",
      v: "|",
    }
  }
  if (style === "single") {
    return {
      tl: "┌",
      tr: "┐",
      bl: "└",
      br: "┘",
      h: "─",
      v: "│",
    }
  }
  return {
    tl: "╭",
    tr: "╮",
    bl: "╰",
    br: "╯",
    h: "─",
    v: "│",
  }
}

// Landing uses a more "Claude-like" chrome: warm border accents + muted secondary text.
const frameColor = CHALK.hex(BRAND_COLORS.strawberryRed)
const dividerColor = CHALK.hex(COLORS.dim)

const visibleLength = (value: string) => visibleWidth(value)

const truncatePlain = (value: string, width: number): string => {
  if (width <= 0) return ""
  if (stringWidth(value) <= width) return value
  if (width === 1) return GLYPHS.ellipsis
  const target = Math.max(0, width - 1)
  let out = ""
  let used = 0
  for (const char of value) {
    const next = used + stringWidth(char)
    if (next > target) break
    out += char
    used = next
  }
  return `${out}${GLYPHS.ellipsis}`
}

const padAnsi = (value: string, width: number, align: "left" | "right" | "center" = "left") => {
  const len = visibleLength(value)
  if (len >= width) return value
  const pad = width - len
  if (align === "right") return `${" ".repeat(pad)}${value}`
  if (align === "center") {
    const left = Math.floor(pad / 2)
    const right = pad - left
    return `${" ".repeat(left)}${value}${" ".repeat(right)}`
  }
  return `${value}${" ".repeat(pad)}`
}

const buildTitleLine = (width: number, box: ReturnType<typeof resolveBoxChars>, leftLabel: string, rightLabel?: string) => {
  const inner = width - 2
  const leftPlain = stripAnsiCodes(leftLabel).trim()
  const rightPlain = rightLabel ? stripAnsiCodes(rightLabel).trim() : ""
  const leftText = `${box.h.repeat(2)} ${leftPlain} `
  const rightText = rightPlain ? ` ${rightPlain} ${box.h.repeat(2)}` : ""
  if (stringWidth(leftText) + stringWidth(rightText) > inner) {
    const compact = truncatePlain(rightPlain ? `${leftPlain}${DOT_SEPARATOR}${rightPlain}` : leftPlain, inner)
    return `${frameColor(box.tl)}${frameColor(padAnsi(compact, inner))}${frameColor(box.tr)}`
  }
  const gap = Math.max(0, inner - stringWidth(leftText) - stringWidth(rightText))
  const left = `${frameColor(box.h.repeat(2))} ${leftLabel.trim()} `
  const right = rightPlain ? ` ${rightLabel?.trim()} ${frameColor(box.h.repeat(2))}` : ""
  const line = `${left}${frameColor(box.h.repeat(gap))}${right}`
  return `${frameColor(box.tl)}${line}${frameColor(box.tr)}`
}

const buildFooterLine = (width: number, box: ReturnType<typeof resolveBoxChars>) =>
  `${frameColor(box.bl)}${frameColor(box.h.repeat(Math.max(0, width - 2)))}${frameColor(box.br)}`

const formatName = () => {
  const raw =
    process.env.BREADBOARD_USER_NAME ??
    process.env.BREADBOARD_TUI_USER_NAME ??
    process.env.USER ??
    process.env.USERNAME ??
    "there"
  const normalized = raw.replace(/[_-]+/g, " ").trim()
  if (!normalized) return "there"
  return normalized.replace(/\b\w/g, (ch) => ch.toUpperCase())
}

const formatModel = (value: string) => {
  const raw = value?.trim()
  if (!raw) return "model"
  const parts = raw.split("/").filter(Boolean)
  return parts[parts.length - 1] || raw
}

const formatConfig = (label: string) => {
  const version =
    process.env.BREADBOARD_TUI_CONFIG_VERSION ??
    process.env.BREADBOARD_CONFIG_VERSION ??
    ""
  if (!version) return label
  const normalized = version.startsWith("v") ? version : `v${version}`
  return `${label} ${normalized}`
}

const renderAsciiLines = (width: number) =>
  ASCII_HEADER.map((line: string) => {
  const clamped = truncatePlain(line, width)
  const colored = applyForegroundGradient(clamped, Gradients.breadboard, true)
  return padAnsi(colored, width, "center")
})

const landingSafeWidth = (contentWidth: number): number =>
  // Preserved-scrollback rows must never autowrap at the terminal edge. A line
  // that exactly fills the last column can move the cursor to the next row in
  // real terminals, shifting the active composer band and stranding stale
  // prompt rows after width shrink. Keep a real gutter rather than one
  // nominal column because terminals disagree on a few brand-glyph widths.
  Math.max(1, Math.floor(contentWidth) - 6)

const buildBoardLines = (context: LandingContext) => {
  const BOX = resolveBoxChars(context.borderStyle)
  const width = landingSafeWidth(context.contentWidth)
  const leftPad = 0
  const inner = width - 2
  const leftWidth = Math.floor((inner - 1) * 0.55)
  const rightWidth = inner - leftWidth - 1
  const leftInnerWidth = Math.max(1, leftWidth - 2)
  const rightInnerWidth = Math.max(1, rightWidth - 2)
  const hello = `Hello again ${formatName()}...`
  const modelLine = `${formatModel(context.modelLabel)}${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`
  const configLine = `Config: ${formatConfig(context.configLabel)}`
  const cwdLine = context.cwd

  const asciiLines = context.showAsciiArt ? renderAsciiLines(leftInnerWidth) : []
  const leftLines = [
    "",
    CHALK.hex(COLORS.textBright)(truncatePlain(hello, leftInnerWidth)),
    "",
    ...asciiLines,
    "",
    CHALK.hex(COLORS.info)
      .bold(truncatePlain(configLine, leftInnerWidth)),
    CHALK.hex(COLORS.textMuted)(truncatePlain(modelLine, leftInnerWidth)),
    CHALK.hex(COLORS.textMuted)(truncatePlain(cwdLine, leftInnerWidth)),
  ]

  const tipHeader = CHALK.hex(BRAND_COLORS.jamRed).bold(truncatePlain("Tips for getting started", rightInnerWidth))
  const tipLine = CHALK.hex(COLORS.text)(truncatePlain("Run /init to create an AGENTS.md", rightInnerWidth))
  const tipLine2 = CHALK.hex(COLORS.text)(truncatePlain("Use @ to attach files", rightInnerWidth))
  const tipLine3 = CHALK.hex(COLORS.textSoft)(
    truncatePlain("Try: \"Summarize this repo in 5 bullets.\"", rightInnerWidth),
  )
  const divider = dividerColor(BOX.h.repeat(rightInnerWidth))
  const recentHeader = CHALK.hex(BRAND_COLORS.jamRed).bold(truncatePlain("Recent activity", rightInnerWidth))
  const recentLine = CHALK.hex(COLORS.textSoft)(truncatePlain("No recent activity yet", rightInnerWidth))
  const recentLine2 = CHALK.hex(COLORS.textMuted)(truncatePlain("Run /replay <scenario> to populate history", rightInnerWidth))

  const rightLines = [
    tipHeader,
    tipLine,
    tipLine2,
    tipLine3,
    divider,
    recentHeader,
    recentLine,
    recentLine2,
  ]

  const totalRows = Math.max(leftLines.length, rightLines.length)
  const rows: string[] = []
  for (let i = 0; i < totalRows; i += 1) {
    const left = padAnsi(leftLines[i] ?? "", leftInnerWidth, "center")
    const right = padAnsi(rightLines[i] ?? "", rightInnerWidth, "left")
    const leftCell = ` ${left} `
    const rightCell = ` ${right} `
    const line = `${frameColor(BOX.v)}${leftCell}${frameColor(BOX.v)}${rightCell}${frameColor(BOX.v)}`
    rows.push(`${" ".repeat(leftPad)}${line}`)
  }

  const top = `${" ".repeat(leftPad)}${buildTitleLine(width, BOX, frameColor.bold(`BreadBoard v${CLI_VERSION}`), frameColor("By Kyle McCleary"))}`
  const bottom = `${" ".repeat(leftPad)}${buildFooterLine(width, BOX)}`
  return [top, ...rows, bottom]
}

const buildSplitLines = (context: LandingContext) => {
  const width = landingSafeWidth(context.contentWidth)
  const asciiWidth = Math.max(...ASCII_HEADER.map((line) => line.length))
  const leftWidth = Math.min(width - 20, asciiWidth + 2)
  const gap = 3
  const rightWidth = Math.max(10, width - leftWidth - gap)
  const title = frameColor.bold(`BreadBoard v${CLI_VERSION}`)
  const config = CHALK.hex(COLORS.info)
    .bold(truncatePlain(`Using Config \`${formatConfig(context.configLabel)}\``, rightWidth))
  const model = CHALK.hex(COLORS.textMuted)(truncatePlain(`${formatModel(context.modelLabel)}${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`, rightWidth))
  const cwd = CHALK.hex(COLORS.textMuted)(truncatePlain(context.cwd, rightWidth))
  const rightLines = [title, config, model, cwd]
  const leftLines = context.showAsciiArt ? renderAsciiLines(leftWidth) : []
  const total = Math.max(leftLines.length, rightLines.length)
  const lines: string[] = []
  for (let i = 0; i < total; i += 1) {
    const left = padAnsi(leftLines[i] ?? "", leftWidth, "left")
    const right = padAnsi(rightLines[i] ?? "", rightWidth, "left")
    lines.push(`${left}${" ".repeat(gap)}${right}`)
  }
  return lines
}

const buildMicroLines = (context: LandingContext) => {
  const width = landingSafeWidth(context.contentWidth)
  const identity = [
    `BreadBoard v${CLI_VERSION}`,
    formatModel(context.modelLabel),
    context.chromeLabel || formatConfig(context.configLabel),
    context.cwd,
  ].filter(Boolean).join(DOT_SEPARATOR)
  return [CHALK.hex(COLORS.textMuted)(truncatePlain(identity, width))]
}

const buildCompactLines = (context: LandingContext) => {
  const BOX = resolveBoxChars(context.borderStyle)
  const width = landingSafeWidth(context.contentWidth)
  const leftPad = 0
  const inner = width - 2
  const contentWidth = Math.max(1, inner - 2)
  const hello = `Hello again ${formatName()}...`
  const modelLine = `${formatModel(context.modelLabel)}${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`
  const configLine = `Config: ${formatConfig(context.configLabel)}`
  const cwdLine = context.cwd
  if (!context.showAsciiArt) {
    const identity = CHALK.hex(COLORS.textMuted)(
      truncatePlain(`${formatModel(context.modelLabel)}${DOT_SEPARATOR}${context.chromeLabel || formatConfig(context.configLabel)}${DOT_SEPARATOR}${cwdLine}`, contentWidth),
    )
    const slimLines = [identity].map((line: string) => padAnsi(line, contentWidth, "center"))
    const top = `${" ".repeat(leftPad)}${buildTitleLine(width, BOX, frameColor.bold(`BreadBoard v${CLI_VERSION}`), frameColor("By Kyle McCleary"))}`
    const bottom = `${" ".repeat(leftPad)}${buildFooterLine(width, BOX)}`
    const body = slimLines.map(
      (line) => `${" ".repeat(leftPad)}${frameColor(BOX.v)} ${line} ${frameColor(BOX.v)}`,
    )
    return [top, ...body, bottom]
  }
  const infoLines = [
    CHALK.hex(COLORS.info).bold(truncatePlain(configLine, contentWidth)),
    CHALK.hex(COLORS.textMuted)(truncatePlain(modelLine, contentWidth)),
  ]
  const asciiLines = context.showAsciiArt ? renderAsciiLines(contentWidth) : []
  const lines = [
    "",
    CHALK.hex(COLORS.textBright)(truncatePlain(hello, contentWidth)),
    "",
    ...asciiLines,
    "",
    ...infoLines,
    CHALK.hex(COLORS.textMuted)(truncatePlain(cwdLine, contentWidth)),
    "",
  ].map((line: string) => padAnsi(line, contentWidth, "center"))

  const top = `${" ".repeat(leftPad)}${buildTitleLine(width, BOX, frameColor.bold(`BreadBoard v${CLI_VERSION}`), frameColor("By Kyle McCleary"))}`
  const bottom = `${" ".repeat(leftPad)}${buildFooterLine(width, BOX)}`
  const body = lines.map(
    (line) => `${" ".repeat(leftPad)}${frameColor(BOX.v)} ${line} ${frameColor(BOX.v)}`,
  )
  return [top, ...body, bottom]
}

export const buildScrollbackLanding = (context: LandingContext): React.ReactNode => {
  const lines = buildScrollbackLandingLines(context)
  if (lines.length === 0) return null
  return (
    <Box flexDirection="column" marginBottom={0}>
      {lines.map((line, index) => (
        <Text key={`landing-${index}`} wrap="truncate-end">{line}</Text>
      ))}
    </Box>
  )
}

export const buildScrollbackSessionHeaderLines = (context: LandingContext): string[] => {
  const width = landingSafeWidth(context.contentWidth)
  const title = CHALK.hex(COLORS.info).bold(
    truncatePlain(`BreadBoard${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`, width),
  )
  const detailParts = [
    formatModel(context.modelLabel),
    context.cwd,
    context.statusLabel?.trim() || null,
    context.sessionLabel?.trim() || null,
  ].filter((value): value is string => Boolean(value && value.length > 0))
  const detail = CHALK.hex(COLORS.textMuted)(truncatePlain(detailParts.join(`  ${DOT_SEPARATOR}  `), width))
  return [title, detail]
}

export const buildScrollbackSessionHeader = (context: LandingContext): React.ReactNode => {
  const lines = buildScrollbackSessionHeaderLines(context)
  return (
    <Box flexDirection="column" marginBottom={0}>
      {lines.map((line, index) => (
        <Text key={`session-header-${index}`} wrap="truncate-end">{line}</Text>
      ))}
    </Box>
  )
}

export const buildScrollbackLandingLines = (context: LandingContext): string[] =>
  context.variant === "suppressed"
    ? []
    : context.variant === "micro"
      ? buildMicroLines(context)
      : context.variant === "split"
    ? buildSplitLines(context)
    : context.variant === "compact"
      ? buildCompactLines(context)
      : buildBoardLines(context)

export const getScrollbackLandingRowCount = (context: LandingContext): number =>
  buildScrollbackLandingLines(context).length

export const resolveLandingVariant = (contentWidth: number): LandingVariant => {
  if (contentWidth >= 92) return "hero"
  if (contentWidth >= 76) return "split"
  if (contentWidth >= 56) return "compact"
  return "micro"
}

export const resolveLandingVariantForViewport = (context: {
  contentWidth: number
  maxRows: number
  preferredVariant: "auto" | LandingVariant
  modelLabel: string
  chromeLabel: string
  configLabel: string
  cwd: string
  borderStyle: "round" | "single"
  showAsciiArt: boolean
}): LandingVariant => {
  const preferred = context.preferredVariant === "auto" ? resolveLandingVariant(context.contentWidth) : context.preferredVariant
  if (preferred === "suppressed") return "suppressed"
  const candidates: LandingVariant[] =
    preferred === "hero" || preferred === "board"
      ? ["hero", "split", "compact", "micro"]
      : preferred === "split"
        ? ["split", "compact", "micro"]
        : preferred === "compact"
          ? ["compact", "micro"]
          : ["micro"]
  for (const variant of candidates) {
    const rows = getScrollbackLandingRowCount({
      contentWidth: context.contentWidth,
      modelLabel: context.modelLabel,
      chromeLabel: context.chromeLabel,
      configLabel: context.configLabel,
      cwd: context.cwd,
      variant,
      borderStyle: context.borderStyle,
      showAsciiArt: context.showAsciiArt,
    })
    if (rows <= context.maxRows) return variant
  }
  return "suppressed"
}

export const buildLandingContext = (context: {
  contentWidth: number
  modelLabel: string
  chromeLabel: string
  configLabel: string
  cwd: string
  sessionLabel?: string | null
  statusLabel?: string | null
  variant: "auto" | LandingVariant
  borderStyle: "round" | "single"
  showAsciiArt: boolean
}): LandingContext => ({
  contentWidth: context.contentWidth,
  modelLabel: context.modelLabel,
  chromeLabel: context.chromeLabel,
  configLabel: context.configLabel,
  cwd: context.cwd,
  sessionLabel: context.sessionLabel ?? null,
  statusLabel: context.statusLabel ?? null,
  variant: context.variant === "auto" ? resolveLandingVariant(context.contentWidth) : context.variant,
  borderStyle: context.borderStyle,
  showAsciiArt: context.showAsciiArt,
})
