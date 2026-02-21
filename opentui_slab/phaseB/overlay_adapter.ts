import type { PaletteKind } from "./protocol.ts"

export type OverlayKind = "none" | "palette" | "permission" | "task"

type TaskEntry = {
  status: string
  description?: string
  laneId?: string
  laneLabel?: string
  subagentId?: string
  subagentLabel?: string
  parentSessionId?: string
  childSessionId?: string
}

export type SubagentGraphEntry = {
  id: string
  label: string
  parentSessionId: string | null
  childSessionId: string | null
  taskCount: number
  runningCount: number
  completedCount: number
  failedCount: number
}

export type OverlayAdapterState = {
  readonly activeKind: OverlayKind
  readonly paletteKind: PaletteKind | null
  readonly permissionRequestId: string | null
  readonly taskById: Readonly<Record<string, TaskEntry>>
  readonly taskRunningCount: number
  readonly taskCompletedCount: number
  readonly taskFailedCount: number
  readonly taskTotalCount: number
  readonly taskLaneCounts: Readonly<Record<string, number>>
  readonly taskActiveLanes: number
  readonly subagentById: Readonly<Record<string, SubagentGraphEntry>>
  readonly subagentOrder: ReadonlyArray<string>
  readonly lastTaskSummary: string | null
}

export type OverlayAdapterAction =
  | { type: "ui.palette.open"; kind: PaletteKind }
  | { type: "ui.palette.close" }
  | { type: "ui.task.open" }
  | { type: "ui.task.close" }
  | { type: "ui.permission.open"; requestId: string }
  | { type: "ui.permission.close"; requestId?: string | null }
  | {
      type: "event.normalized"
      normalizedEvent?: Record<string, unknown> | null
      overlayIntent?: Record<string, unknown> | null
      summaryText?: string | null
    }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const normalizeStatus = (value: string | undefined): string => {
  const lowered = (value ?? "").trim().toLowerCase()
  if (!lowered) return "unknown"
  if (["running", "active", "in_progress"].includes(lowered)) return "running"
  if (["completed", "done", "success", "ok"].includes(lowered)) return "completed"
  if (["failed", "error", "cancelled", "canceled", "aborted", "denied"].includes(lowered)) return "failed"
  return lowered
}

const computeCounts = (taskById: Readonly<Record<string, TaskEntry>>) => {
  let running = 0
  let completed = 0
  let failed = 0
  const laneCounts: Record<string, number> = {}
  const subagentById: Record<string, SubagentGraphEntry> = {}
  const ids = Object.keys(taskById)
  for (const id of ids) {
    const entry = taskById[id]
    const status = normalizeStatus(entry?.status)
    const lane = (entry?.laneLabel ?? entry?.subagentLabel ?? "").trim()
    if (lane) laneCounts[lane] = (laneCounts[lane] ?? 0) + 1
    const subagentId = (entry?.subagentId ?? entry?.childSessionId ?? entry?.laneId ?? entry?.laneLabel ?? "").trim()
    if (subagentId) {
      const label = (entry?.subagentLabel ?? entry?.laneLabel ?? subagentId).trim() || subagentId
      const next =
        subagentById[subagentId] ??
        ({
          id: subagentId,
          label,
          parentSessionId: entry?.parentSessionId ?? null,
          childSessionId: entry?.childSessionId ?? null,
          taskCount: 0,
          runningCount: 0,
          completedCount: 0,
          failedCount: 0,
        } satisfies SubagentGraphEntry)
      next.taskCount += 1
      if (status === "running") next.runningCount += 1
      else if (status === "completed") next.completedCount += 1
      else if (status === "failed") next.failedCount += 1
      subagentById[subagentId] = next
    }
    if (status === "running") running += 1
    else if (status === "completed") completed += 1
    else if (status === "failed") failed += 1
  }
  const subagentOrder = Object.values(subagentById)
    .sort((a, b) => {
      if (b.runningCount !== a.runningCount) return b.runningCount - a.runningCount
      if (b.taskCount !== a.taskCount) return b.taskCount - a.taskCount
      return a.label.localeCompare(b.label)
    })
    .map((entry) => entry.id)
  return {
    taskRunningCount: running,
    taskCompletedCount: completed,
    taskFailedCount: failed,
    taskTotalCount: ids.length,
    taskLaneCounts: laneCounts,
    taskActiveLanes: Object.keys(laneCounts).length,
    subagentById,
    subagentOrder,
  }
}

export const createOverlayAdapterState = (): OverlayAdapterState => ({
  activeKind: "none",
  paletteKind: null,
  permissionRequestId: null,
  taskById: {},
  taskRunningCount: 0,
  taskCompletedCount: 0,
  taskFailedCount: 0,
  taskTotalCount: 0,
  taskLaneCounts: {},
  taskActiveLanes: 0,
  subagentById: {},
  subagentOrder: [],
  lastTaskSummary: null,
})

