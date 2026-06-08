import { describe, expect, it } from "vitest"
import type { ConversationEntry } from "../../../../types.js"
import { hasVisibleAssistantStreamingText, shouldSuppressThinkingPreview } from "../streamingSurfacePolicy.js"

const entry = (input: Partial<ConversationEntry>): ConversationEntry => ({
  id: input.id ?? "entry-1",
  speaker: input.speaker ?? "assistant",
  text: input.text ?? "",
  phase: input.phase ?? "streaming",
  createdAt: input.createdAt ?? 0,
})

describe("streamingSurfacePolicy", () => {
  it("detects visible assistant streaming text only while a response is pending", () => {
    const conversation = [entry({ text: "partial answer" })]

    expect(hasVisibleAssistantStreamingText(conversation, true)).toBe(true)
    expect(hasVisibleAssistantStreamingText(conversation, false)).toBe(false)
  })

  it("does not treat empty, user, or finalized entries as active assistant streaming", () => {
    expect(hasVisibleAssistantStreamingText([entry({ text: "   " })], true)).toBe(false)
    expect(hasVisibleAssistantStreamingText([entry({ speaker: "user", text: "hello" })], true)).toBe(false)
    expect(hasVisibleAssistantStreamingText([entry({ text: "done", phase: "final" })], true)).toBe(false)
  })

  it("suppresses thinking preview as soon as runtime enters responding", () => {
    expect(
      shouldSuppressThinkingPreview({
        activityPrimary: "responding",
        conversation: [],
        pendingResponse: true,
      }),
    ).toBe(true)
  })

  it("keeps thinking preview available during pre-answer thinking", () => {
    expect(
      shouldSuppressThinkingPreview({
        activityPrimary: "thinking",
        conversation: [],
        pendingResponse: true,
      }),
    ).toBe(false)
  })

  it("suppresses high-churn thinking previews after the short initial feedback window", () => {
    expect(
      shouldSuppressThinkingPreview({
        activityPrimary: "thinking",
        conversation: [],
        pendingResponse: true,
        previewEventCount: 13,
      }),
    ).toBe(true)
  })
})
