import { describe, expect, it } from "vitest"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import { applyTranscriptMemoryBounds } from "../transcriptMemoryBounds.js"

const makeMessage = (id: string, text: string, createdAt: number): TranscriptItem => ({
  id,
  kind: "message",
  source: "conversation",
  createdAt,
  speaker: "assistant",
  text,
  phase: "final",
})

describe("applyTranscriptMemoryBounds", () => {
  it("returns input unchanged when disabled", () => {
    const entries = [makeMessage("m-1", "alpha", 1), makeMessage("m-2", "beta", 2)]
    const result = applyTranscriptMemoryBounds(entries, {
      enabled: false,
      maxItems: 1,
      maxBytes: 64,
      includeCompactionMarker: true,
    })
    expect(result.items).toEqual(entries)
    expect(result.summary).toBeNull()
  })

  it("drops oldest entries by count and prepends a deterministic compaction marker", () => {
    const entries = [
      makeMessage("m-1", "alpha", 1),
      makeMessage("m-2", "beta", 2),
      makeMessage("m-3", "gamma", 3),
      makeMessage("m-4", "delta", 4),
    ]
    const result = applyTranscriptMemoryBounds(entries, {
      enabled: true,
      maxItems: 2,
      maxBytes: 10_000,
      includeCompactionMarker: true,
    })
    expect(result.summary?.droppedCount).toBe(2)
    expect(result.items.length).toBe(3)
    expect(result.items[0]?.kind).toBe("system")
    expect(result.items[0]?.id).toBe("system:transcript-compacted:m-3:2")
    expect(result.items[1]?.id).toBe("m-3")
    expect(result.items[2]?.id).toBe("m-4")
  })

  it("applies byte bounds deterministically while preserving newest ordering", () => {
    const entries = Array.from({ length: 10 }, (_, index) =>
      makeMessage(`msg-${index + 1}`, `line-${index + 1}-${"x".repeat(40)}`, index + 1),
    )
    const options = {
      enabled: true,
      maxItems: 10,
      maxBytes: 600,
      includeCompactionMarker: false,
    } as const
    const first = applyTranscriptMemoryBounds(entries, options)
    const second = applyTranscriptMemoryBounds(entries, options)
    expect(first.items.map((item) => item.id)).toEqual(second.items.map((item) => item.id))
    expect(first.summary?.droppedCount).toBeGreaterThan(0)
    const ids = first.items.map((item) => item.id)
    expect(ids[ids.length - 1]).toBe("msg-10")
  })
})
