import { stripAnsiCodes } from "../utils/ansi.js"

export type TaskStatusGroup = "running" | "blocked" | "failed" | "completed" | "cancelled" | "pending"
export type TaskGroupingMode = "status" | "lane"

const GROUP_RANK: Record<TaskStatusGroup, number> = {
  running: 0,
  blocked: 1,
  failed: 2,
  completed: 3,
  cancelled: 4,
  pending: 5,
}

export type GroupableTask = {
  readonly id?: string
  readonly status?: string
  readonly updatedAt?: number
  readonly laneId?: string | null
  readonly description?: string | null
  readonly subagentType?: string | null
  readonly kind?: string | null
  readonly sessionId?: string | null
}

export type TaskGroup<T extends GroupableTask> = {
  readonly key: string
  readonly label: string
  readonly mode: TaskGroupingMode
  readonly items: T[]
}

export type FlattenedGroupedTask<T extends GroupableTask> = {
  readonly task: T
  readonly groupKey: string
  readonly groupLabel: string
}

export type TaskStepLike = {
  readonly stepId?: string
  readonly label?: string
  readonly status?: string
  readonly attempt?: number
  readonly detail?: string
}

export const normalizeTaskStatusGroup = (value: unknown): TaskStatusGroup => {
  const raw = String(value ?? "").trim().toLowerCase()
  if (!raw) return "pending"
  if (raw.includes("run") || raw.includes("progress") || raw.includes("start")) return "running"
  if (raw.includes("block") || raw.includes("wait")) return "blocked"
  if (raw.includes("fail") || raw.includes("error") || raw.includes("timeout")) return "failed"
  if (raw.includes("complete") || raw.includes("done") || raw.includes("success") || raw.includes("finish")) {
    return "completed"
  }
  if (raw.includes("cancel") || raw.includes("stop")) return "cancelled"
  return "pending"
}

export const taskStatusGroupRank = (status: TaskStatusGroup): number => GROUP_RANK[status]

export const normalizeTaskLaneId = (value: unknown): string => {
  const lane = typeof value === "string" ? value.trim() : ""
  return lane.length > 0 ? lane : "primary"
}

export const taskGroupKeyForTask = (task: GroupableTask, mode: TaskGroupingMode): string => {
  if (mode === "lane") return `lane:${normalizeTaskLaneId(task.laneId)}`
  return `status:${normalizeTaskStatusGroup(task.status)}`
}

export const taskStatusGroupLabel = (status: TaskStatusGroup): string => {
  switch (status) {
    case "running":
      return "Running"
    case "blocked":
      return "Blocked"
    case "failed":
      return "Failed"
    case "completed":
      return "Completed"
    case "cancelled":
      return "Cancelled"
    default:
      return "Pending"
  }
}

export const countTaskRowsByStatusGroup = (
  rows: ReadonlyArray<{ status?: string }>,
): Record<TaskStatusGroup, number> => {
  const counts: Record<TaskStatusGroup, number> = {
    running: 0,
    blocked: 0,
    failed: 0,
    completed: 0,
    cancelled: 0,
    pending: 0,
  }
  for (const row of rows) {
    const group = normalizeTaskStatusGroup(row.status)
    counts[group] += 1
  }
  return counts
}

export const sortTasksForStatusGrouping = <T extends { status?: string; updatedAt?: number; id?: string }>(
  tasks: ReadonlyArray<T>,
): T[] =>
  [...tasks].sort((left, right) => {
    const leftGroup = normalizeTaskStatusGroup(left.status)
    const rightGroup = normalizeTaskStatusGroup(right.status)
    const byGroup = taskStatusGroupRank(leftGroup) - taskStatusGroupRank(rightGroup)
    if (byGroup !== 0) return byGroup
    const leftUpdated = Number.isFinite(left.updatedAt as number) ? (left.updatedAt as number) : 0
    const rightUpdated = Number.isFinite(right.updatedAt as number) ? (right.updatedAt as number) : 0
    if (leftUpdated !== rightUpdated) return rightUpdated - leftUpdated
    return String(left.id ?? "").localeCompare(String(right.id ?? ""))
  })

export const sortTasksForLaneGrouping = <T extends GroupableTask>(
  tasks: ReadonlyArray<T>,
  laneOrder: ReadonlyArray<string>,
): T[] => {
  const rank = new Map<string, number>()
  laneOrder.forEach((laneId, index) => {
    rank.set(normalizeTaskLaneId(laneId), index)
  })
  const laneRank = (laneId: string): number => {
    if (rank.has(laneId)) return rank.get(laneId) ?? 0
    return laneOrder.length + laneId.charCodeAt(0)
  }

  return [...tasks].sort((left, right) => {
    const leftLane = normalizeTaskLaneId(left.laneId)
    const rightLane = normalizeTaskLaneId(right.laneId)
    const byLane = laneRank(leftLane) - laneRank(rightLane)
    if (byLane !== 0) return byLane
    const leftStatus = normalizeTaskStatusGroup(left.status)
    const rightStatus = normalizeTaskStatusGroup(right.status)
    const byStatus = taskStatusGroupRank(leftStatus) - taskStatusGroupRank(rightStatus)
    if (byStatus !== 0) return byStatus
    const leftUpdated = Number.isFinite(left.updatedAt as number) ? (left.updatedAt as number) : 0
    const rightUpdated = Number.isFinite(right.updatedAt as number) ? (right.updatedAt as number) : 0
    if (leftUpdated !== rightUpdated) return rightUpdated - leftUpdated
    return String(left.id ?? "").localeCompare(String(right.id ?? ""))
  })
}

