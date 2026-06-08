import { describe, expect, it } from "vitest"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import { shouldRenderScrollbackActiveEntry } from "../useReplViewScrollback.js"

const assistantFinal = (id = "a1"): TranscriptItem => ({
  id,
  kind: "message",
  speaker: "assistant",
  text: "final answer",
  phase: "final",
  createdAt: 2,
  source: "conversation",
})

const userFinal = (id = "u1"): TranscriptItem => ({
  id,
  kind: "message",
  speaker: "user",
  text: "prompt",
  phase: "final",
  createdAt: 1,
  source: "conversation",
})

const assistantStreaming = (id = "a1"): TranscriptItem => ({
  id,
  kind: "message",
  speaker: "assistant",
  text: "streaming answer",
  phase: "streaming",
  createdAt: 2,
  source: "conversation",
  streaming: true,
})

describe("shouldRenderScrollbackActiveEntry", () => {
  it("keeps finalized assistant output in the active band until the static feed records it", () => {
    expect(shouldRenderScrollbackActiveEntry(assistantFinal(), new Set())).toBe(true)
    expect(shouldRenderScrollbackActiveEntry(assistantFinal(), new Set(["a1"]))).toBe(false)
  })

  it("keeps finalized user prompts visible until the static feed records them", () => {
    expect(shouldRenderScrollbackActiveEntry(userFinal(), new Set())).toBe(true)
    expect(shouldRenderScrollbackActiveEntry(userFinal(), new Set(["u1"]))).toBe(false)
  })

  it("keeps live assistant output visible before static ownership is possible", () => {
    expect(shouldRenderScrollbackActiveEntry(assistantStreaming(), new Set())).toBe(true)
    expect(shouldRenderScrollbackActiveEntry(assistantStreaming(), new Set(["a1"]))).toBe(false)
  })
})
