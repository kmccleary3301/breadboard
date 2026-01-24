import React from "react"
import { Box, Text } from "ink"

export const COMMAND_RESULT_LIMIT = 64

export const buildCommandResultEntry = (title: string, lines: string[]) => {
  const entryId = `atcmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
  const node = (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="cyan">{title}</Text>
      {lines.map((line, index) => (
        <Text key={`${entryId}-line-${index}`} wrap="truncate-end">
          {line}
        </Text>
      ))}
    </Box>
  )
  return { id: entryId, node }
}
