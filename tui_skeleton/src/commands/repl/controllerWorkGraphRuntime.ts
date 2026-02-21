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
const DEFAULT_LANE_RETENTION_MS = 30 * 60 * 1000
const DEFAULT_MAX_LANES = 128

export interface WorkGraphLimits {
  readonly maxWorkItems: number
  readonly maxStepsPerTask: number
  readonly maxProcessedEventKeys: number
  readonly laneRetentionMs: number
  readonly maxLanes: number
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
  telemetry: {
    laneTransitions: 0,
    droppedTransitions: 0,
    laneChurn: 0,
    droppedEvents: 0,
  },
})

export const resolveWorkGraphLimits = (input?: Partial<WorkGraphLimits>): WorkGraphLimits => ({
  maxWorkItems: Math.max(1, Math.floor(input?.maxWorkItems ?? DEFAULT_MAX_WORK_ITEMS)),
  maxStepsPerTask: Math.max(1, Math.floor(input?.maxStepsPerTask ?? DEFAULT_MAX_STEPS_PER_TASK)),
  maxProcessedEventKeys: Math.max(16, Math.floor(input?.maxProcessedEventKeys ?? DEFAULT_MAX_PROCESSED_EVENT_KEYS)),
  laneRetentionMs: Math.max(1_000, Math.floor(input?.laneRetentionMs ?? DEFAULT_LANE_RETENTION_MS)),
  maxLanes: Math.max(1, Math.floor(input?.maxLanes ?? DEFAULT_MAX_LANES)),
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

const ALLOWED_STATUS_TRANSITIONS: Record<WorkStatus, ReadonlySet<WorkStatus>> = {
  pending: new Set(["pending", "running", "blocked", "cancelled", "failed"]),
  running: new Set(["running", "blocked", "completed", "failed", "cancelled"]),
  blocked: new Set(["blocked", "running", "completed", "failed", "cancelled"]),
  completed: new Set(["completed"]),
  failed: new Set(["failed", "running"]),
  cancelled: new Set(["cancelled", "running"]),
}

const resolveTransitionedStatus = (
  previousStatus: WorkStatus | undefined,
  nextStatus: WorkStatus,
): { status: WorkStatus; droppedTransition: boolean; transitioned: boolean } => {
  if (!previousStatus) {
    return { status: nextStatus, droppedTransition: false, transitioned: true }
  }
  if (previousStatus === nextStatus) {
    return { status: nextStatus, droppedTransition: false, transitioned: false }
  }
  const allowed = ALLOWED_STATUS_TRANSITIONS[previousStatus]
  if (allowed && allowed.has(nextStatus)) {
    return { status: nextStatus, droppedTransition: false, transitioned: true }
  }
  return { status: previousStatus, droppedTransition: true, transitioned: false }
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
  const laneUpdatedAt: Record<string, number> = {}
  const laneActiveCount: Record<string, number> = {}
  const laneRecentTool: Record<string, string | null> = {}
  for (const laneId of laneOrder) {
    summaries[laneId] = { running: 0, failed: 0, blocked: 0 }
    laneUpdatedAt[laneId] = 0
    laneActiveCount[laneId] = 0
    laneRecentTool[laneId] = null
  }
  for (const workId of itemOrder) {
    const item = itemsById[workId]
    if (!item) continue
    if (!summaries[item.laneId]) summaries[item.laneId] = { running: 0, failed: 0, blocked: 0 }
    laneUpdatedAt[item.laneId] = Math.max(laneUpdatedAt[item.laneId] ?? 0, item.updatedAt)
    laneActiveCount[item.laneId] = (laneActiveCount[item.laneId] ?? 0) + 1
    const latestStep = item.steps[item.steps.length - 1]
    if (latestStep?.label) {
      laneRecentTool[item.laneId] = latestStep.label
    }
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
      updatedAt: laneUpdatedAt[laneId] ?? lane.updatedAt ?? 0,
      activeCount: laneActiveCount[laneId] ?? lane.activeCount ?? 0,
      recentTool: laneRecentTool[laneId] ?? lane.recentTool ?? null,
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
  const nextStatusCandidate = normalizeWorkStatus(extractString(input.payload, ["status", "state"]), kind)
  const existing = previous.itemsById[taskId]
  const transition = resolveTransitionedStatus(existing?.status, nextStatusCandidate)
  const status = transition.status
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
  let droppedEvents = previous.telemetry?.droppedEvents ?? 0
  if (nextItemOrder.length > limits.maxWorkItems) {
    const keep = new Set(nextItemOrder.slice(0, limits.maxWorkItems))
    for (const workId of Object.keys(nextItemsById)) {
      if (!keep.has(workId)) {
        delete nextItemsById[workId]
        droppedEvents += 1
      }
    }
    nextItemOrder = nextItemOrder.slice(0, limits.maxWorkItems)
  }

  const liveLaneIds = new Set(nextItemOrder.map((workId) => nextItemsById[workId]?.laneId).filter(Boolean))
  for (const laneId of Object.keys(nextLanesById)) {
    if (!liveLaneIds.has(laneId)) {
      const laneUpdatedAt = nextLanesById[laneId]?.updatedAt ?? 0
      if (updatedAt - laneUpdatedAt > limits.laneRetentionMs) {
        delete nextLanesById[laneId]
      }
    }
  }

  let nextLaneOrder = Object.keys(nextLanesById).sort((a, b) => {
    const left = nextLanesById[a]
    const right = nextLanesById[b]
    if (!left || !right) return a.localeCompare(b)
    if ((left.updatedAt ?? 0) !== (right.updatedAt ?? 0)) {
      return (right.updatedAt ?? 0) - (left.updatedAt ?? 0)
    }
    return left.label.localeCompare(right.label) || a.localeCompare(b)
  })
  if (nextLaneOrder.length > limits.maxLanes) {
    const keep = new Set(nextLaneOrder.slice(0, limits.maxLanes))
    for (const laneId of Object.keys(nextLanesById)) {
      if (!keep.has(laneId)) delete nextLanesById[laneId]
    }
    nextLaneOrder = nextLaneOrder.slice(0, limits.maxLanes)
  }

  const processedEventKeys = [...previous.processedEventKeys, eventKey]
  const clampedProcessedEventKeys =
    processedEventKeys.length <= limits.maxProcessedEventKeys
      ? processedEventKeys
      : processedEventKeys.slice(processedEventKeys.length - limits.maxProcessedEventKeys)

  const previousLaneForItem = existing?.laneId ?? null
  const laneChurn = previousLaneForItem && previousLaneForItem !== laneId ? 1 : 0
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
    telemetry: {
      laneTransitions: (previous.telemetry?.laneTransitions ?? 0) + (transition.transitioned ? 1 : 0),
      droppedTransitions: (previous.telemetry?.droppedTransitions ?? 0) + (transition.droppedTransition ? 1 : 0),
      laneChurn: (previous.telemetry?.laneChurn ?? 0) + laneChurn,
      droppedEvents,
    },
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

export const formatWorkGraphReducerTrace = (
  previous: WorkGraphState,
  next: WorkGraphState,
  events: ReadonlyArray<WorkGraphReduceInput>,
): string => {
  const eventCount = events.length
  const eventTypes = Array.from(new Set(events.map((event) => event.eventType))).slice(0, 8).join(",")
  const seqs = events
    .map((event) => event.seq)
    .filter((seq): seq is number => typeof seq === "number" && Number.isFinite(seq))
  const seqRange = seqs.length > 0 ? `${Math.min(...seqs)}..${Math.max(...seqs)}` : "n/a"
  const keyDelta = Math.max(0, next.processedEventKeys.length - previous.processedEventKeys.length)
  return [
    "[workgraph-trace]",
    `events=${eventCount}`,
    `seq=${seqRange}`,
    `types=${eventTypes || "n/a"}`,
    `items=${previous.itemOrder.length}->${next.itemOrder.length}`,
    `lanes=${previous.laneOrder.length}->${next.laneOrder.length}`,
    `keysApplied=${keyDelta}`,
    `lastSeq=${previous.lastSeq}->${next.lastSeq}`,
    `laneTransitions=${previous.telemetry?.laneTransitions ?? 0}->${next.telemetry?.laneTransitions ?? 0}`,
    `droppedTransitions=${previous.telemetry?.droppedTransitions ?? 0}->${next.telemetry?.droppedTransitions ?? 0}`,
    `laneChurn=${previous.telemetry?.laneChurn ?? 0}->${next.telemetry?.laneChurn ?? 0}`,
  ].join(" ")
}
