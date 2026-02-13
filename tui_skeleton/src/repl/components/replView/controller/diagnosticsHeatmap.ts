type HeatmapTask = {
  readonly laneId?: string | null
  readonly updatedAt?: number | null
  readonly status?: string | null
}

export type LaneDiagnosticsHotspot = {
  readonly laneId: string
  readonly laneLabel: string
  readonly score: number
  readonly intensity: "low" | "medium" | "high" | "critical"
  readonly bar: string
  readonly taskCount: number
  readonly running: number
  readonly failed: number
  readonly blocked: number
  readonly freshnessMs: number
}

type BuildHeatmapOptions = {
  readonly nowMs?: number
  readonly maxRows?: number
  readonly barWidth?: number
  readonly laneLabelById?: Record<string, string>
}

type LaneAccumulator = {
  laneId: string
  taskCount: number
  running: number
  failed: number
  blocked: number
  latestUpdatedAt: number
}

const clamp = (value: number, min: number, max: number): number => Math.max(min, Math.min(max, value))

const normalizeLaneId = (value: unknown): string => {
  if (typeof value !== "string") return "primary"
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : "primary"
}

const normalizeStatus = (value: unknown): string => {
  if (typeof value !== "string") return "pending"
  const trimmed = value.trim().toLowerCase()
  return trimmed.length > 0 ? trimmed : "pending"
}

const scoreToIntensity = (score: number): LaneDiagnosticsHotspot["intensity"] => {
  if (score >= 80) return "critical"
  if (score >= 60) return "high"
  if (score >= 35) return "medium"
  return "low"
}

const scoreToBar = (score: number, width: number): string => {
  const safeWidth = clamp(width, 4, 24)
  const filled = clamp(Math.round((clamp(score, 0, 100) / 100) * safeWidth), 0, safeWidth)
  return `${"#".repeat(filled)}${"-".repeat(Math.max(0, safeWidth - filled))}`
}

export const buildLaneDiagnosticsHeatmap = (
  tasks: readonly HeatmapTask[],
  options: BuildHeatmapOptions = {},
): LaneDiagnosticsHotspot[] => {
  if (!Array.isArray(tasks) || tasks.length === 0) return []
  const nowMs = Number.isFinite(options.nowMs) ? Number(options.nowMs) : Date.now()
  const maxRows = clamp(Math.floor(options.maxRows ?? 6), 1, 64)
  const barWidth = clamp(Math.floor(options.barWidth ?? 10), 4, 24)
  const laneLabelById = options.laneLabelById ?? {}

  const byLane = new Map<string, LaneAccumulator>()
  for (const task of tasks) {
    const laneId = normalizeLaneId(task?.laneId)
    const status = normalizeStatus(task?.status)
    const updatedAtRaw = Number(task?.updatedAt ?? 0)
    const updatedAt = Number.isFinite(updatedAtRaw) ? Math.max(0, updatedAtRaw) : 0
    const acc =
      byLane.get(laneId) ??
      ({
        laneId,
        taskCount: 0,
        running: 0,
        failed: 0,
        blocked: 0,
        latestUpdatedAt: 0,
      } as LaneAccumulator)
    acc.taskCount += 1
    if (status === "running") acc.running += 1
    if (status === "failed") acc.failed += 1
    if (status === "blocked") acc.blocked += 1
    acc.latestUpdatedAt = Math.max(acc.latestUpdatedAt, updatedAt)
    byLane.set(laneId, acc)
  }

  const rows = Array.from(byLane.values()).map((lane) => {
    const freshnessMs = Math.max(0, nowMs - lane.latestUpdatedAt)
    const activityScore = clamp(lane.taskCount * 8, 0, 40)
    const recencyScore = clamp(30 - Math.floor(freshnessMs / 1000), 0, 30)
    const statePressureScore = clamp(lane.running * 4 + lane.blocked * 6 + lane.failed * 8, 0, 30)
    const score = clamp(activityScore + recencyScore + statePressureScore, 0, 100)
    return {
      laneId: lane.laneId,
      laneLabel: laneLabelById[lane.laneId] ?? lane.laneId,
      score,
      intensity: scoreToIntensity(score),
      bar: scoreToBar(score, barWidth),
      taskCount: lane.taskCount,
      running: lane.running,
      failed: lane.failed,
      blocked: lane.blocked,
      freshnessMs,
    } as LaneDiagnosticsHotspot
  })

  rows.sort((left, right) => {
    if (right.score !== left.score) return right.score - left.score
    if (left.freshnessMs !== right.freshnessMs) return left.freshnessMs - right.freshnessMs
    return left.laneId.localeCompare(right.laneId)
  })
  return rows.slice(0, maxRows)
}
