import type { WorkGraphState } from "../../../types.js"

export interface SubagentStripCounts {
  readonly running: number
  readonly completed: number
  readonly failed: number
  readonly blocked: number
}

export interface SubagentStripSummary {
  readonly headline: string
  readonly detail?: string
  readonly tone: "info" | "success" | "error"
  readonly counts: SubagentStripCounts
}

export interface SubagentStripLifecycleState {
  readonly rendered: SubagentStripSummary | null
  readonly visibleUntilMs: number
  readonly lastUpdatedMs: number
}

export interface SubagentStripLifecycleInput {
  readonly summary: SubagentStripSummary | null
  readonly nowMs: number
  readonly idleCooldownMs: number
  readonly minUpdateMs: number
}

export interface SubagentStripChurnMetrics {
  readonly samples: number
  readonly transitions: number
  readonly ratio: number
}

const normalizeNonNegativeInt = (value: number, fallback: number): number => {
  if (!Number.isFinite(value)) return fallback
  return Math.max(0, Math.floor(value))
}

const countsEqual = (left: SubagentStripCounts, right: SubagentStripCounts): boolean =>
  left.running === right.running &&
  left.completed === right.completed &&
  left.failed === right.failed &&
  left.blocked === right.blocked

const summaryEqual = (left: SubagentStripSummary | null, right: SubagentStripSummary | null): boolean => {
  if (left === right) return true
  if (!left || !right) return false
  return (
    left.headline === right.headline &&
    left.detail === right.detail &&
    left.tone === right.tone &&
    countsEqual(left.counts, right.counts)
  )
}

const summarySignature = (value: SubagentStripSummary | null): string =>
  value
    ? `${value.headline}|${value.detail ?? ""}|${value.tone}|${value.counts.running}/${value.counts.completed}/${value.counts.failed}/${value.counts.blocked}`
    : "__none__"

export const createSubagentStripLifecycleState = (): SubagentStripLifecycleState => ({
  rendered: null,
  visibleUntilMs: 0,
  lastUpdatedMs: 0,
})

export const evaluateSubagentStripChurn = (
  samples: ReadonlyArray<SubagentStripSummary | null>,
): SubagentStripChurnMetrics => {
  if (samples.length <= 1) {
    return {
      samples: samples.length,
      transitions: 0,
      ratio: 0,
    }
  }
  let transitions = 0
  let previous = summarySignature(samples[0] ?? null)
  for (let index = 1; index < samples.length; index += 1) {
    const current = summarySignature(samples[index] ?? null)
    if (current !== previous) transitions += 1
    previous = current
  }
  const denominator = Math.max(1, samples.length - 1)
  return {
    samples: samples.length,
    transitions,
    ratio: transitions / denominator,
  }
}

export const buildSubagentStripSummary = (workGraph: WorkGraphState | null | undefined): SubagentStripSummary | null => {
  const order = Array.isArray(workGraph?.itemOrder) ? workGraph.itemOrder : []
  if (order.length === 0) return null
  let running = 0
  let completed = 0
  let failed = 0
  let blocked = 0
  let lead: { status?: string; title?: string } | null = null
  for (const workId of order) {
    const item = workGraph?.itemsById?.[workId]
    if (!item) continue
    if (!lead) lead = item
    if (item.status === "running") running += 1
    else if (item.status === "completed") completed += 1
    else if (item.status === "failed") failed += 1
    else if (item.status === "blocked") blocked += 1
  }
  const tone = failed > 0 ? "error" : running > 0 ? "info" : "success"
  return {
    headline: `subagents ${running} run · ${completed} done · ${failed} fail · ${blocked} blocked`,
    detail: lead ? `${lead.status ?? "pending"} · ${lead.title ?? "task"}` : undefined,
    tone,
    counts: { running, completed, failed, blocked },
  }
}

export const reduceSubagentStripLifecycle = (
  previous: SubagentStripLifecycleState,
  input: SubagentStripLifecycleInput,
): SubagentStripLifecycleState => {
  const nowMs = Number.isFinite(input.nowMs) ? input.nowMs : Date.now()
  const idleCooldownMs = normalizeNonNegativeInt(input.idleCooldownMs, 0)
  const minUpdateMs = normalizeNonNegativeInt(input.minUpdateMs, 0)
  const summary = input.summary
  const hasRunning = Boolean(summary && summary.counts.running > 0)
  const nextVisibleUntil = hasRunning ? nowMs + idleCooldownMs : previous.visibleUntilMs

  if (!summary) {
    if (previous.rendered && nowMs <= previous.visibleUntilMs) {
      return {
        ...previous,
        visibleUntilMs: nextVisibleUntil,
      }
    }
    return {
      rendered: null,
      visibleUntilMs: nextVisibleUntil,
      lastUpdatedMs: previous.lastUpdatedMs,
    }
  }

  const visible = hasRunning || nowMs <= nextVisibleUntil
  if (!visible) {
    return {
      rendered: null,
      visibleUntilMs: nextVisibleUntil,
      lastUpdatedMs: previous.lastUpdatedMs,
    }
  }

  const withinCadenceWindow = nowMs - previous.lastUpdatedMs < minUpdateMs
  const changed = !summaryEqual(previous.rendered, summary)
  if (withinCadenceWindow && previous.rendered && changed) {
    return {
      ...previous,
      visibleUntilMs: nextVisibleUntil,
    }
  }

  if (previous.rendered && !changed) {
    return {
      ...previous,
      visibleUntilMs: nextVisibleUntil,
    }
  }

  return {
    rendered: summary,
    visibleUntilMs: nextVisibleUntil,
    lastUpdatedMs: nowMs,
  }
}
