import { describe, expect, it } from "vitest"
import { resolveCommandPresentation, renderCompletionBlock } from "../src/commands/commandPresentation.js"

describe("commandPresentation", () => {
  it("renders completion blocks", () => {
    expect(renderCompletionBlock({ ok: true })).toBe("\n---\nCompletion: {\"ok\":true}")
  })

  it("resolves json output in json mode", () => {
    expect(resolveCommandPresentation({ mode: "json", jsonValue: { ok: true }, text: "ignored" })).toEqual({
      kind: "json",
      value: JSON.stringify({ ok: true }, null, 2),
    })
  })

  it("resolves text output in text-like modes", () => {
    expect(resolveCommandPresentation({ mode: "text", jsonValue: { ok: true }, text: "hello" })).toEqual({
      kind: "text",
      value: "hello",
    })
  })

  it("resolves empty text output to none in text-like modes", () => {
    expect(resolveCommandPresentation({ mode: "table", jsonValue: { ok: true }, text: "" })).toEqual({
      kind: "none",
      value: "",
    })
  })
})
