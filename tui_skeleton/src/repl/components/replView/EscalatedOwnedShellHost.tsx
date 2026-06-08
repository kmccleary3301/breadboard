import React from "react"
import { Box, Text } from "ink"
import { CHALK, COLORS } from "./theme.js"

export const EscalatedOwnedShellHost: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <Box flexDirection="column">
      <Text color={COLORS.warning}>{CHALK.bold("owned viewport host")}{CHALK.dim(" · escalation seam")}</Text>
      <Box flexDirection="column">{children}</Box>
    </Box>
  )
}
