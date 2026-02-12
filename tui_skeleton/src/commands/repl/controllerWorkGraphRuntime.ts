import type {
  Lane,
  LaneKind,
  LaneStatusSummary,
  WorkCounters,
  WorkGraphState,
  WorkItem,
  WorkMode,
  WorkStatus,
  WorkStep,
  WorkStepKind,
} from "../../repl/types.js"
import { extractString, isRecord, numberOrUndefined } from "./controllerUtils.js"

const DEFAULT_MAX_WORK_ITEMS = 200
const DEFAULT_MAX_STEPS_PER_TASK = 50
const DEFAULT_MAX_PROCESSED_EVENT_KEYS = 512

export interface WorkGraphLimits {
  readonly maxWorkItems: number
  readonly maxStepsPerTask: number
  readonly maxProcessedEventKeys: number
}

export interface WorkGraphReduceInput {
  readonly eventType: string
  readonly payload: Record<string, unknown>
  readonly seq?: number | null
  readonly eventId?: string | null
  readonly timestamp?: number | null
}

export const createWorkGraphState = (): WorkGraphState => ({
  itemsById: {},
  itemOrder: [],
  lanesById: {},
  laneOrder: [],
  processedEventKeys: [],
  lastSeq: 0,
})

export const resolveWorkGraphLimits = (input?: Partial<WorkGraphLimits>): WorkGraphLimits => ({
  maxWorkItems: Math.max(1, Math.floor(input?.maxWorkItems ?? DEFAULT_MAX_WORK_ITEMS)),
  maxStepsPerTask: Math.max(1, Math.floor(input?.maxStepsPerTask ?? DEFAULT_MAX_STEPS_PER_TASK)),
  maxProcessedEventKeys: Math.max(16, Math.floor(input?.maxProcessedEventKeys ?? DEFAULT_MAX_PROCESSED_EVENT_KEYS)),
})

const normalizeWorkStatus = (value: string | undefined, kind?: string): WorkStatus => {
  const raw = String(value ?? kind ?? "").trim().toLowerCase()
  if (!raw) return "pending"
  if (
    raw.includes("complete") ||
    raw.includes("done") ||
    raw === "ok" ||
    raw === "success" ||
    raw === "succeeded" ||
    raw === "finished"
  ) {
    return "completed"
  }
  if (raw.includes("fail") || raw.includes("error") || raw === "timeout") return "failed"
  if (raw.includes("cancel")) return "cancelled"
  if (raw.includes("block") || raw.includes("wait")) return "blocked"
  if (raw.includes("run") || raw.includes("progress") || raw.includes("spawn") || raw.includes("start")) {
    return "running"
  }
  return "pending"
}

const normalizeWorkMode = (payload: Record<string, unknown>, eventType: string): WorkMode => {
  const raw = extractString(payload, ["mode", "task_mode", "taskMode"])?.toLowerCase()
  if (raw === "sync" || raw === "foreground" || raw === "fg") return "sync"
  if (raw === "async" || raw === "background" || raw === "bg") return "async"
  if (typeof payload.background === "boolean") return payload.background ? "async" : "sync"
  const kind = extractString(payload, ["kind", "type", "event"])?.toLowerCase() ?? ""
  if (kind.includes("background")) return "async"
  if (eventType.startsWith("agent.")) return "async"
  return "unknown"
}

const parseEventTime = (input: WorkGraphReduceInput): number => {
  if (typeof input.seq === "number" && Number.isFinite(input.seq)) return input.seq
  if (typeof input.timestamp === "number" && Number.isFinite(input.timestamp)) return input.timestamp
  const fromPayload =
    numberOrUndefined(input.payload.timestamp) ??
    numberOrUndefined(input.payload.timestamp_ms) ??
    numberOrUndefined(input.payload.updated_at)
  if (fromPayload != null) return fromPayload
  return Date.now()
}

const resolveLaneKind = (mode: WorkMode, eventType: string): LaneKind => {
  if (mode === "async") return "background_task"
  if (eventType.startsWith("agent.")) return "subagent"
  return "main"
}

