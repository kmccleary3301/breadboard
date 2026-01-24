import React from "react"
import { Box, Text } from "ink"
import { applyForegroundGradient, Gradients } from "../../../../colors/gradients.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import { truncateLine } from "../utils/format.js"
import { ASCII_HEADER } from "../../../viewUtils.js"
import { BRAND_COLORS } from "../../../designSystem.js"
import { CHALK, COLORS, DOT_SEPARATOR, ICONS, CLI_VERSION } from "../theme.js"

type LandingVariant = "board" | "split" | "compact"

type LandingContext = {
  contentWidth: number
  modelLabel: string
  chromeLabel: string
  configLabel: string
  cwd: string
  variant: LandingVariant
}

const BOX = {
  tl: "╭",
  tr: "╮",
  bl: "╰",
  br: "╯",
  h: "─",
  v: "│",
}

// Landing uses a more "Claude-like" chrome: warm border accents + muted secondary text.
const frameColor = CHALK.hex(BRAND_COLORS.strawberryRed)
const dividerColor = CHALK.hex(COLORS.dim)

const visibleLength = (value: string) => stripAnsiCodes(value).length

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

const buildTitleLine = (width: number, leftLabel: string, rightLabel?: string) => {
  const inner = width - 2
  const leftText = `${BOX.h.repeat(2)} ${stripAnsiCodes(leftLabel).trim()} `
  const rightText = rightLabel ? ` ${stripAnsiCodes(rightLabel).trim()} ${BOX.h.repeat(2)}` : ""
  const gap = Math.max(0, inner - leftText.length - rightText.length)
  const left = `${frameColor(BOX.h.repeat(2))} ${leftLabel.trim()} `
  const right = rightLabel ? ` ${rightLabel.trim()} ${frameColor(BOX.h.repeat(2))}` : ""
  const line = `${left}${frameColor(BOX.h.repeat(gap))}${right}`
  return `${frameColor(BOX.tl)}${line}${frameColor(BOX.tr)}`
}

const buildFooterLine = (width: number) =>
  `${frameColor(BOX.bl)}${frameColor(BOX.h.repeat(Math.max(0, width - 2)))}${frameColor(BOX.br)}`

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
    const colored = applyForegroundGradient(line, Gradients.breadboard, true)
    return padAnsi(colored, width, "center")
  })

