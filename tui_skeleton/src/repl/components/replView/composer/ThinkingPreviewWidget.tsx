import React from "react"
import { Box, Text } from "ink"
import { COLORS } from "../theme.js"
import type { ThinkingPreviewModel } from "./thinkingPreview.js"

export const ThinkingPreviewWidget: React.FC<{ model: ThinkingPreviewModel }> = ({ model }) => {
  if (!model.lines || model.lines.length === 0) return null
  const borderColor =
    model.lifecycle === "closed" ? COLORS.textMuted : COLORS.info
  const lineColor = model.lifecycle === "closed" ? COLORS.textMuted : COLORS.textBright
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={borderColor}
      paddingX={1}
      width={model.frameWidth ?? undefined}
    >
      <Text color={COLORS.textMuted} wrap="truncate">
        {model.headerLine}
      </Text>
      {model.lines.map((line, index) => (
        <Text key={`thinking-line-${index}`} color={lineColor} wrap="truncate">
          {line}
        </Text>
      ))}
    </Box>
  )
}

