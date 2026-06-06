import { describe, expect, it } from "vitest"
import { renderLabeledLine, renderOptionalJsonBlock, renderSectionBlock } from "../src/commands/commandText.js"

describe("commandText", () => {
  it("renders labeled lines", () => {
    expect(renderLabeledLine("Status", "ok")).toBe("Status: ok")
  })

  it("renders titled sections with body lines", () => {
    expect(renderSectionBlock("Conversation", ["a", "b"])) .toBe("=== Conversation ===\na\nb")
  })

  it("renders titled sections with empty fallback", () => {
    expect(renderSectionBlock("Conversation", [], "(empty)")).toBe("=== Conversation ===\n(empty)")
  })

  it("renders optional json block only when value exists", () => {
    expect(renderOptionalJsonBlock("Completion", { ok: true })).toBe('Completion: {"ok":true}')
    expect(renderOptionalJsonBlock("Completion", null)).toBeNull()
  })
})
