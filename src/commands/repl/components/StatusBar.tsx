import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { StreamStats } from "../types.js"

const dot = (active: boolean, color: string) => chalk.hex(color)(active ? "●" : "○")

interface StatusBarProps {
  readonly status: string
  readonly stats: StreamStats
  readonly hashline: string
}

export const StatusBar: React.FC<StatusBarProps> = ({ status, stats, hashline }) => {
  const statusDot = status.toLowerCase().includes("completed")
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text>
        {dot(statusDot, "#22c55e")} {status.padEnd(12, " ")}{"  "}
        {dot(true, "#f97316")} model {chalk.bold(stats.model)}{"  "}
        {dot(stats.remote, "#38bdf8")} {stats.remote ? "remote" : "local"}{"  "}
        {dot(stats.eventCount > 0, "#a855f7")} events {stats.eventCount}{"  "}
        {dot(stats.toolCount > 0, "#facc15")} tools {stats.toolCount}{"  "}
        {dot(stats.lastTurn != null, "#34d399")} turn {stats.lastTurn ?? "-"}
      </Text>
      <Text>{hashline}</Text>
    </Box>
  )
}

