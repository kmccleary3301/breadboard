import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { StreamStats } from "../../repl/types.js"
import { useHashTicker } from "../../repl/hooks/useHashTicker.js"
import { BRAND_COLORS, SEMANTIC_COLORS, resolveAsciiOnly, resolveColorMode, resolveIcons } from "../../repl/designSystem.js"

const ASCII_ONLY = resolveAsciiOnly()
const ICONS = resolveIcons(ASCII_ONLY)
const COLOR_MODE = resolveColorMode()
if (COLOR_MODE === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}
const HOLLOW_DOT = ASCII_ONLY ? "o" : "○"
const dot = (active: boolean, color: string) => chalk.hex(color)(active ? ICONS.bullet : HOLLOW_DOT)

interface ClassicStatusBarProps {
  readonly status: string
  readonly stats: StreamStats
}

export const ClassicStatusBar: React.FC<ClassicStatusBarProps> = ({ status, stats }) => {
  const hashline = useHashTicker()
  const completed = status.toLowerCase().startsWith("completed")
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        {dot(completed, SEMANTIC_COLORS.info)} {status}  {dot(true, BRAND_COLORS.duneOrange)} model {chalk.bold(stats.model)}{" "}
        {dot(stats.remote, SEMANTIC_COLORS.info)} {stats.remote ? "remote" : "local"}{" "}
        {dot(stats.eventCount > 0, SEMANTIC_COLORS.info)} events {stats.eventCount}{" "}
        {dot(stats.toolCount > 0, SEMANTIC_COLORS.tool)} tools {stats.toolCount}{" "}
        {dot(stats.lastTurn != null, SEMANTIC_COLORS.success)} turn {stats.lastTurn ?? "-"}
      </Text>
      <Text>{hashline}</Text>
    </Box>
  )
}
