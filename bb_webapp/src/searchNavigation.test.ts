import { describe, expect, it } from "vitest"
import { nextSearchResultIndex, prevSearchResultIndex, resolveSearchResultAnchors } from "./searchNavigation"

describe("searchNavigation", () => {
  it("computes next index with wrap-around", () => {
    expect(nextSearchResultIndex(0, -1)).toBe(-1)
    expect(nextSearchResultIndex(3, -1)).toBe(0)
    expect(nextSearchResultIndex(3, 0)).toBe(1)
    expect(nextSearchResultIndex(3, 2)).toBe(0)
  })

  it("computes previous index with wrap-around", () => {
    expect(prevSearchResultIndex(0, -1)).toBe(-1)
    expect(prevSearchResultIndex(3, 0)).toBe(2)
    expect(prevSearchResultIndex(3, 2)).toBe(1)
  })

  it("resolves artifact fallback anchors", () => {
    expect(resolveSearchResultAnchors("tool-12")).toEqual({ primary: "entry-tool-12", fallback: null })
    expect(resolveSearchResultAnchors("artifact-evt-77")).toEqual({ primary: "entry-artifact-evt-77", fallback: "event-evt-77" })
  })
})
