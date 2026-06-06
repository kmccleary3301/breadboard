import { describe, expect, it } from "vitest"
import type { SessionEvent } from "../src/api/types.js"
import {
  defaultShouldRenderOneShotEvent,
  normalizeOneShotOutputMode,
} from "../src/commands/oneShotCommandRuntime.js"

describe("oneShotCommandRuntime", () => {
  it("normalizes output mode to text/json", () => {
    expect(normalizeOneShotOutputMode("json")).toBe("json")
    expect(normalizeOneShotOutputMode("text")).toBe("text")
    expect(normalizeOneShotOutputMode("other")).toBe("text")
  })

  it("suppresses completion events in the default render policy", () => {
    const completion = { type: "completion", payload: {} } as SessionEvent
    const assistant = { type: "assistant_message", payload: { text: "hello" } } as SessionEvent
    expect(defaultShouldRenderOneShotEvent(completion)).toBe(false)
    expect(defaultShouldRenderOneShotEvent(assistant)).toBe(true)
  })
})
