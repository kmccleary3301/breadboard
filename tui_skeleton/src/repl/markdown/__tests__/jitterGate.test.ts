import { describe, expect, it } from "vitest"
import { evaluateJitterGate, renderJitterGateJson } from "../jitterGate.js"

describe("jitterGate", () => {
  it("passes when candidate does not exceed configured jitter deltas", () => {
    const baselineFrames = [
      "a\nb\nc",
      "a\nb-updated\nc",
    ]
    const candidateFrames = [
      "a\nb\nc",
      "a\nb\nc\nd",
    ]
    const result = evaluateJitterGate(baselineFrames, candidateFrames)
    expect(result.ok).toBe(true)
    expect(result.summary).toBe("ok")
  })

  it("fails when candidate exceeds thresholded jitter deltas", () => {
    const baselineFrames = [
      "x\ny\nz",
      "x\ny\nz\nw",
    ]
    const candidateFrames = [
      "x\ny\nz",
      "x\nY\nz",
    ]
    const result = evaluateJitterGate(baselineFrames, candidateFrames, {
      maxPrefixChurnDelta: 0,
      maxReflowDelta: 0,
    })
    expect(result.ok).toBe(false)
    expect(result.summary).toContain("reflow-delta")
  })

  it("emits machine-readable JSON output", () => {
    const result = evaluateJitterGate(["one", "one\ntwo"], ["one", "one\ntwo\nthree"])
    const json = renderJitterGateJson(result)
    expect(() => JSON.parse(json)).not.toThrow()
    const parsed = JSON.parse(json)
    expect(parsed.ok).toBe(result.ok)
  })
})