const buildBoardLines = (context: LandingContext) => {
  const width = Math.max(1, context.contentWidth)
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

  const leftLines = [
    "",
    CHALK.hex(COLORS.textBright)(truncateLine(hello, leftInnerWidth)),
    "",
    ...renderAsciiLines(leftInnerWidth),
    "",
    CHALK.hex(COLORS.info)
      .bold(truncateLine(configLine, leftInnerWidth)),
    CHALK.hex(COLORS.textMuted)(truncateLine(modelLine, leftInnerWidth)),
    CHALK.hex(COLORS.textMuted)(truncateLine(cwdLine, leftInnerWidth)),
  ]

  const tipHeader = CHALK.hex(BRAND_COLORS.jamRed).bold(truncateLine("Tips for getting started", rightInnerWidth))
  const tipLine = CHALK.hex(COLORS.text)(truncateLine("Run /init to create an AGENTS.md", rightInnerWidth))
  const tipLine2 = CHALK.hex(COLORS.text)(truncateLine("Use @ to attach files", rightInnerWidth))
  const divider = dividerColor(BOX.h.repeat(rightInnerWidth))
  const recentHeader = CHALK.hex(BRAND_COLORS.jamRed).bold(truncateLine("Recent activity", rightInnerWidth))
  const recentLine = CHALK.hex(COLORS.textMuted)(truncateLine("No recent activity", rightInnerWidth))

  const rightLines = [
    tipHeader,
    tipLine,
    tipLine2,
    divider,
    recentHeader,
    recentLine,
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

  const top = `${" ".repeat(leftPad)}${buildTitleLine(width, frameColor.bold(`BreadBoard v${CLI_VERSION}`), frameColor("By Kyle McCleary"))}`
  const bottom = `${" ".repeat(leftPad)}${buildFooterLine(width)}`
  return [top, ...rows, bottom]
}

const buildSplitLines = (context: LandingContext) => {
  const width = context.contentWidth
  const asciiWidth = Math.max(...ASCII_HEADER.map((line) => line.length))
  const leftWidth = Math.min(width - 20, asciiWidth + 2)
  const gap = 3
  const rightWidth = Math.max(10, width - leftWidth - gap)
  const title = frameColor.bold(`BreadBoard v${CLI_VERSION}`)
  const config = CHALK.hex(COLORS.info)
    .bold(truncateLine(`Using Config \`${formatConfig(context.configLabel)}\``, rightWidth))
  const model = CHALK.hex(COLORS.textMuted)(truncateLine(`${formatModel(context.modelLabel)}${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`, rightWidth))
  const cwd = CHALK.hex(COLORS.textMuted)(truncateLine(context.cwd, rightWidth))
  const rightLines = [title, config, model, cwd]
  const leftLines = renderAsciiLines(leftWidth)
  const total = Math.max(leftLines.length, rightLines.length)
  const lines: string[] = []
  for (let i = 0; i < total; i += 1) {
    const left = padAnsi(leftLines[i] ?? "", leftWidth, "left")
    const right = padAnsi(rightLines[i] ?? "", rightWidth, "left")
    lines.push(`${left}${" ".repeat(gap)}${right}`)
  }
  return lines
}

const buildCompactLines = (context: LandingContext) => {
  const width = Math.max(1, context.contentWidth)
  const leftPad = 0
  const inner = width - 2
  const contentWidth = Math.max(1, inner - 2)
  const hello = `Hello again ${formatName()}...`
  const modelLine = `${formatModel(context.modelLabel)}${context.chromeLabel ? `${DOT_SEPARATOR}${context.chromeLabel}` : ""}`
  const configLine = `Config: ${formatConfig(context.configLabel)}`
  const cwdLine = context.cwd
  const infoLines = [
    CHALK.hex(COLORS.info).bold(truncateLine(configLine, contentWidth)),
    CHALK.hex(COLORS.textMuted)(truncateLine(modelLine, contentWidth)),
  ]
  const lines = [
    "",
    CHALK.hex(COLORS.textBright)(truncateLine(hello, contentWidth)),
    "",
    ...renderAsciiLines(contentWidth),
    "",
    ...infoLines,
    CHALK.hex(COLORS.textMuted)(truncateLine(cwdLine, contentWidth)),
    "",
  ].map((line: string) => padAnsi(line, contentWidth, "center"))

  const top = `${" ".repeat(leftPad)}${buildTitleLine(width, frameColor.bold(`BreadBoard v${CLI_VERSION}`), frameColor("By Kyle McCleary"))}`
  const bottom = `${" ".repeat(leftPad)}${buildFooterLine(width)}`
  const body = lines.map(
    (line) => `${" ".repeat(leftPad)}${frameColor(BOX.v)} ${line} ${frameColor(BOX.v)}`,
  )
  return [top, ...body, bottom]
}

export const buildScrollbackLanding = (context: LandingContext): React.ReactNode => {
  const lines =
    context.variant === "split" ? buildSplitLines(context) : context.variant === "compact" ? buildCompactLines(context) : buildBoardLines(context)
  return (
    <Box flexDirection="column" marginBottom={0}>
      {lines.map((line, index) => (
        <Text key={`landing-${index}`}>{line}</Text>
      ))}
    </Box>
  )
}

export const resolveLandingVariant = (contentWidth: number): LandingVariant => {
  const override = process.env.BREADBOARD_TUI_LANDING_VARIANT?.trim().toLowerCase()
  if (override === "board" || override === "split" || override === "compact") return override
  if (contentWidth >= 92) return "board"
  if (contentWidth >= 76) return "split"
  return "compact"
}

export const buildLandingContext = (contentWidth: number, modelLabel: string, chromeLabel: string, configLabel: string, cwd: string): LandingContext => ({
  contentWidth,
  modelLabel,
  chromeLabel,
  configLabel,
  cwd,
  variant: resolveLandingVariant(contentWidth),
})

