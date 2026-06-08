import { describe, expect, it } from "vitest"
import { renderDetailPresentation } from "../src/commands/commandDetailPresentation.js"

describe("commandDetailPresentation", () => {
  it("renders lines sections and json blocks", () => {
    expect(
      renderDetailPresentation("Report", {
        lines: ["A: 1"],
        sections: [{ title: "Conversation", lines: ["x", "y"] }],
        jsonBlocks: [{ label: "Completion", value: { ok: true } }],
        trailingLines: ["Done: yes"],
      }),
    ).toBe('Report\n\nA: 1\n\n=== Conversation ===\nx\ny\n\nCompletion: {"ok":true}\n\nDone: yes')
  })
})
