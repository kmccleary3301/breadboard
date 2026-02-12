import { describe, expect, it } from "vitest"
import {
  compareJitterMetrics,
  extractJitterMetrics,
  renderJitterComparisonJson,
  renderJitterMetricsJson,
} from "../jitterMetrics.js"

describe("jitterMetrics", () => {
  it("reports zero prefix churn and reflow for append-only tail growth", () => {
    const frames = [
      "line-1\nline-2",
      "line-1\nline-2\nline-3",
      "line-1\nline-2\nline-3\nline-4",
    ]
    const metrics = extractJitterMetrics(frames)
    expect(metrics.transitions).toBe(2)
    expect(metrics.prefixChurn).toBe(0)
    expect(metrics.reflowCount).toBe(0)
    expect(metrics.tailChurn).toBeGreaterThan(0)
  })

  it("detects reflow when prefix lines are rewritten mid-stream", () => {
    const frames = [
      "a\nb\nc",
      "a\nB-UPDATED\nc",
      "a\nB-UPDATED\nc\nd",
    ]
    const metrics = extractJitterMetrics(frames)
    expect(metrics.reflowCount).toBeGreaterThan(0)
    expect(metrics.prefixChurn).toBeGreaterThan(0)
  })

  it("emits machine-readable JSON output", () => {
    const metrics = extractJitterMetrics(["alpha", "alpha\nbeta"])
    const json = renderJitterMetricsJson(metrics)
    expect(() => JSON.parse(json)).not.toThrow()
    const parsed = JSON.parse(json)
    expect(parsed.tailChurn).toBe(metrics.tailChurn)
  })

  it("compares baseline and candidate jitter and marks improvements", () => {
    const baselineFrames = [
      "a\nb\nc",
      "a\nb-updated\nc",
      "a\nb-updated\nc\nd",
    ]
    const candidateFrames = [
      "a\nb\nc",
      "a\nb\nc\nd",
      "a\nb\nc\nd\ne",
    ]
    const comparison = compareJitterMetrics(baselineFrames, candidateFrames)
    expect(comparison.improved).toBe(true)
    expect(comparison.deltaPrefixChurn).toBeLessThanOrEqual(0)
    expect(comparison.deltaReflowCount).toBeLessThanOrEqual(0)
  })

  it("marks non-improvement when candidate has more reflow", () => {
    const baselineFrames = [
      "a\nb\nc",
      "a\nb\nc\nd",
    ]
    const candidateFrames = [
      "a\nb\nc",
      "a\nB\nc",
    ]
    const comparison = compareJitterMetrics(baselineFrames, candidateFrames)
    expect(comparison.improved).toBe(false)
    expect(comparison.deltaReflowCount).toBeGreaterThanOrEqual(0)
  })

  it("emits machine-readable JSON output for comparisons", () => {
    const comparison = compareJitterMetrics(["one", "one\ntwo"], ["one", "one\ntwo\nthree"])
    const json = renderJitterComparisonJson(comparison)
    expect(() => JSON.parse(json)).not.toThrow()
    const parsed = JSON.parse(json)
    expect(parsed.candidate.tailChurn).toBe(comparison.candidate.tailChurn)
  })
})
