import { describe, expect, it } from "vitest"
import { createScrollWindow, formatScrollRange } from "../../utils/scrollWindow.js"

describe("scrollWindow utility", () => {
  it("clamps requested scroll and returns a consistent visible window", () => {
    const rows = Array.from({ length: 10 }, (_, idx) => `row-${idx + 1}`)
    const window = createScrollWindow(rows, 8, 3)
    expect(window.total).toBe(10)
    expect(window.viewport).toBe(3)
    expect(window.maxScroll).toBe(7)
    expect(window.scroll).toBe(7)
    expect(window.start).toBe(7)
    expect(window.end).toBe(10)
    expect(window.visible).toEqual(["row-8", "row-9", "row-10"])
  })

  it("formats scroll ranges with and without offset detail", () => {
    const rows = ["a", "b", "c", "d", "e"]
    const window = createScrollWindow(rows, 1, 3)
    expect(formatScrollRange(window)).toBe("2-4 of 5")
    expect(formatScrollRange(window, { includeScrollOffset: true })).toBe("2-4 of 5 • scroll 1/2")
  })

  it("returns a stable empty range string for empty data", () => {
    const window = createScrollWindow([], 0, 5)
    expect(window.visible).toEqual([])
    expect(formatScrollRange(window)).toBe("0-0 of 0")
  })
})
