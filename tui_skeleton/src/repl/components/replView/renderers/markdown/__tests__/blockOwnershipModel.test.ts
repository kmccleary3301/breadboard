import { describe, expect, it } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { classifyStableBlocksForPromotion } from "../blockStabilityClassifier.js"
import { StreamMdxBlockStore } from "../streamMdxBlockStore.js"

const block = (id: string, type: string, raw: string, isFinalized = true): Block => ({
  id,
  type,
  isFinalized,
  payload: { raw },
})

describe("P12 block ownership model primitives", () => {
  it("tracks changed blocks and finalized prefix", () => {
    const store = new StreamMdxBlockStore()
    const first = store.update("msg-1", [block("b1", "paragraph", "one"), block("b2", "paragraph", "two", false)])
    expect(first.changedBlockIds).toEqual(["b1", "b2"])
    expect(first.finalizedBlockIds).toEqual(["b1"])
    expect(first.stableFinalizedPrefixIds).toEqual(["b1"])

    const second = store.update("msg-1", [block("b1", "paragraph", "one"), block("b2", "paragraph", "two")])
    expect(second.changedBlockIds).toEqual([])
    expect(second.finalizedBlockIds).toEqual(["b1", "b2"])
    expect(second.stableFinalizedPrefixIds).toEqual(["b1", "b2"])

    const third = store.update("msg-1", [block("b1", "paragraph", "one"), block("b2", "paragraph", "changed")])
    expect(third.changedBlockIds).toEqual(["b2"])
  })

  it("detects mutation after a block is marked frozen", () => {
    const store = new StreamMdxBlockStore()
    store.update("msg-1", [block("b1", "paragraph", "stable")])
    store.markFrozen("msg-1", ["b1"])
    const update = store.update("msg-1", [block("b1", "paragraph", "mutated")])
    expect(update.mutationAfterFrozenIds).toEqual(["b1"])
  })

  it("classifies only conservative finalized blocks outside holdback and resize windows", () => {
    const store = new StreamMdxBlockStore()
    store.update("msg-1", [
      block("b1", "heading", "Title"),
      block("b2", "paragraph", "Body"),
      block("b3", "table", "| A |"),
      block("b4", "paragraph", "Tail"),
    ])
    const decisions = classifyStableBlocksForPromotion(store.get("msg-1"), {
      holdbackRows: 1,
      resizeQuietMs: 250,
      now: 1000,
      lastResizeAt: 0,
    })
    expect(decisions).toEqual([
      { blockId: "b1", eligible: true, reason: "finalized-stable-prefix" },
      { blockId: "b2", eligible: true, reason: "finalized-stable-prefix" },
      { blockId: "b3", eligible: false, reason: "unsupported-type:table" },
      { blockId: "b4", eligible: false, reason: "holdback-window" },
    ])

    const resizeDecisions = classifyStableBlocksForPromotion(store.get("msg-1"), {
      holdbackRows: 1,
      resizeQuietMs: 250,
      now: 1000,
      lastResizeAt: 900,
    })
    expect(resizeDecisions[0]).toEqual({ blockId: "b1", eligible: false, reason: "resize-quiet-window" })
  })
})
