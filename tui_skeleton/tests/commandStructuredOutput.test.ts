import { describe, expect, it } from "vitest"
import { resolveStructuredOutput } from "../src/commands/commandStructuredOutput.js"

describe("commandStructuredOutput", () => {
  it("renders json mode", () => {
    expect(resolveStructuredOutput({ mode: "json", jsonValue: { ok: true }, summaryText: "ignored" })).toBe(
      JSON.stringify({ ok: true }, null, 2),
    )
  })

  it("renders yaml mode", () => {
    expect(
      resolveStructuredOutput({
        mode: "yaml",
        jsonValue: { ok: true },
        summaryText: "ignored",
        yamlText: "a: 1\n",
      }),
    ).toBe("a: 1\n")
  })

  it("renders summary mode", () => {
    expect(resolveStructuredOutput({ mode: "summary", jsonValue: { ok: true }, summaryText: "summary" })).toBe(
      "summary",
    )
  })
})
