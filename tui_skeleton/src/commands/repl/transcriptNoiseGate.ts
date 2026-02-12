export interface TranscriptNoiseMetrics {
  readonly canonicalChars: number
  readonly noiseChars: number
  readonly ratio: number
}

export interface TranscriptNoiseGateResult {
  readonly ok: boolean
  readonly threshold: number
  readonly metrics: TranscriptNoiseMetrics
  readonly summary: string
}

const NON_CANONICAL_SPEAKERS = new Set(["system", "tool"])

const safeString = (value: unknown): string => (typeof value === "string" ? value : "")

export const collectTranscriptNoiseMetrics = (state: any): TranscriptNoiseMetrics => {
  const canonicalChars = (Array.isArray(state?.conversation) ? state.conversation : [])
    .filter((entry: any) => !NON_CANONICAL_SPEAKERS.has(safeString(entry?.speaker)))
    .reduce((sum: number, entry: any) => sum + safeString(entry?.text).length, 0)

  const toolNoiseChars = (Array.isArray(state?.toolEvents) ? state.toolEvents : []).reduce(
    (sum: number, entry: any) => sum + safeString(entry?.text).length,
    0,
  )
  const hintNoiseChars = (Array.isArray(state?.hints) ? state.hints : []).reduce(
    (sum: number, hint: any) => sum + safeString(hint).length,
    0,
  )
  const noiseChars = toolNoiseChars + hintNoiseChars
  const ratio = noiseChars / Math.max(1, canonicalChars)
  return { canonicalChars, noiseChars, ratio }
}

export const evaluateTranscriptNoiseGate = (
  state: any,
  threshold = 0.8,
): TranscriptNoiseGateResult => {
  const safeThreshold = Number.isFinite(threshold) ? Math.max(0, threshold) : 0.8
  const metrics = collectTranscriptNoiseMetrics(state)
  const ok = metrics.ratio <= safeThreshold
  const summary = ok ? "ok" : `ratio:${metrics.ratio.toFixed(3)}`
  return {
    ok,
    threshold: safeThreshold,
    metrics,
    summary,
  }
}

export const renderTranscriptNoiseGateJson = (result: TranscriptNoiseGateResult): string =>
  JSON.stringify(result, null, 2)
