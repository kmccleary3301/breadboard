import React from "react"
import { Box, Text } from "ink"
import type { TodoPreviewModel } from "./todoPreview.js"
import { TodoPreviewWidget } from "./TodoPreviewWidget.js"

export const RuntimePreviewStack: React.FC<{
  readonly claudeChrome: boolean
  readonly overlayActive: boolean
  readonly pendingClaudeStatus: string | null
  readonly todoPreviewModel: TodoPreviewModel | null
  readonly hintNodes: React.ReactNode[]
}> = ({ claudeChrome, overlayActive, pendingClaudeStatus, todoPreviewModel, hintNodes }) => {
  if (!claudeChrome) return null
  const hasContent = Boolean(pendingClaudeStatus) || Boolean(todoPreviewModel) || (!overlayActive && hintNodes.length > 0)
  if (!hasContent) return null

  return (
    <Box flexDirection="column">
      {pendingClaudeStatus ? <Text color="dim">{pendingClaudeStatus}</Text> : null}
      {!overlayActive && todoPreviewModel ? <TodoPreviewWidget model={todoPreviewModel} /> : null}
      {!overlayActive && hintNodes.length > 0 ? <Box flexDirection="column">{hintNodes}</Box> : null}
      {/* No extra spacing; the composer rules/input follow immediately. */}
    </Box>
  )
}
