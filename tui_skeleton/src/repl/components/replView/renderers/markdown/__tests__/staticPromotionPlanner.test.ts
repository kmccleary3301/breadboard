import { describe, expect, it } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { applyFrozenChunkToFragmentState, buildInitialFragmentState, findActiveFrozenOverlap, hotTailBlockIds } from "../assistantMessageFragmentModel.js"
import { expandMarkdownBlocksForPromotion } from "../markdownPromotionUnits.js"
import { planStaticPromotions } from "../staticPromotionPlanner.js"
import { StreamMdxBlockStore } from "../streamMdxBlockStore.js"
import { TerminalLineChunkCache } from "../terminalLineChunkCache.js"

const block = (id: string, type: string, raw: string, isFinalized = true): Block => ({
  id,
  type,
  isFinalized,
  payload: { raw, inline: [{ kind: "text", text: raw }] },
})

const policy = { holdbackRows: 1, resizeQuietMs: 250, now: 1000, lastResizeAt: 0 }

describe("static promotion planner", () => {
  it("plans conservative contiguous finalized chunks and leaves a hot tail", () => {
    const store = new StreamMdxBlockStore()
    store.update("msg-1", [
      block("b1", "heading", "Title"),
      block("b2", "paragraph", "Body"),
      block("b3", "table", "| A |"),
      block("b4", "paragraph", "Tail"),
    ])
    const cache = new TerminalLineChunkCache()
    const plan = planStaticPromotions({
      messageId: "msg-1",
      snapshots: store.get("msg-1"),
      cache,
      renderOptions: { width: 80 },
      stabilityPolicy: policy,
      now: 1000,
    })

    expect(plan.chunks).toHaveLength(1)
    expect(plan.chunks[0].blockIds).toEqual(["b1", "b2"])
    expect(plan.hotTailBlockIds).toEqual(["b3", "b4"])
    expect(plan.duplicateChunkIds).toEqual([])
  })

  it("excludes promoted blocks from hot tail fragment state", () => {
    const store = new StreamMdxBlockStore()
    store.update("msg-1", [block("b1", "heading", "Title"), block("b2", "paragraph", "Tail")])
    const cache = new TerminalLineChunkCache()
    const plan = planStaticPromotions({
      messageId: "msg-1",
      snapshots: store.get("msg-1"),
      cache,
      renderOptions: { width: 80 },
      stabilityPolicy: policy,
      now: 1000,
    })
    const initial = buildInitialFragmentState("msg-1", ["b1", "b2"])
    const next = applyFrozenChunkToFragmentState(initial, plan.chunks[0], "static-msg-1-chunk-0")

    expect(hotTailBlockIds(next)).toEqual(["b2"])
    expect(findActiveFrozenOverlap(next)).toEqual([])
    expect(next.fragments.map((fragment) => fragment.kind)).toEqual(["frozen-chunk", "hot-tail"])
  })

  it("reports duplicate chunk ids instead of planning a second append", () => {
    const store = new StreamMdxBlockStore()
    store.update("msg-1", [block("b1", "heading", "Title"), block("b2", "paragraph", "Tail")])
    const cache = new TerminalLineChunkCache()
    const first = planStaticPromotions({
      messageId: "msg-1",
      snapshots: store.get("msg-1"),
      cache,
      renderOptions: { width: 80 },
      stabilityPolicy: policy,
      now: 1000,
    })
    const second = planStaticPromotions({
      messageId: "msg-1",
      snapshots: store.get("msg-1"),
      cache,
      renderOptions: { width: 80 },
      stabilityPolicy: policy,
      alreadyPromotedChunkIds: new Set(first.chunks.map((chunk) => chunk.chunkId)),
      now: 1000,
    })

    expect(second.chunks).toEqual([])
    expect(second.duplicateChunkIds).toEqual(first.chunks.map((chunk) => chunk.chunkId))
  })

  it("promotes finalized prefix items from a single streaming list block", () => {
    const store = new StreamMdxBlockStore()
    const units = expandMarkdownBlocksForPromotion([
      block("list-1", "list", "- one\n- two\n- three", false),
    ])
    store.update("msg-1", units)
    const cache = new TerminalLineChunkCache()
    const plan = planStaticPromotions({
      messageId: "msg-1",
      snapshots: store.get("msg-1"),
      cache,
      renderOptions: { width: 80 },
      stabilityPolicy: policy,
      now: 1000,
    })

    expect(plan.chunks).toHaveLength(1)
    expect(plan.chunks[0].blockIds).toEqual(["list-1:item:0", "list-1:item:1"])
    expect(plan.hotTailBlockIds).toEqual(["list-1:item:2"])
  })
})
