import { describe, expect, it } from "vitest"
import { buildFocusLatencyMetrics, evaluateFocusLatencyGate, percentile } from "../focusLatency.js"

describe("focusLatency", () => {
  it("computes percentile with stable ceiling index behavior", () => {
    expect(percentile([], 0.95)).toBe(0)
    expect(percentile([5], 0.95)).toBe(5)
    expect(percentile([1, 2, 3, 4, 5], 0.95)).toBe(5)
    expect(percentile([1, 2, 3, 4, 5], 0.5)).toBe(3)
  })

  it("builds p95 metrics from finite sample sets", () => {
    const metrics = buildFocusLatencyMetrics([4, 5, Number.NaN, 9], [2, 3, 8, Infinity])
    expect(metrics.openSamples).toEqual([4, 5, 9])
    expect(metrics.switchSamples).toEqual([2, 3, 8])
    expect(metrics.openP95Ms).toBe(9)
    expect(metrics.switchP95Ms).toBe(8)
  })

  it("passes when both open and switch p95 are under thresholds", () => {
    const result = evaluateFocusLatencyGate([8, 10, 12], [5, 6, 7], {
      openP95Ms: 20,
      switchP95Ms: 10,
    })
    expect(result.ok).toBe(true)
    expect(result.failures).toEqual([])
  })

  it("fails with explicit diagnostics when p95 exceeds thresholds", () => {
    const result = evaluateFocusLatencyGate([8, 10, 35], [5, 6, 22], {
      openP95Ms: 20,
      switchP95Ms: 10,
    })
    expect(result.ok).toBe(false)
    expect(result.failures.length).toBe(2)
    expect(result.failures[0]).toContain("openP95")
    expect(result.failures[1]).toContain("switchP95")
  })
})
