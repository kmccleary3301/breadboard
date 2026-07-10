import { describe, expect, it } from "vitest"
import { resolveCommandResultEnvelope } from "../src/commands/commandResultEnvelope.js"

describe("commandResultEnvelope", () => {
  it("resolves summary mode as text", () => {
    expect(
      resolveCommandResultEnvelope({
        mode: "summary",
        envelope: { jsonValue: { ok: true }, text: "summary" },
      }),
    ).toEqual({ kind: "text", value: "summary" })
  })

  it("resolves yaml mode as yaml text", () => {
    expect(
      resolveCommandResultEnvelope({
        mode: "yaml",
        envelope: { jsonValue: { ok: true }, text: "summary", yamlText: "a: 1\n" },
      }),
    ).toEqual({ kind: "yaml", value: "a: 1\n" })
  })

  it("resolves json mode through shared command presentation", () => {
    expect(
      resolveCommandResultEnvelope({
        mode: "json",
        envelope: { jsonValue: { ok: true }, text: "summary" },
      }),
    ).toEqual({ kind: "json", value: JSON.stringify({ ok: true }, null, 2) })
  })
})
