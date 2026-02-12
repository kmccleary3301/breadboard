import { compareJitterMetrics, type JitterComparison } from "./jitterMetrics.js"

export interface JitterGateThresholds {
  readonly maxPrefixChurnDelta: number
  readonly maxReflowDelta: number
}

export interface JitterGateResult {
  readonly ok: boolean
  readonly thresholds: JitterGateThresholds
  readonly comparison: JitterComparison
  readonly summary: string
}

const DEFAULT_THRESHOLDS: JitterGateThresholds = {
  maxPrefixChurnDelta: 0,
  maxReflowDelta: 0,
}

export const evaluateJitterGate = (
  baselineFrames: ReadonlyArray<string>,
  candidateFrames: ReadonlyArray<string>,
  thresholds: Partial<JitterGateThresholds> = {},
): JitterGateResult => {
  const resolvedThresholds: JitterGateThresholds = {
    maxPrefixChurnDelta:
      typeof thresholds.maxPrefixChurnDelta === "number" && Number.isFinite(thresholds.maxPrefixChurnDelta)
        ? thresholds.maxPrefixChurnDelta
        : DEFAULT_THRESHOLDS.maxPrefixChurnDelta,
    maxReflowDelta:
      typeof thresholds.maxReflowDelta === "number" && Number.isFinite(thresholds.maxReflowDelta)
        ? thresholds.maxReflowDelta
        : DEFAULT_THRESHOLDS.maxReflowDelta,
  }
  const comparison = compareJitterMetrics(baselineFrames, candidateFrames)
  const ok =
    comparison.deltaPrefixChurn <= resolvedThresholds.maxPrefixChurnDelta &&
    comparison.deltaReflowCount <= resolvedThresholds.maxReflowDelta
  const summary = ok
    ? "ok"
    : `prefix-delta:${comparison.deltaPrefixChurn}|reflow-delta:${comparison.deltaReflowCount}`
  return {
    ok,
    thresholds: resolvedThresholds,
    comparison,
    summary,
  }
}

export const renderJitterGateJson = (result: JitterGateResult): string =>
  JSON.stringify(result, null, 2)
