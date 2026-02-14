import React from "react"
import { Box, Text } from "ink"
import { COLORS, uiText } from "../theme.js"
import type { TodoPreviewModel } from "./todoPreview.js"

const previewColorForStatus = (status: string): string => {
  switch (status) {
    case "done":
      return "dim"
    case "blocked":
      return COLORS.error
    case "in_progress":
      return COLORS.warning
    case "canceled":
      return COLORS.textMuted
    default:
      return COLORS.textBright
  }
}

export const TodoPreviewWidget: React.FC<{ model: TodoPreviewModel }> = ({ model }) => {
  if (!model.items || model.items.length === 0) return null
  return (
    <Box flexDirection="column">
      {model.header ? <Text color={COLORS.textMuted} wrap="truncate">{uiText(model.header)}</Text> : null}
      {model.items.map((item) => (
        <Text key={item.id} color={previewColorForStatus(item.status)} wrap="truncate">
          {uiText(item.label)}
        </Text>
      ))}
    </Box>
  )
}

