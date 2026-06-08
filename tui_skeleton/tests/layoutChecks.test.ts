import { describe, expect, it } from "vitest"

import { runLayoutAssertionsOnLines } from "../tools/assertions/layoutChecks.js"

describe("layoutChecks", () => {
  it("accepts the live composer glyph used by the TUI", () => {
    const lines = [
      "BreadBoard v0.2.0",
      "",
      "❯ Hello",
      "",
      "• [ready] last 1s · enter send",
    ]

    expect(runLayoutAssertionsOnLines(lines)).not.toContainEqual(
      expect.objectContaining({ id: "composer-missing" }),
    )
  })
})
