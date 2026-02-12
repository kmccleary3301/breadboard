import { describe, expect, it } from "vitest"
import {
  INLINE_THINKING_MARKER,
  isInlineThinkingBlockText,
  shouldAutoCollapseEntry,
  stripInlineThinkingMarker,
} from "../transcriptUtils.js"

describe("transcriptUtils inline thinking marker", () => {
  it("detects and strips inline thinking marker payload", () => {
    const text = `${INLINE_THINKING_MARKER}\nsummary: plan\nsummary: refine`
    expect(isInlineThinkingBlockText(text)).toBe(true)
    expect(stripInlineThinkingMarker(text)).toBe("summary: plan\nsummary: refine")
  })

  it("forces auto-collapse for inline thinking entries", () => {
    const collapsible = shouldAutoCollapseEntry({
      id: "conv-1",
      speaker: "assistant",
      text: `${INLINE_THINKING_MARKER}\nsummary: plan`,
      phase: "final",
      createdAt: 1,
    })
    expect(collapsible).toBe(true)
  })
})
