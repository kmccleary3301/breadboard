import React from "react"
import { Box, Text } from "ink"
import { CHALK, COLORS } from "./theme.js"

export const DedicatedSceneBufferShellHost: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <Box flexDirection="column">
      <Text color={COLORS.success}>{CHALK.bold("scene buffer host")}{CHALK.dim(" · dormant strategy")}</Text>
      <Box flexDirection="column">{children}</Box>
    </Box>
  )
}
