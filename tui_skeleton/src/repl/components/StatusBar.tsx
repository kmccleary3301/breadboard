import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { StreamStats } from "../types.js"

const coloredDot = (active: boolean, color: string) => chalk.hex(color)(active ? "●" : "○")

interface StatusBarProps {
  readonly status: string
  readonly stats: StreamStats
  readonly hashline: string
  readonly spinner: string
  readonly pending: boolean
}

export const StatusBar: React.FC<StatusBarProps> = ({ status, stats, hashline, spinner, pending }) => {
  const statusDot = status.toLowerCase().startsWith("completed")
  const statusIndicator = pending ? spinner : coloredDot(statusDot, "#14b8a6")
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        {statusIndicator} {status}  {coloredDot(true, "#f97316")} model {chalk.bold(stats.model)}  {coloredDot(stats.remote, "#60a5fa")}{" "}
        {stats.remote ? "remote" : "local"}  {coloredDot(stats.eventCount > 0, "#a855f7")} events {stats.eventCount}  {coloredDot(stats.toolCount > 0, "#facc15")} tools {stats.toolCount}{" "}
        {coloredDot(stats.lastTurn != null, "#34d399")} turn {stats.lastTurn ?? "-"}
      </Text>
      <Text>{hashline}</Text>
    </Box>
  )
}
