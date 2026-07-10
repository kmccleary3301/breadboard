import React from "react"
import { Box, Text } from "ink"
import type { TranscriptSystemItem } from "../../../transcriptModel.js"
import { resolveActualStdoutColumns } from "../../../inkScrollbackStdout.js"
import { truncateLine } from "../utils/format.js"

export const COMMAND_RESULT_LIMIT = 64

export const buildCommandResultTranscriptItem = (
  id: string,
  title: string,
  lines: readonly string[],
): TranscriptSystemItem => ({
  id,
  kind: "system",
  systemKind: "command-result",
  text: [title, ...lines].join("\n"),
  status: "success",
  createdAt: Date.now(),
  source: "system",
  cellRole: "command-result",
  lifecycle: "committed",
})

export const buildCommandResultEntry = (title: string, lines: string[]) => {
  const entryId = `atcmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
  const transcriptCell = buildCommandResultTranscriptItem(entryId, title, lines)
  const displayWidth = Math.max(20, resolveActualStdoutColumns())
  const displayTitle = truncateLine(title, displayWidth)
  const displayLines = lines.map((line) => truncateLine(line, displayWidth))
  const node = (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="cyan">{displayTitle}</Text>
      {displayLines.map((line, index) => (
        <Text key={`${entryId}-line-${index}`} wrap="truncate-end">
          {line}
        </Text>
      ))}
    </Box>
  )
  return { id: entryId, node, lineCount: 1 + lines.length + 1, transcriptCell }
}
