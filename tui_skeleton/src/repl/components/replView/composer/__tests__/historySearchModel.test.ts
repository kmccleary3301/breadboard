import { describe, expect, it } from "vitest"
import { findHistorySearchMatch } from "../historySearchModel.js"

describe("findHistorySearchMatch", () => {
  it("cycles backward through matching history entries while preserving the original query", () => {
    const entries = ["alpha history search", "beta history search", "alpha second search"]
    const latest = findHistorySearchMatch(entries, "alpha", -1, null)
    expect(latest).toMatchObject({ entry: "alpha second search", index: 2 })
    const older = findHistorySearchMatch(entries, latest!.entry, -1, latest!.cursor)
    expect(older).toMatchObject({ entry: "alpha history search", index: 0 })
  })

  it("cycles forward from an active search cursor", () => {
    const entries = ["alpha history search", "beta history search", "alpha second search"]
    const latest = findHistorySearchMatch(entries, "alpha", -1, null)
    const older = findHistorySearchMatch(entries, latest!.entry, -1, latest!.cursor)
    expect(older).toMatchObject({ entry: "alpha history search", index: 0 })
    const forward = findHistorySearchMatch(entries, older!.entry, 1, older!.cursor)
    expect(forward).toMatchObject({ entry: "alpha second search", index: 2 })
  })

  it("returns null when no history entry matches", () => {
    expect(findHistorySearchMatch(["abc"], "xyz", -1, null)).toBeNull()
  })
})
