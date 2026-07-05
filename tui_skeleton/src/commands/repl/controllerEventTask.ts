import type { SessionEvent } from "../../api/types.js"
import { BRAND_COLORS, SEMANTIC_COLORS } from "../../repl/designSystem.js"
import { stripAnsi } from "../../repl/stringUtils.js"
import type { RuntimeBehaviorFlags } from "../../repl/types.js"
import { extractString, isRecord } from "./controllerUtils.js"
import { resolveSubagentUiPolicy, type SubagentUiPolicy } from "./subagentUiPolicy.js"

type ToolStatus = "pending" | "success" | "error"
type LiveSlotStatus = ToolStatus

type TaskEventOptions = {
  readonly eventType?: string
  readonly eventId?: string | null
  readonly seq?: number | null
  readonly timestamp?: number | null
}

type ActivityTransition = {
  readonly to?: string
  readonly eventType?: string
  readonly source?: string
}

type TaskEventController = {
  readonly runtimeFlags?: RuntimeBehaviorFlags
  readonly clock?: { readonly now?: () => number }
  readonly subagentToastLedger: Map<string, { status: string; at: number }>
  noteStopRequested(): void
  setActivityStatus?: (message: string, transition?: ActivityTransition) => void
  addTool(kind: string, text: string, status?: ToolStatus): void
  handleTaskEvent(payload: Record<string, unknown>, options?: TaskEventOptions): void
  upsertLiveSlot?: (
    id: string,
    text: string,
    color?: string,
    status?: LiveSlotStatus,
    ttlMs?: number,
    eventType?: string,
  ) => void
}

const clockNow = (controller: TaskEventController): number => controller.clock?.now?.() ?? Date.now()

const normalizeSubagentStatus = (rawStatus: string): "running" | "completed" | "failed" | "blocked" | "cancelled" | "pending" => {
  const seed = rawStatus.trim().toLowerCase()
  if (!seed) return "pending"
  if (seed.includes("complete") || seed.includes("done") || seed.includes("success")) return "completed"
  if (seed.includes("fail") || seed.includes("error") || seed.includes("timeout")) return "failed"
  if (seed.includes("block") || seed.includes("wait")) return "blocked"
  if (seed.includes("cancel") || seed.includes("stop")) return "cancelled"
  if (seed.includes("run") || seed.includes("progress") || seed.includes("spawn") || seed.includes("start")) return "running"
  return "pending"
}

const resolveSubagentStatus = (payload: Record<string, unknown>, fallback?: string): string => {
  const status = extractString(payload, ["status", "state", "kind", "event", "type"]) ?? fallback ?? "update"
  return status.trim() || "update"
}

const sanitizeSubagentPreview = (value: string, maxChars = 120): string => {
  const cleaned = stripAnsi(value)
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
  if (!cleaned) return "task"
  if (cleaned.length <= maxChars) return cleaned
  return `${cleaned.slice(0, Math.max(1, maxChars - 3))}...`
}

export const emitSubagentToast = (
  controller: TaskEventController,
  payload: Record<string, unknown>,
  eventType: string,
  policy: SubagentUiPolicy,
): void => {
  if (!policy.routeTaskEventsToLiveSlots) return
  const rawTaskId = extractString(payload, ["task_id", "taskId", "id"]) ?? "task"
  const taskId = sanitizeSubagentPreview(rawTaskId, 64)
  const statusRaw = resolveSubagentStatus(payload)
  const status = normalizeSubagentStatus(statusRaw)
  const rawDescription =
    extractString(payload, ["description", "title", "summary", "prompt", "subagent_type", "subagentType"]) ?? taskId
  const description = sanitizeSubagentPreview(rawDescription, 120)
  const now = clockNow(controller)
  const key = `subagent:${taskId}`
  const ledger = controller.subagentToastLedger
  const last = ledger.get(key)
  if (last && last.status === status && now - last.at < policy.toastMergeWindowMs) return
  ledger.set(key, { status, at: now })
  if (ledger.size > 256) {
    const stale = ledger.keys().next().value
    if (stale) ledger.delete(stale)
  }
  const statusLabel =
    status === "completed"
      ? "completed"
      : status === "failed"
        ? "failed"
        : status === "blocked"
          ? "blocked"
          : status === "cancelled"
            ? "cancelled"
            : status === "running"
              ? "running"
              : statusRaw
  const slotStatus = status === "completed" ? "success" : status === "failed" ? "error" : "pending"
  const color =
    status === "completed"
      ? SEMANTIC_COLORS.success
      : status === "failed"
        ? SEMANTIC_COLORS.error
        : status === "blocked"
          ? BRAND_COLORS.duneOrange
          : BRAND_COLORS.railBlue
  const ttl = status === "failed" ? policy.toastErrorTtlMs : policy.toastTtlMs
  controller.upsertLiveSlot?.(
    key,
    `[subagent] ${statusLabel} · ${description}`,
    color,
    slotStatus,
    ttl,
    eventType,
  )
}

export function applyTaskEvent(this: TaskEventController, event: SessionEvent, nextSeq: number | null): void {
  const payload = isRecord(event.payload) ? event.payload : {}
  // applyEvent historically tolerates absent runtime flags; policy reads missing fields as falsy.
  const runtimeFlags = (this.runtimeFlags ?? {}) as RuntimeBehaviorFlags
  const policy = resolveSubagentUiPolicy(runtimeFlags, process.env)
  const taskId = extractString(payload, ["task_id", "id"])
  const status = extractString(payload, ["status", "state"]) ?? "update"
  const statusLower = status.toLowerCase()
  if (statusLower.includes("stop")) {
    this.noteStopRequested()
    this.setActivityStatus?.("Stopping…", { to: "cancelled", eventType: event.type, source: "event" })
  }
  const description = extractString(payload, ["description", "title", "prompt"])
  const lineParts = [status, description, taskId].filter(Boolean)
  const line = lineParts.length > 0 ? lineParts.join(" · ") : JSON.stringify(payload)
  const isError = typeof payload.error === "string" && payload.error.trim().length > 0
  const isComplete = status.toLowerCase().includes("complete") || status.toLowerCase().includes("done")
  if (policy.routeTaskEventsToToolRail) {
    this.addTool("status", `[task] ${line}`, isError ? "error" : isComplete ? "success" : "pending")
  }
  this.handleTaskEvent(payload, {
    eventType: event.type,
    eventId: event.id,
    seq: nextSeq,
    timestamp: event.timestamp,
  })
  emitSubagentToast(this, payload, event.type, policy)
}
