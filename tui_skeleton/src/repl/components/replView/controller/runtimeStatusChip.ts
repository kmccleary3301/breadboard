import type { ActivitySnapshot } from "../../../types.js"

export const DEFAULT_STATUS_CHIP_HYSTERESIS_MS = 240

export type RuntimeStatusChip = {
  readonly id: string
  readonly label: string
  readonly tone: "info" | "success" | "warning" | "error"
  readonly priority: number
  readonly updatedAt: number
}

export type PhaseLineTone = RuntimeStatusChip["tone"] | "muted"

export type PhaseLineState = {
  readonly id: string
  readonly label: string
  readonly tone: PhaseLineTone
}

export type RuntimeStatusTransitionDecision =
  | { readonly kind: "noop" }
  | { readonly kind: "set"; readonly chip: RuntimeStatusChip }
  | { readonly kind: "schedule"; readonly chip: RuntimeStatusChip; readonly delayMs: number }

const isCriticalRuntimeStatus = (chip: RuntimeStatusChip): boolean => {
  if (chip.tone === "error") return true
  return ["disconnected", "error", "halted", "interrupted", "permission", "reconnecting"].includes(chip.id)
}

const CANONICAL_PHASE_LABELS: Record<string, PhaseLineState> = {
  disconnected: { id: "disconnected", label: "disconnected", tone: "error" },
  error: { id: "error", label: "error", tone: "error" },
  halted: { id: "halted", label: "halted", tone: "error" },
  interrupted: { id: "interrupted", label: "interrupted", tone: "error" },
  permission: { id: "permission", label: "permission", tone: "warning" },
  reconnecting: { id: "reconnecting", label: "reconnecting", tone: "warning" },
  tool: { id: "tool", label: "tool", tone: "warning" },
  done: { id: "done", label: "done", tone: "success" },
  responding: { id: "responding", label: "responding", tone: "info" },
  thinking: { id: "thinking", label: "thinking", tone: "info" },
  run: { id: "run", label: "run", tone: "info" },
  ready: { id: "ready", label: "ready", tone: "muted" },
  starting: { id: "starting", label: "starting", tone: "info" },
}

const normalizePhaseLabel = (raw: string): string =>
  raw
    .trim()
    .toLowerCase()
    .replace(/[.\u2026]+$/g, "")
    .replace(/\s+/g, " ")

const canonicalFromStatus = (status: string): PhaseLineState => {
  const normalized = normalizePhaseLabel(status)
  if (!normalized || normalized === "ready" || normalized === "idle") return CANONICAL_PHASE_LABELS.ready
  if (normalized.includes("reconnect")) return CANONICAL_PHASE_LABELS.reconnecting
  if (normalized.includes("error") || normalized.includes("fail")) return CANONICAL_PHASE_LABELS.error
  if (normalized.includes("interrupt")) return CANONICAL_PHASE_LABELS.interrupted
  if (normalized.includes("halt")) return CANONICAL_PHASE_LABELS.halted
  if (normalized.includes("finish") || normalized.includes("complete") || normalized.includes("done")) {
    return CANONICAL_PHASE_LABELS.done
  }
  if (normalized.includes("respond")) return CANONICAL_PHASE_LABELS.responding
  if (normalized.includes("think")) return CANONICAL_PHASE_LABELS.thinking
  if (normalized.includes("start") || normalized.includes("launch") || normalized.includes("init")) {
    return CANONICAL_PHASE_LABELS.starting
  }
  return { id: normalized, label: normalized, tone: "info" }
}

export const mapRuntimePhaseLineState = (input: {
  readonly disconnected: boolean
  readonly pendingResponse: boolean
  readonly status: string
  readonly runtimeStatusChip: RuntimeStatusChip | null
}): PhaseLineState => {
  if (input.disconnected) return CANONICAL_PHASE_LABELS.disconnected

  const chip = input.runtimeStatusChip
  if (chip) {
    const byId = CANONICAL_PHASE_LABELS[String(chip.id ?? "").trim().toLowerCase()]
    if (byId) return byId
    const normalized = normalizePhaseLabel(String(chip.label ?? ""))
    if (normalized) return { id: normalized, label: normalized, tone: chip.tone }
  }

  if (input.pendingResponse) return CANONICAL_PHASE_LABELS.thinking
  return canonicalFromStatus(input.status)
}

export const mapActivityToRuntimeChip = (
  activity: ActivitySnapshot | undefined,
  pendingResponse: boolean,
  disconnected: boolean,
  nowMs: number,
): RuntimeStatusChip | null => {
  if (disconnected) {
    return { id: "disconnected", label: "disconnected", tone: "error", priority: 100, updatedAt: nowMs }
  }
  const primary = activity?.primary ?? (pendingResponse ? "thinking" : "idle")
  switch (primary) {
    case "tool_call":
      return { id: "tool", label: "tool", tone: "warning", priority: 70, updatedAt: nowMs }
    case "permission_required":
      return { id: "permission", label: "permission", tone: "warning", priority: 80, updatedAt: nowMs }
    case "error":
      return { id: "error", label: "error", tone: "error", priority: 95, updatedAt: nowMs }
    case "completed":
      return { id: "done", label: "done", tone: "success", priority: 90, updatedAt: nowMs }
    case "halted":
      return { id: "halted", label: "halted", tone: "error", priority: 96, updatedAt: nowMs }
    case "cancelled":
      return { id: "interrupted", label: "interrupted", tone: "error", priority: 94, updatedAt: nowMs }
    case "responding":
      return { id: "responding", label: "responding", tone: "info", priority: 65, updatedAt: nowMs }
    case "thinking":
      return { id: "thinking", label: "thinking", tone: "info", priority: 60, updatedAt: nowMs }
    case "run":
      return { id: "run", label: "run", tone: "info", priority: 55, updatedAt: nowMs }
    case "reconnecting":
      return { id: "reconnecting", label: "reconnecting", tone: "warning", priority: 85, updatedAt: nowMs }
    default:
      return pendingResponse
        ? { id: "thinking", label: "thinking", tone: "info", priority: 60, updatedAt: nowMs }
        : null
  }
}

export const resolveRuntimeStatusChipTransition = (input: {
  readonly current: RuntimeStatusChip | null
  readonly target: RuntimeStatusChip
  readonly nowMs: number
  readonly hysteresisMs: number
}): RuntimeStatusTransitionDecision => {
  const { current, target, nowMs, hysteresisMs } = input
  if (!current) {
    return { kind: "set", chip: target }
  }

  if (current.id === target.id) {
    const unchanged =
      current.label === target.label && current.tone === target.tone && current.priority === target.priority
    if (unchanged) return { kind: "noop" }
    return {
      kind: "set",
      chip: {
        ...target,
        // Preserve original visible-start timestamp so a relabeled state
        // does not restart dwell timing.
        updatedAt: current.updatedAt,
      },
    }
  }

  if (isCriticalRuntimeStatus(target)) {
    return { kind: "set", chip: target }
  }

  const elapsed = Math.max(0, nowMs - current.updatedAt)
  if (elapsed >= hysteresisMs) {
    return { kind: "set", chip: target }
  }

  return {
    kind: "schedule",
    chip: target,
    delayMs: Math.max(0, hysteresisMs - elapsed),
  }
}