const resolveTaskId = (payload: Record<string, unknown>): string | null =>
  extractString(payload, ["task_id", "taskId", "agent_id", "agentId", "id"]) ?? null

const resolveLaneId = (payload: Record<string, unknown>, taskId: string): string =>
  extractString(payload, ["lane_id", "laneId"]) ?? `task:${taskId}`

const resolveLaneLabel = (payload: Record<string, unknown>, taskId: string): string =>
  extractString(payload, ["lane_label", "laneLabel", "subagent_type", "subagentType"]) ?? taskId

const resolveTitle = (payload: Record<string, unknown>, fallbackId: string): string =>
  extractString(payload, ["description", "title", "prompt", "summary"]) ?? fallbackId

const resolveEventKey = (input: WorkGraphReduceInput, taskId: string, status: WorkStatus, updatedAt: number): string => {
  if (input.eventId && input.eventId.trim()) return input.eventId.trim()
  const kind = extractString(input.payload, ["kind", "type", "event"]) ?? ""
  const seq = input.seq != null ? String(input.seq) : ""
  return [input.eventType, taskId, status, kind, seq, String(updatedAt)].join("|")
}

const sanitizeExcerpt = (payload: Record<string, unknown>): string | null => {
  const raw = extractString(payload, ["output_excerpt", "output", "result", "message", "error"])
  if (!raw) return null
  const cleaned = raw.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, " ").trim()
  if (!cleaned) return null
  return cleaned.length > 160 ? `${cleaned.slice(0, 159)}…` : cleaned
}

const safeCounterTotals = (steps: ReadonlyArray<WorkStep>): WorkCounters => {
  let completed = 0
  let running = 0
  let failed = 0
  for (const step of steps) {
    if (step.status === "completed") completed += 1
    else if (step.status === "running") running += 1
    else if (step.status === "failed") failed += 1
  }
  return { completed, running, failed, total: steps.length }
}

const clampSteps = (steps: ReadonlyArray<WorkStep>, maxStepsPerTask: number): ReadonlyArray<WorkStep> =>
  steps.length <= maxStepsPerTask ? steps : steps.slice(steps.length - maxStepsPerTask)

const upsertStep = (
  current: ReadonlyArray<WorkStep>,
  payload: Record<string, unknown>,
  status: WorkStatus,
  updatedAt: number,
  maxStepsPerTask: number,
): ReadonlyArray<WorkStep> => {
  const tool = extractString(payload, ["tool", "tool_name", "toolName"])
  const callId = extractString(payload, ["call_id", "callId"])
  const kindRaw = extractString(payload, ["kind", "event", "type"])?.toLowerCase() ?? ""
  const hasStepSignal = Boolean(tool || callId || kindRaw.includes("tool"))
  if (!hasStepSignal) return current
  const stepId = extractString(payload, ["step_id", "stepId", "call_id", "callId"]) ?? `${kindRaw}:${updatedAt}`
  const label = tool ? `${tool}` : kindRaw || "step"
  const detail = sanitizeExcerpt(payload) ?? undefined
  const attemptRaw = payload.attempt
  const attempt = typeof attemptRaw === "number" && Number.isFinite(attemptRaw) ? Math.max(1, Math.floor(attemptRaw)) : undefined
  const stepKind: WorkStepKind = tool ? "tool" : "note"
  const nextStep: WorkStep = {
    stepId,
    kind: stepKind,
    label,
    status,
    startedAt: status === "running" ? updatedAt : undefined,
    endedAt: status !== "running" ? updatedAt : undefined,
    attempt,
    detail,
  }
  const index = current.findIndex((step) => step.stepId === stepId)
  if (index < 0) return clampSteps([...current, nextStep], maxStepsPerTask)
  const merged: WorkStep = {
    ...current[index],
    ...nextStep,
    startedAt: current[index].startedAt ?? nextStep.startedAt,
    endedAt: nextStep.endedAt ?? current[index].endedAt,
  }
  const next = current.slice()
  next[index] = merged
  return clampSteps(next, maxStepsPerTask)
}

