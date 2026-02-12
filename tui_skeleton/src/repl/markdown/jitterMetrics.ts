export interface JitterMetrics {
  readonly frameCount: number
  readonly transitions: number
  readonly prefixChurn: number
  readonly tailChurn: number
  readonly reflowCount: number
}

export interface JitterComparison {
  readonly baseline: JitterMetrics
  readonly candidate: JitterMetrics
  readonly deltaPrefixChurn: number
  readonly deltaTailChurn: number
  readonly deltaReflowCount: number
  readonly improved: boolean
}

const splitLines = (text: string): string[] => text.split(/\r?\n/)

const firstDiffIndex = (a: string[], b: string[]): number => {
  const max = Math.min(a.length, b.length)
  for (let i = 0; i < max; i += 1) {
    if (a[i] !== b[i]) return i
  }
  return a.length === b.length ? -1 : max
}

export const extractJitterMetrics = (frames: ReadonlyArray<string>): JitterMetrics => {
  if (frames.length <= 1) {
    return {
      frameCount: frames.length,
      transitions: Math.max(0, frames.length - 1),
      prefixChurn: 0,
      tailChurn: 0,
      reflowCount: 0,
    }
  }

  let prefixChurn = 0
  let tailChurn = 0
  let reflowCount = 0

  for (let i = 1; i < frames.length; i += 1) {
    const prev = splitLines(frames[i - 1] ?? "")
    const next = splitLines(frames[i] ?? "")
    const diffAt = firstDiffIndex(prev, next)
    if (diffAt === -1) continue

    const minLen = Math.min(prev.length, next.length)
    const prevTailIndex = Math.max(0, prev.length - 1)
    const changedBeforeTail = diffAt < Math.min(prevTailIndex, minLen)
    if (changedBeforeTail) {
      reflowCount += 1
      prefixChurn += Math.max(1, minLen - diffAt - 1)
    }

    const nextTailDelta = Math.max(0, next.length - Math.max(diffAt, 0))
    tailChurn += nextTailDelta
  }

  return {
    frameCount: frames.length,
    transitions: frames.length - 1,
    prefixChurn,
    tailChurn,
    reflowCount,
  }
}

export const renderJitterMetricsJson = (metrics: JitterMetrics): string =>
  JSON.stringify(metrics, null, 2)

export const compareJitterMetrics = (
  baselineFrames: ReadonlyArray<string>,
  candidateFrames: ReadonlyArray<string>,
): JitterComparison => {
  const baseline = extractJitterMetrics(baselineFrames)
  const candidate = extractJitterMetrics(candidateFrames)
  const deltaPrefixChurn = candidate.prefixChurn - baseline.prefixChurn
  const deltaTailChurn = candidate.tailChurn - baseline.tailChurn
  const deltaReflowCount = candidate.reflowCount - baseline.reflowCount
  const improved =
    candidate.prefixChurn <= baseline.prefixChurn &&
    candidate.reflowCount <= baseline.reflowCount &&
    (candidate.prefixChurn < baseline.prefixChurn || candidate.reflowCount < baseline.reflowCount)
  return {
    baseline,
    candidate,
    deltaPrefixChurn,
    deltaTailChurn,
    deltaReflowCount,
    improved,
  }
}

export const renderJitterComparisonJson = (comparison: JitterComparison): string =>
  JSON.stringify(comparison, null, 2)
