import React from "react"
import { Box, Text } from "ink"
import type { TodoPreviewModel } from "./todoPreview.js"
import { TodoPreviewWidget } from "./TodoPreviewWidget.js"
import type { ThinkingPreviewModel } from "./thinkingPreview.js"
import { ThinkingPreviewWidget } from "./ThinkingPreviewWidget.js"
import { COLORS } from "../theme.js"

export const RuntimePreviewStack: React.FC<{
  readonly claudeChrome: boolean
  readonly overlayActive: boolean
  readonly pendingClaudeStatus: string | null
  readonly todoPreviewModel: TodoPreviewModel | null
  readonly thinkingPreviewModel: ThinkingPreviewModel | null
  readonly statusChips: ReadonlyArray<{ readonly id: string; readonly label: string; readonly tone: "info" | "success" | "warning" | "error" }>
  readonly hintNodes: React.ReactNode[]
}> = ({ claudeChrome, overlayActive, pendingClaudeStatus, todoPreviewModel, thinkingPreviewModel, statusChips, hintNodes }) => {
  if (!claudeChrome) return null
  const hasContent =
    Boolean(pendingClaudeStatus) ||
    Boolean(todoPreviewModel) ||
    Boolean(thinkingPreviewModel) ||
    statusChips.length > 0 ||
    (!overlayActive && hintNodes.length > 0)
  if (!hasContent) return null

  const colorForTone = (tone: "info" | "success" | "warning" | "error"): string => {
    if (tone === "success") return COLORS.success
    if (tone === "warning") return COLORS.warning
    if (tone === "error") return COLORS.error
    return COLORS.info
  }

  return (
    <Box flexDirection="column">
      {statusChips.length > 0 ? (
        <Box flexDirection="row">
          {statusChips.map((chip, index) => (
            <Text key={chip.id} color={colorForTone(chip.tone)} wrap="truncate">
              {`${index > 0 ? " " : ""}[${chip.label}]`}
            </Text>
          ))}
        </Box>
      ) : null}
      {pendingClaudeStatus ? <Text color="dim">{pendingClaudeStatus}</Text> : null}
      {!overlayActive && thinkingPreviewModel ? <ThinkingPreviewWidget model={thinkingPreviewModel} /> : null}
      {!overlayActive && todoPreviewModel ? <TodoPreviewWidget model={todoPreviewModel} /> : null}
      {!overlayActive && hintNodes.length > 0 ? <Box flexDirection="column">{hintNodes}</Box> : null}
      {/* No extra spacing; the composer rules/input follow immediately. */}
    </Box>
  )
}
