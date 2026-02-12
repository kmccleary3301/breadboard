import { describe, expect, it } from "vitest"
import { scanStableBoundary } from "../stableBoundaryScanner.js"

describe("stableBoundaryScanner", () => {
  it("keeps partial trailing line in tail", () => {
    const result = scanStableBoundary("line one\nline two")
    expect(result.stableBoundaryLen).toBe("line one\n".length)
    expect(result.prefix).toBe("line one\n")
    expect(result.tail).toBe("line two")
  })

  it("holds boundary inside an unclosed fence", () => {
    const result = scanStableBoundary("```ts\nconst x = 1;\nconst y = 2")
    expect(result.state.inFence).toBe(true)
    expect(result.stableBoundaryLen).toBe(0)
    expect(result.tail).toContain("const y = 2")
  })

  it("releases boundary after fence closes", () => {
    const result = scanStableBoundary("```ts\nconst x = 1;\n```\nnext\n")
    expect(result.state.inFence).toBe(false)
    expect(result.stableBoundaryLen).toBe("```ts\nconst x = 1;\n```\nnext\n".length)
    expect(result.tail).toBe("")
  })

  it("tracks list/table line modes and resets on blank line", () => {
    const listResult = scanStableBoundary("- one\n- two\n")
    expect(listResult.state.inList).toBe(true)
    expect(listResult.state.inTable).toBe(false)

    const tableResult = scanStableBoundary("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    expect(tableResult.state.inTable).toBe(true)
    expect(tableResult.state.inList).toBe(false)

    const resetResult = scanStableBoundary("| a | b |\n\nnext\n")
    expect(resetResult.state.inTable).toBe(false)
    expect(resetResult.state.inList).toBe(false)
  })
})