const compareIdsByUpdatedAt = (itemsById: Record<string, WorkItem>) => (a: string, b: string): number => {
  const left = itemsById[a]
  const right = itemsById[b]
  if (!left || !right) return a.localeCompare(b)
  if (left.updatedAt !== right.updatedAt) return right.updatedAt - left.updatedAt
  return a.localeCompare(b)
}

const rebuildLaneSummaries = (
  laneOrder: ReadonlyArray<string>,
  lanesById: Record<string, Lane>,
  itemOrder: ReadonlyArray<string>,
  itemsById: Record<string, WorkItem>,
): Record<string, Lane> => {
  type MutableLaneSummary = {
    running: number
    failed: number
    blocked: number
  }
  const summaries: Record<string, MutableLaneSummary> = {}
  for (const laneId of laneOrder) {
    summaries[laneId] = { running: 0, failed: 0, blocked: 0 }
  }
  for (const workId of itemOrder) {
    const item = itemsById[workId]
    if (!item) continue
    if (!summaries[item.laneId]) summaries[item.laneId] = { running: 0, failed: 0, blocked: 0 }
    if (item.status === "running") summaries[item.laneId].running += 1
    else if (item.status === "failed") summaries[item.laneId].failed += 1
    else if (item.status === "blocked") summaries[item.laneId].blocked += 1
  }
  const nextLanes: Record<string, Lane> = {}
  for (const laneId of laneOrder) {
    const lane = lanesById[laneId]
    if (!lane) continue
    nextLanes[laneId] = {
      ...lane,
      statusSummary: (summaries[laneId] ?? { running: 0, failed: 0, blocked: 0 }) as LaneStatusSummary,
    }
  }
  return nextLanes
}

