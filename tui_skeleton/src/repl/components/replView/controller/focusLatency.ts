export interface FocusLatencyThresholds {
  readonly openP95Ms: number
  readonly switchP95Ms: number
}

export interface FocusLatencyMetrics {
  readonly openSamples: number[]
  readonly switchSamples: number[]
  readonly openP95Ms: number
  readonly switchP95Ms: number
}

export interface FocusLatencyGateResult {
  readonly ok: boolean
  readonly thresholds: FocusLatencyThresholds
  readonly metrics: FocusLatencyMetrics
  readonly failures: string[]
}

const toFiniteSamples = (values: ReadonlyArray<number>): number[] =>
  values.filter((value) => Number.isFinite(value) && value >= 0).map((value) => Number(value))

export const percentile = (values: ReadonlyArray<number>, q: number): number => {
  const samples = toFiniteSamples(values).sort((a, b) => a - b)
  if (samples.length === 0) return 0
  const normalizedQ = Number.isFinite(q) ? Math.max(0, Math.min(1, q)) : 1
  if (samples.length === 1) return samples[0]
  const index = Math.ceil(normalizedQ * samples.length) - 1
  return samples[Math.max(0, Math.min(samples.length - 1, index))]
}

export const buildFocusLatencyMetrics = (
  openSamples: ReadonlyArray<number>,
  switchSamples: ReadonlyArray<number>,
): FocusLatencyMetrics => {
  const open = toFiniteSamples(openSamples)
  const laneSwitch = toFiniteSamples(switchSamples)
  return {
    openSamples: open,
    switchSamples: laneSwitch,
    openP95Ms: percentile(open, 0.95),
    switchP95Ms: percentile(laneSwitch, 0.95),
  }
}

export const evaluateFocusLatencyGate = (
  openSamples: ReadonlyArray<number>,
  switchSamples: ReadonlyArray<number>,
  thresholds: FocusLatencyThresholds,
): FocusLatencyGateResult => {
  const metrics = buildFocusLatencyMetrics(openSamples, switchSamples)
  const failures: string[] = []
  if (metrics.openP95Ms > thresholds.openP95Ms) {
    failures.push(`openP95 ${metrics.openP95Ms.toFixed(3)}ms exceeded threshold ${thresholds.openP95Ms.toFixed(3)}ms`)
  }
  if (metrics.switchP95Ms > thresholds.switchP95Ms) {
    failures.push(
      `switchP95 ${metrics.switchP95Ms.toFixed(3)}ms exceeded threshold ${thresholds.switchP95Ms.toFixed(3)}ms`,
    )
  }
  return {
    ok: failures.length === 0,
    thresholds,
    metrics,
    failures,
  }
}
