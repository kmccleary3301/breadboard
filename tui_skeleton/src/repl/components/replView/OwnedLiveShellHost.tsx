import React from "react"
import { Box, Text } from "ink"
import { CHALK, COLORS } from "./theme.js"

export const OwnedLiveShellHost: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <Box flexDirection="column">
      <Text color={COLORS.info}>{CHALK.bold("owned live shell")}{CHALK.dim(" · prototype")}</Text>
      <Box flexDirection="column">{children}</Box>
    </Box>
  )
}
