import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { buildSearchEntries, searchEntries } from "./searchIndex"

describe("searchIndex", () => {
  it("builds transcript/tool/artifact entries and ranks query hits", () => {
    const events: SessionEvent[] = [
      {
        id: "e1",
        type: "tool_result",
        session_id: "s1",
        turn: 1,
        timestamp: 1_000,
        payload: { path: "src/app.ts", summary: "patched app" },
      },
    ]
    const entries = buildSearchEntries({
      transcript: [{ id: "m1", role: "assistant", text: "apply patch to app.ts", final: true }],
      toolRows: [{ id: "t1", type: "tool_result", label: "edit", summary: "updated src/app.ts", timestamp: 1_001, diffText: null, diffFilePath: null }],
      events,
      limits: { transcript: 10, toolRows: 10, events: 10 },
    })
    const results = searchEntries(entries, "app.ts", { type: "all" })
    expect(results.length).toBeGreaterThan(0)
    expect(results[0].score).toBeGreaterThanOrEqual(results[results.length - 1].score)
  })

  it("returns stable ordering for equal-score rows", () => {
    const rows = [
      { id: "b", type: "tool" as const, text: "abc", timestamp: 100 },
      { id: "a", type: "tool" as const, text: "abc", timestamp: 100 },
    ]
    const results = searchEntries(rows, "abc")
    expect(results.map((row) => row.id)).toEqual(["a", "b"])
  })
})
