import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { computeProjectionHash } from "./projectionHash"

const mkEvent = (payload: Record<string, unknown>): SessionEvent => ({
  id: "e1",
  type: "tool_result",
  session_id: "s1",
  turn: null,
  timestamp: 100,
  seq: 1,
  payload,
})

describe("computeProjectionHash", () => {
  it("is stable for equivalent payloads with different key order", async () => {
    const a = [mkEvent({ b: 2, a: 1, nested: { y: 2, x: 1 } })]
    const b = [mkEvent({ nested: { x: 1, y: 2 }, a: 1, b: 2 })]
    const hashA = await computeProjectionHash(a)
    const hashB = await computeProjectionHash(b)
    expect(hashA).toBe(hashB)
  })

  it("changes when event payload changes", async () => {
    const a = [mkEvent({ value: 1 })]
    const b = [mkEvent({ value: 2 })]
    const hashA = await computeProjectionHash(a)
    const hashB = await computeProjectionHash(b)
    expect(hashA).not.toBe(hashB)
  })
})
