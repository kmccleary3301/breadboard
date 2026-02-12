import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { StreamStats } from "../types.js"
import { BRAND_COLORS, SEMANTIC_COLORS, resolveAsciiOnly, resolveIcons, resolveColorMode } from "../designSystem.js"

const ASCII_ONLY = resolveAsciiOnly()
const ICONS = resolveIcons(ASCII_ONLY)
const COLOR_MODE = resolveColorMode()
if (COLOR_MODE === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}
const HOLLOW_DOT = ASCII_ONLY ? "o" : "○"
const coloredDot = (active: boolean, color: string) => chalk.hex(color)(active ? ICONS.bullet : HOLLOW_DOT)

interface StatusBarProps {
  readonly status: string
  readonly stats: StreamStats
  readonly hashline: string
  readonly spinner: string
  readonly pending: boolean
}

export const StatusBar: React.FC<StatusBarProps> = ({ status, stats, hashline, spinner, pending }) => {
  const statusDot = status.toLowerCase().startsWith("completed")
  const statusIndicator = pending ? spinner : coloredDot(statusDot, BRAND_COLORS.duneOrange)
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        {statusIndicator} {status}  {coloredDot(true, BRAND_COLORS.duneOrange)} model {chalk.bold(stats.model)}{" "}
        {coloredDot(stats.remote, SEMANTIC_COLORS.info)} {stats.remote ? "remote" : "local"}{" "}
        {coloredDot(stats.eventCount > 0, SEMANTIC_COLORS.info)} events {stats.eventCount}{" "}
        {coloredDot(stats.toolCount > 0, SEMANTIC_COLORS.tool)} tools {stats.toolCount}{" "}
        {coloredDot(stats.lastTurn != null, SEMANTIC_COLORS.success)} turn {stats.lastTurn ?? "-"}
      </Text>
      <Text>{hashline}</Text>
    </Box>
  )
}
