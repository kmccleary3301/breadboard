import React from "react"
import { Box, Text } from "ink"
import chalk from "chalk"
import type { StreamStats } from "../../repl/types.js"
import { useHashTicker } from "../../repl/hooks/useHashTicker.js"

const dot = (active: boolean, color: string) => chalk.hex(color)(active ? "●" : "○")

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
        {dot(completed, "#38bdf8")} {status}  {dot(true, "#f97316")} model {chalk.bold(stats.model)}  {dot(stats.remote, "#60a5fa")}{" "}
        {stats.remote ? "remote" : "local"}  {dot(stats.eventCount > 0, "#a855f7")} events {stats.eventCount}  {dot(stats.toolCount > 0, "#facc15")} tools{" "}
        {stats.toolCount}  {dot(stats.lastTurn != null, "#34d399")} turn {stats.lastTurn ?? "-"}
      </Text>
      <Text>{hashline}</Text>
    </Box>
  )
}
