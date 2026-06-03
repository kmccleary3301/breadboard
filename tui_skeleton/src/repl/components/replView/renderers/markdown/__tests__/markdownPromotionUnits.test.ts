import { describe, expect, it } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { expandMarkdownBlocksForPromotion } from "../markdownPromotionUnits.js"
import { blocksToLines } from "../streamMdxAdapter.js"

const listBlock = (raw: string, isFinalized = false): Block => ({
  id: "list-1",
  type: "list",
  isFinalized,
  payload: { raw },
})

describe("markdown promotion units", () => {
  it("splits one streaming list block into stable item-level promotion units", () => {
    const units = expandMarkdownBlocksForPromotion([
      listBlock("- one\n- two\n- three", false),
    ])

    expect(units.map((unit) => unit.id)).toEqual(["list-1:item:0", "list-1:item:1", "list-1:item:2"])
    expect(units.map((unit) => unit.type)).toEqual(["list-item", "list-item", "list-item"])
    expect(units.map((unit) => unit.isFinalized)).toEqual([true, true, false])
  })

  it("preserves list rendering for item-level units", () => {
    const units = expandMarkdownBlocksForPromotion([
      listBlock("- one\n- two", true),
    ])

    expect(blocksToLines(units, { width: 80 }).map((line) => line.replace(/\u001b\[[0-9;]*m/g, ""))).toEqual(["- one", "- two"])
  })
})