export const reduceWorkGraphEvent = (
  previous: WorkGraphState,
  input: WorkGraphReduceInput,
  limitsInput?: Partial<WorkGraphLimits>,
): WorkGraphState => {
  const limits = resolveWorkGraphLimits(limitsInput)
  const taskId = resolveTaskId(input.payload)
  if (!taskId) return previous
  const updatedAt = parseEventTime(input)
  const kind = extractString(input.payload, ["kind", "event", "type"])
  const status = normalizeWorkStatus(extractString(input.payload, ["status", "state"]), kind)
  const eventKey = resolveEventKey(input, taskId, status, updatedAt)
  if (previous.processedEventKeys.includes(eventKey)) return previous

  const mode = normalizeWorkMode(input.payload, input.eventType)
  const laneId = resolveLaneId(input.payload, taskId)
  const laneLabel = resolveLaneLabel(input.payload, taskId)
  const laneKind = resolveLaneKind(mode, input.eventType)
  const title = resolveTitle(input.payload, taskId)
  const artifactPath =
    extractString(input.payload, ["artifact_path", "artifactPath"]) ??
    (isRecord(input.payload.artifact)
      ? extractString(input.payload.artifact, ["path", "file"])
      : undefined)
  const excerpt = sanitizeExcerpt(input.payload)
  const existing = previous.itemsById[taskId]
  const currentSteps = existing?.steps ?? []
  const nextSteps = upsertStep(currentSteps, input.payload, status, updatedAt, limits.maxStepsPerTask)
  const nextItem: WorkItem = {
    workId: taskId,
    laneId,
    laneLabel,
    title,
    mode,
    status,
    createdAt: existing?.createdAt ?? updatedAt,
    updatedAt,
    parentWorkId: extractString(input.payload, ["parent_task_id", "parentTaskId"]) ?? existing?.parentWorkId ?? null,
    treePath: extractString(input.payload, ["tree_path", "treePath"]) ?? existing?.treePath ?? null,
    depth: numberOrUndefined(input.payload.depth) ?? existing?.depth ?? null,
    artifactPaths: artifactPath
      ? [...new Set([...(existing?.artifactPaths ?? []), artifactPath])]
      : existing?.artifactPaths ?? [],
    lastSafeExcerpt: excerpt ?? existing?.lastSafeExcerpt ?? null,
    steps: nextSteps,
    counters: safeCounterTotals(nextSteps),
  }
  const nextItemsById: Record<string, WorkItem> = {
    ...previous.itemsById,
    [taskId]: nextItem,
  }

  const laneArtifactJsonl =
    extractString(input.payload, ["artifact_path", "artifactPath"]) ??
    (artifactPath?.endsWith(".jsonl") ? artifactPath : undefined) ??
    previous.lanesById[laneId]?.artifact?.jsonl ??
    null
  const laneArtifactMeta =
    artifactPath && artifactPath.endsWith(".json") ? artifactPath : previous.lanesById[laneId]?.artifact?.metaJson ?? null

  const nextLanesById: Record<string, Lane> = {
    ...previous.lanesById,
    [laneId]: {
      laneId,
      label: laneLabel,
      kind: laneKind,
      statusSummary: previous.lanesById[laneId]?.statusSummary ?? { running: 0, failed: 0, blocked: 0 },
      artifact: {
        jsonl: laneArtifactJsonl,
        metaJson: laneArtifactMeta,
      },
    },
  }

  let nextItemOrder = Object.keys(nextItemsById).sort(compareIdsByUpdatedAt(nextItemsById))
  if (nextItemOrder.length > limits.maxWorkItems) {
    const keep = new Set(nextItemOrder.slice(0, limits.maxWorkItems))
    for (const workId of Object.keys(nextItemsById)) {
      if (!keep.has(workId)) delete nextItemsById[workId]
    }
    nextItemOrder = nextItemOrder.slice(0, limits.maxWorkItems)
  }

  const nextLaneOrder = Object.keys(nextLanesById).sort((a, b) => {
    const left = nextLanesById[a]
    const right = nextLanesById[b]
    if (!left || !right) return a.localeCompare(b)
    return left.label.localeCompare(right.label) || a.localeCompare(b)
  })

  const processedEventKeys = [...previous.processedEventKeys, eventKey]
  const clampedProcessedEventKeys =
    processedEventKeys.length <= limits.maxProcessedEventKeys
      ? processedEventKeys
      : processedEventKeys.slice(processedEventKeys.length - limits.maxProcessedEventKeys)

  return {
    itemsById: nextItemsById,
    itemOrder: nextItemOrder,
    lanesById: rebuildLaneSummaries(nextLaneOrder, nextLanesById, nextItemOrder, nextItemsById),
    laneOrder: nextLaneOrder,
    processedEventKeys: clampedProcessedEventKeys,
    lastSeq:
      typeof input.seq === "number" && Number.isFinite(input.seq)
        ? Math.max(previous.lastSeq, input.seq)
        : previous.lastSeq,
  }
}

export const reduceWorkGraphEvents = (
  previous: WorkGraphState,
  events: ReadonlyArray<WorkGraphReduceInput>,
  limitsInput?: Partial<WorkGraphLimits>,
): WorkGraphState => {
  if (events.length === 0) return previous
  const decorated = events.map((event, index) => ({ event, index }))
  decorated.sort((a, b) => {
    const aSeq = typeof a.event.seq === "number" && Number.isFinite(a.event.seq) ? a.event.seq : null
    const bSeq = typeof b.event.seq === "number" && Number.isFinite(b.event.seq) ? b.event.seq : null
    if (aSeq != null && bSeq != null && aSeq !== bSeq) return aSeq - bSeq
    const aTs = parseEventTime(a.event)
    const bTs = parseEventTime(b.event)
    if (aTs !== bTs) return aTs - bTs
    return a.index - b.index
  })
  let state = previous
  for (const { event } of decorated) {
    state = reduceWorkGraphEvent(state, event, limitsInput)
  }
  return state
}
