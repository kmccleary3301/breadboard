import type { ActivityPrimary } from "../../repl/types.js"

export interface ActivityTransitionTrace {
  readonly at: number
  readonly from: ActivityPrimary
  readonly to: ActivityPrimary
  readonly eventType?: string | null
  readonly source?: string | null
  readonly reason: "applied" | "disabled" | "same" | "hysteresis" | "illegal"
  readonly applied: boolean
}

const formatAt = (value: number): string => {
  if (!Number.isFinite(value)) return "n/a"
  return new Date(value).toISOString().slice(11, 23)
}

const formatReason = (trace: ActivityTransitionTrace): string =>
  trace.applied ? "applied" : `blocked:${trace.reason}`

export const formatActivityTransitionTimeline = (
  traces: ReadonlyArray<ActivityTransitionTrace>,
  maxEntries = 12,
): string => {
  if (!traces || traces.length === 0) return "(no transitions captured)"
  const slice = traces.slice(-Math.max(1, maxEntries))
  return slice
    .map(
      (trace, idx) =>
        `${String(idx + 1).padStart(2, "0")}. ${formatAt(trace.at)} ${trace.from} -> ${trace.to} [${formatReason(trace)}]${trace.eventType ? ` event=${trace.eventType}` : ""}${trace.source ? ` source=${trace.source}` : ""}`,
    )
    .join("\n")
}
