import React from "react"
import { Box, Text } from "ink"
import type { TodoPreviewModel } from "./todoPreview.js"
import { TodoPreviewWidget } from "./TodoPreviewWidget.js"
import type { ThinkingPreviewModel } from "./thinkingPreview.js"
import { ThinkingPreviewWidget } from "./ThinkingPreviewWidget.js"

type RuntimeStatusChip = {
  readonly id: string
  readonly label: string
  readonly tone: "info" | "success" | "warning" | "error"
}

export const RuntimePreviewStack: React.FC<{
  readonly claudeChrome: boolean
  readonly overlayActive: boolean
  readonly footerV2Enabled: boolean
  readonly pendingClaudeStatus: string | null
  readonly runtimeStatusChips: ReadonlyArray<RuntimeStatusChip>
  readonly thinkingPreviewModel: ThinkingPreviewModel | null
  readonly todoPreviewModel: TodoPreviewModel | null
  readonly hintNodes: React.ReactNode[]
}> = ({
  claudeChrome,
  overlayActive,
  footerV2Enabled,
  pendingClaudeStatus,
  runtimeStatusChips,
  thinkingPreviewModel,
  todoPreviewModel,
  hintNodes,
}) => {
  if (!claudeChrome) return null
  const hasStatusChips = !footerV2Enabled && runtimeStatusChips.length > 0
  const hasContent =
    Boolean(pendingClaudeStatus) ||
    hasStatusChips ||
    Boolean(thinkingPreviewModel) ||
    Boolean(todoPreviewModel) ||
    (!overlayActive && hintNodes.length > 0)
  if (!hasContent) return null

  const statusToneColor = (tone: RuntimeStatusChip["tone"]): string => {
    if (tone === "error") return "red"
    if (tone === "warning") return "yellow"
    if (tone === "success") return "green"
    return "cyan"
  }

  return (
    <Box flexDirection="column">
      {pendingClaudeStatus ? <Text color="dim">{pendingClaudeStatus}</Text> : null}
      {!overlayActive && hasStatusChips ? (
        <Text color="dim">
          {runtimeStatusChips.map((chip, index) => (
            <Text key={`${chip.id}-${index}`} color={statusToneColor(chip.tone)}>
              {index > 0 ? " " : ""}
              [{chip.label}]
            </Text>
          ))}
        </Text>
      ) : null}
      {!overlayActive && thinkingPreviewModel ? <ThinkingPreviewWidget model={thinkingPreviewModel} /> : null}
      {!overlayActive && todoPreviewModel ? <TodoPreviewWidget model={todoPreviewModel} /> : null}
      {!overlayActive && hintNodes.length > 0 ? <Box flexDirection="column">{hintNodes}</Box> : null}
      {/* No extra spacing; the composer rules/input follow immediately. */}
    </Box>
  )
}
