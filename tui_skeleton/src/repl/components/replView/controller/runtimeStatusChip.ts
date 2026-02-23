import type { ActivitySnapshot } from "../../../types.js"

export const DEFAULT_STATUS_CHIP_HYSTERESIS_MS = 240

export type RuntimeStatusChip = {
  readonly id: string
  readonly label: string
  readonly tone: "info" | "success" | "warning" | "error"
  readonly priority: number
  readonly updatedAt: number
}

export type RuntimeStatusTransitionDecision =
  | { readonly kind: "noop" }
  | { readonly kind: "set"; readonly chip: RuntimeStatusChip }
  | { readonly kind: "schedule"; readonly chip: RuntimeStatusChip; readonly delayMs: number }

const isCriticalRuntimeStatus = (chip: RuntimeStatusChip): boolean => {
  if (chip.tone === "error") return true
  return ["disconnected", "error", "halted", "permission", "reconnecting"].includes(chip.id)
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