type BuildTaskGroupsOptions = {
  readonly mode: TaskGroupingMode
  readonly laneOrder?: ReadonlyArray<string>
  readonly laneLabelById?: Readonly<Record<string, string | undefined>>
}

export const buildTaskGroups = <T extends GroupableTask>(
  tasks: ReadonlyArray<T>,
  options: BuildTaskGroupsOptions,
): TaskGroup<T>[] => {
  const mode = options.mode
  const normalizedLaneOrder = (options.laneOrder ?? []).map((laneId) => normalizeTaskLaneId(laneId))
  const sorted =
    mode === "lane" ? sortTasksForLaneGrouping(tasks, normalizedLaneOrder) : sortTasksForStatusGrouping(tasks)
  const groups = new Map<string, TaskGroup<T>>()

  const resolveLabel = (task: T): string => {
    if (mode === "lane") {
      const laneId = normalizeTaskLaneId(task.laneId)
      return options.laneLabelById?.[laneId] ?? laneId
    }
    return taskStatusGroupLabel(normalizeTaskStatusGroup(task.status))
  }

  for (const task of sorted) {
    const key = taskGroupKeyForTask(task, mode)
    const existing = groups.get(key)
    if (existing) {
      existing.items.push(task)
      continue
    }
    groups.set(key, {
      key,
      label: resolveLabel(task),
      mode,
      items: [task],
    })
  }

  return Array.from(groups.values())
}

export const flattenTaskGroups = <T extends GroupableTask>(
  groups: ReadonlyArray<TaskGroup<T>>,
  collapsedGroupKeys?: ReadonlySet<string> | null,
): Array<FlattenedGroupedTask<T>> => {
  const collapsed = collapsedGroupKeys ?? new Set<string>()
  const rows: Array<FlattenedGroupedTask<T>> = []
  for (const group of groups) {
    if (collapsed.has(group.key)) continue
    for (const task of group.items) {
      rows.push({
        task,
        groupKey: group.key,
        groupLabel: group.label,
      })
    }
  }
  return rows
}

type TaskFilterOptions = {
  readonly query?: string
  readonly statusFilter?: "all" | TaskStatusGroup
  readonly laneFilter?: string
}

export const filterTasksForTaskboard = <T extends GroupableTask>(
  tasks: ReadonlyArray<T>,
  options: TaskFilterOptions,
): T[] => {
  const query = String(options.query ?? "")
    .trim()
    .toLowerCase()
  const statusFilter = options.statusFilter ?? "all"
  const laneFilter = normalizeTaskLaneId(options.laneFilter ?? "all")
  return tasks.filter((task) => {
    if (statusFilter !== "all") {
      const normalized = normalizeTaskStatusGroup(task.status)
      if (normalized !== statusFilter) return false
    }
    if (laneFilter !== "all") {
      const normalizedLane = normalizeTaskLaneId(task.laneId)
      if (normalizedLane !== laneFilter) return false
    }
    if (!query) return true
    const haystack = [
      task.id,
      task.description ?? "",
      task.subagentType ?? "",
      task.status ?? "",
      task.kind ?? "",
      task.sessionId ?? "",
      task.laneId ?? "",
    ]
      .join(" ")
      .toLowerCase()
    return haystack.includes(query)
  })
}

export const sanitizeTaskPreview = (value: unknown, maxChars = 240): string | null => {
  if (typeof value !== "string") return null
  const cleaned = stripAnsiCodes(value)
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
  if (!cleaned) return null
  if (cleaned.length <= maxChars) return cleaned
  return `${cleaned.slice(0, Math.max(1, maxChars - 3))}...`
}

export const formatTaskModeBadge = (mode: unknown): string | null => {
  const raw = String(mode ?? "")
    .trim()
    .toLowerCase()
  if (raw === "sync" || raw === "foreground" || raw === "fg") return "[fg]"
  if (raw === "async" || raw === "background" || raw === "bg") return "[bg]"
  return null
}

const deriveStepTerminalReason = (step: TaskStepLike): string | null => {
  const seed = `${String(step.label ?? "")} ${String(step.detail ?? "")}`
    .toLowerCase()
    .trim()
  if (!seed) return null
  if (seed.includes("timeout")) return "timeout"
  if (seed.includes("restart")) return "restarted"
  if (seed.includes("cancel")) return "cancelled"
  return null
}

export const formatTaskStepLine = (step: TaskStepLike, maxDetailChars = 100): string => {
  const status = normalizeTaskStatusGroup(step.status)
  const statusLabel =
    status === "running"
      ? "running"
      : status === "completed"
        ? "done"
        : status === "failed"
          ? "failed"
          : status === "cancelled"
            ? "cancelled"
            : status === "blocked"
              ? "blocked"
              : "pending"
  const label = sanitizeTaskPreview(step.label ?? "", 72) ?? "step"
  const attemptValue = Number(step.attempt)
  const attemptSuffix =
    Number.isFinite(attemptValue) && attemptValue > 1 ? ` (attempt ${Math.floor(attemptValue)})` : ""
  const reason = deriveStepTerminalReason(step)
  const reasonSuffix = reason ? `/${reason}` : ""
  const detail = sanitizeTaskPreview(step.detail ?? "", maxDetailChars)
  const depthSeed = String(step.stepId ?? "").split(/[/:.]/).filter(Boolean).length
  const depth = Math.max(0, Math.min(2, depthSeed - 1))
  const indent = depth > 0 ? "  ".repeat(depth) : ""
  if (detail) return `${indent}[${statusLabel}${reasonSuffix}] ${label}${attemptSuffix} - ${detail}`
  return `${indent}[${statusLabel}${reasonSuffix}] ${label}${attemptSuffix}`
}