const withTaskUpdate = (
  state: OverlayAdapterState,
  taskId: string | null,
  taskStatus: string | null,
  taskDescription: string | null,
  taskLaneId: string | null,
  taskLaneLabel: string | null,
  taskSubagentId: string | null,
  taskSubagentLabel: string | null,
  parentSessionId: string | null,
  childSessionId: string | null,
  summaryText: string | null,
): OverlayAdapterState => {
  const nextTaskById: Record<string, TaskEntry> = { ...state.taskById }
  if (taskId) {
    nextTaskById[taskId] = {
      status: normalizeStatus(taskStatus ?? undefined),
      description: taskDescription ?? undefined,
      laneId: taskLaneId ?? undefined,
      laneLabel: taskLaneLabel ?? undefined,
      subagentId: taskSubagentId ?? undefined,
      subagentLabel: taskSubagentLabel ?? undefined,
      parentSessionId: parentSessionId ?? undefined,
      childSessionId: childSessionId ?? undefined,
    }
  }
  const counts = computeCounts(nextTaskById)
  const activeKind: OverlayKind =
    state.permissionRequestId != null
      ? "permission"
      : state.paletteKind != null
        ? "palette"
        : counts.taskRunningCount > 0
          ? "task"
          : "none"
  return {
    ...state,
    ...counts,
    activeKind,
    taskById: nextTaskById,
    lastTaskSummary: summaryText ?? state.lastTaskSummary,
  }
}

export const reduceOverlayAdapterState = (state: OverlayAdapterState, action: OverlayAdapterAction): OverlayAdapterState => {
  switch (action.type) {
    case "ui.palette.open":
      return {
        ...state,
        activeKind: "palette",
        paletteKind: action.kind,
      }
    case "ui.palette.close": {
      const activeKind: OverlayKind =
        state.permissionRequestId != null
          ? "permission"
          : state.taskRunningCount > 0
            ? "task"
            : "none"
      return {
        ...state,
        activeKind,
        paletteKind: null,
      }
    }
    case "ui.task.open": {
      return {
        ...state,
        activeKind: "task",
      }
    }
    case "ui.task.close": {
      const activeKind: OverlayKind =
        state.permissionRequestId != null ? "permission" : state.paletteKind != null ? "palette" : "none"
      return {
        ...state,
        activeKind,
      }
    }
    case "ui.permission.open":
      return {
        ...state,
        activeKind: "permission",
        permissionRequestId: action.requestId,
      }
    case "ui.permission.close": {
      if (state.permissionRequestId && action.requestId && state.permissionRequestId !== action.requestId) {
        return state
      }
      const activeKind: OverlayKind =
        state.paletteKind != null ? "palette" : state.taskRunningCount > 0 ? "task" : "none"
      return {
        ...state,
        activeKind,
        permissionRequestId: null,
      }
    }
    case "event.normalized": {
      const normalized = isRecord(action.normalizedEvent) ? action.normalizedEvent : null
      const overlayIntent = isRecord(action.overlayIntent) ? action.overlayIntent : null
      const intentKind = overlayIntent && typeof overlayIntent.kind === "string" ? overlayIntent.kind : null
      const intentAction = overlayIntent && typeof overlayIntent.action === "string" ? overlayIntent.action : null

      if (intentKind === "permission") {
        const requestId = overlayIntent && typeof overlayIntent.requestId === "string" ? overlayIntent.requestId : null
        if (intentAction === "open" && requestId) {
          return {
            ...state,
            activeKind: "permission",
            permissionRequestId: requestId,
          }
        }
        if (intentAction === "close") {
          return reduceOverlayAdapterState(state, { type: "ui.permission.close", requestId })
        }
      }

      const normalizedType = normalized && typeof normalized.type === "string" ? normalized.type : null
      if (intentKind === "task" || normalizedType === "task.update") {
        const taskId =
          (overlayIntent && typeof overlayIntent.taskId === "string" && overlayIntent.taskId) ||
          (normalized && typeof normalized.taskId === "string" && normalized.taskId) ||
          null
        const taskStatus = normalized && typeof normalized.taskStatus === "string" ? normalized.taskStatus : null
        const taskDescription =
          normalized && typeof normalized.taskDescription === "string" ? normalized.taskDescription : null
        const taskLaneId = normalized && typeof normalized.taskLaneId === "string" ? normalized.taskLaneId : null
        const taskLaneLabel =
          normalized && typeof normalized.taskLaneLabel === "string" ? normalized.taskLaneLabel : null
        const taskSubagentId =
          normalized && typeof normalized.taskSubagentId === "string" ? normalized.taskSubagentId : null
        const taskSubagentLabel =
          normalized && typeof normalized.taskSubagentLabel === "string" ? normalized.taskSubagentLabel : null
        const parentSessionId =
          normalized && typeof normalized.parentSessionId === "string" ? normalized.parentSessionId : null
        const childSessionId =
          normalized && typeof normalized.childSessionId === "string" ? normalized.childSessionId : null
        return withTaskUpdate(
          state,
          taskId,
          taskStatus,
          taskDescription,
          taskLaneId,
          taskLaneLabel,
          taskSubagentId,
          taskSubagentLabel,
          parentSessionId,
          childSessionId,
          action.summaryText ?? null,
        )
      }
      if (normalizedType === "run.finished") {
        const counts = computeCounts(state.taskById)
        return {
          ...state,
          ...counts,
          activeKind: state.permissionRequestId ? "permission" : state.paletteKind ? "palette" : "none",
        }
      }
      return state
    }
    default:
      return state
  }
}
