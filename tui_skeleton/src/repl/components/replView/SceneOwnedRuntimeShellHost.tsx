import React from "react"
import { Box, Text } from "ink"
import { CHALK, COLORS } from "./theme.js"

type SceneOwnedRuntimeShellHostProps = {
  children: React.ReactNode
  sessionId: string
  status: string
  pendingResponse: boolean
  disconnected: boolean
  rows: number
  transcriptShortcutHint: string
}

export const SceneOwnedRuntimeShellHost: React.FC<SceneOwnedRuntimeShellHostProps> = ({
  children,
  pendingResponse,
  disconnected,
  rows,
  transcriptShortcutHint,
}) => {
  const tone = disconnected ? COLORS.error : pendingResponse ? COLORS.warning : COLORS.success
  const stateLabel = disconnected ? "Needs attention" : pendingResponse ? "Streaming..." : "Ready"
  return (
    <Box flexDirection="column" height={Math.max(1, rows)}>
      <Text color={tone}>
        {CHALK.bold("Live Shell")}
        {CHALK.dim(" · ")}
        {CHALK.bold(stateLabel)}
        {CHALK.dim(disconnected ? " · Check connection" : pendingResponse ? " · Working" : ` · ${transcriptShortcutHint}`)}
      </Text>
      <Box flexDirection="column" flexGrow={1}>{children}</Box>
    </Box>
  )
}
