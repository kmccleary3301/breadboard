import { describe, expect, it } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { blocksToLines } from "../streamMdxAdapter.js"
import {
  TerminalLineChunkCache,
  buildRenderOptionsKey,
  cachedBlocksToLines,
  hashMarkdownBlocks,
} from "../terminalLineChunkCache.js"

const paragraphBlock = (id: string, raw: string, isFinalized = true): Block => ({
  id,
  type: "paragraph",
  isFinalized,
  payload: { raw, inline: [{ kind: "text", text: raw }] },
})

const tableBlock = (id = "table-1"): Block => ({
  id,
  type: "table",
  isFinalized: true,
  payload: {
    raw: "| Column | Value |\n| --- | --- |\n| Alpha | 1 |",
    meta: {
      header: [[{ kind: "text", text: "Column" }], [{ kind: "text", text: "Value" }]],
      rows: [[[{ kind: "text", text: "Alpha" }], [{ kind: "text", text: "1" }]]],
    },
  },
})

describe("TerminalLineChunkCache", () => {
  it("returns the same lines as blocksToLines", () => {
    const blocks = [paragraphBlock("p1", "First para"), paragraphBlock("p2", "Second para")]
    const cache = new TerminalLineChunkCache()
    expect(cachedBlocksToLines(cache, blocks, { width: 40 })).toEqual(blocksToLines(blocks, { width: 40 }))
    expect(cache.stats()).toMatchObject({ hits: 0, misses: 1, entries: 1 })
  })

  it("records cache hits for identical blocks and render options", () => {
    const blocks = [paragraphBlock("p1", "Repeated")]
    const cache = new TerminalLineChunkCache()
    cachedBlocksToLines(cache, blocks, { width: 40 })
    cachedBlocksToLines(cache, blocks, { width: 40 })
    expect(cache.stats()).toMatchObject({ hits: 1, misses: 1, entries: 1, hitRate: 0.5 })
  })

  it("invalidates when width changes", () => {
    const blocks = [tableBlock()]
    const cache = new TerminalLineChunkCache()
    const wide = cachedBlocksToLines(cache, blocks, { width: 80 })
    const narrow = cachedBlocksToLines(cache, blocks, { width: 24 })
    expect(wide).toEqual(blocksToLines(blocks, { width: 80 }))
    expect(narrow).toEqual(blocksToLines(blocks, { width: 24 }))
    expect(cache.stats()).toMatchObject({ hits: 0, misses: 2, entries: 2 })
  })

  it("invalidates when block content changes", () => {
    const cache = new TerminalLineChunkCache()
    const first = [paragraphBlock("p1", "Before")]
    const second = [paragraphBlock("p1", "After")]
    expect(hashMarkdownBlocks(first)).not.toEqual(hashMarkdownBlocks(second))
    cachedBlocksToLines(cache, first, { width: 40 })
    cachedBlocksToLines(cache, second, { width: 40 })
    expect(cache.stats()).toMatchObject({ hits: 0, misses: 2, entries: 2 })
  })

  it("captures chunk metadata needed by later static promotion", () => {
    const blocks = [paragraphBlock("p1", "Metadata")]
    const cache = new TerminalLineChunkCache()
    const chunk = cache.getOrCreate(blocks, { width: 40 }, { messageId: "msg-1", now: 123 })
    expect(chunk).toMatchObject({
      messageId: "msg-1",
      blockIds: ["p1"],
      rowCount: 1,
      createdAt: 123,
      finalizedAt: 123,
      source: "stream-mdx",
    })
    expect(chunk.chunkId).toContain("msg-1/chunk/p1/")
    expect(chunk.plainLines).toEqual(["Metadata"])
  })

  it("normalizes render option keys", () => {
    expect(buildRenderOptionsKey({ width: 40.8 }).width).toBe(40)
    expect(buildRenderOptionsKey({}).width).toBeNull()
  })
})
