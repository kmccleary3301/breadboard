import type { ConversationEntry } from "../../../types.js"

export const hasVisibleAssistantStreamingText = (
  conversation: ReadonlyArray<ConversationEntry | null | undefined>,
  pendingResponse: boolean,
): boolean =>
  Boolean(pendingResponse) &&
  conversation.some(
    (entry) =>
      entry?.speaker === "assistant" &&
      entry.phase === "streaming" &&
      typeof entry.text === "string" &&
      entry.text.trim().length > 0,
  )

export const shouldSuppressThinkingPreview = (input: {
  readonly activityPrimary?: string | null
  readonly conversation: ReadonlyArray<ConversationEntry | null | undefined>
  readonly pendingResponse: boolean
  readonly previewEventCount?: number | null
}): boolean => {
  if (!input.pendingResponse) return false
  const phase = String(input.activityPrimary ?? "").trim().toLowerCase()
  if (phase === "responding") return true
  const eventCount = Number(input.previewEventCount ?? 0)
  if (Number.isFinite(eventCount) && eventCount > 12) return true
  return hasVisibleAssistantStreamingText(input.conversation, input.pendingResponse)
}
