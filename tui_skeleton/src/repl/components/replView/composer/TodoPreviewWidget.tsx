import React from "react"
import { Box, Text } from "ink"
import { COLORS, uiText } from "../theme.js"
import { todoStatusPresentation } from "../../../todos/todoStatusPresentation.js"
import type { TodoPreviewModel } from "./todoPreview.js"


export const TodoPreviewWidget: React.FC<{ model: TodoPreviewModel }> = ({ model }) => {
  if (!model.items || model.items.length === 0) return null
  const headerLine = model.headerLine

  const body = (
    <>
      {headerLine ? (
        <Text color={COLORS.textMuted} wrap="truncate">
          {uiText(headerLine)}
        </Text>
      ) : null}
      {model.items.map((item) => (
        <Text key={item.id} color={todoStatusPresentation(item.status).previewColor} wrap="truncate">
          {uiText(item.label)}
        </Text>
      ))}
    </>
  )

  if (model.style === "minimal") {
    return <Box flexDirection="column">{body}</Box>
  }
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={COLORS.textMuted}
      paddingX={1}
      width={model.frameWidth ?? undefined}
    >
      {body}
    </Box>
  )
}
