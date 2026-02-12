import type {
  ActivityDetail,
  ActivityPrimary,
  ActivitySnapshot,
  RuntimeBehaviorFlags,
  RuntimeTelemetry,
  ThinkingArtifact,
  ThinkingMode,
} from "../../repl/types.js"
import { extractRawString, isRecord, parseNumberish } from "./controllerUtils.js"

const DEFAULT_MIN_DISPLAY_MS = 200
const DEFAULT_STATUS_UPDATE_MS = 120
const DEFAULT_THINKING_MAX_CHARS = 600
const DEFAULT_THINKING_MAX_LINES = 6
const DEFAULT_ADAPTIVE_MIN_CHUNK_CHARS = 8
const DEFAULT_ADAPTIVE_MIN_COALESCE_MS = 12
const DEFAULT_ADAPTIVE_BURST_CHARS = 48
const DEFAULT_SUBAGENT_COALESCE_MS = 125
const DEFAULT_SUBAGENT_MAX_WORK_ITEMS = 200
const DEFAULT_SUBAGENT_MAX_STEPS_PER_TASK = 50

const PRIMARY_LABELS: Record<ActivityPrimary, string> = {
  idle: "Ready",
  session: "Session active",
  run: "Run started",
  thinking: "Assistant thinking…",
  responding: "Assistant responding…",
  tool_call: "Tool call in progress…",
  tool_result: "Tool result received",
  permission_required: "Permission required",
  permission_resolved: "Permission response received",
  reconnecting: "Reconnecting",
  compacting: "Compacting conversation…",
  completed: "Finished",
  halted: "Halted",
  cancelled: "Cancelled",
  error: "Error received",
}

const PRIMARY_PRIORITY: Record<ActivityPrimary, number> = {
  idle: 10,
  session: 20,
  run: 30,
  thinking: 40,
  responding: 50,
  tool_call: 55,
  tool_result: 45,
  permission_required: 80,
  permission_resolved: 82,
  reconnecting: 85,
  compacting: 75,
  completed: 95,
  halted: 95,
  cancelled: 95,
  error: 100,
}

const LEGAL_TRANSITIONS: Record<ActivityPrimary, ReadonlyArray<ActivityPrimary>> = {
  idle: ["idle", "session", "run", "thinking", "reconnecting", "compacting", "error"],
  session: [
    "session",
    "run",
    "thinking",
    "responding",
    "permission_required",
    "reconnecting",
    "completed",
    "halted",
    "cancelled",
    "idle",
    "error",
  ],
  run: [
    "run",
    "thinking",
    "responding",
    "tool_call",
    "permission_required",
    "reconnecting",
    "compacting",
    "completed",
    "halted",
    "cancelled",
    "error",
    "idle",
  ],
  thinking: [
    "thinking",
    "responding",
    "tool_call",
    "permission_required",
    "compacting",
    "completed",
    "halted",
    "cancelled",
    "error",
    "reconnecting",
  ],
  responding: [
    "responding",
    "thinking",
    "tool_call",
    "tool_result",
    "permission_required",
    "compacting",
    "completed",
    "halted",
    "cancelled",
    "error",
    "reconnecting",
  ],
  tool_call: [
    "tool_call",
    "tool_result",
    "responding",
    "permission_required",
    "completed",
    "halted",
    "error",
    "cancelled",
    "reconnecting",
  ],
  tool_result: ["tool_result", "responding", "thinking", "completed", "halted", "cancelled", "error", "reconnecting"],
  permission_required: [
    "permission_required",
    "permission_resolved",
    "completed",
    "halted",
    "cancelled",
    "error",
    "reconnecting",
  ],
  permission_resolved: ["permission_resolved", "thinking", "responding", "tool_call", "completed", "halted", "cancelled", "error", "reconnecting"],
  reconnecting: ["reconnecting", "run", "thinking", "responding", "idle", "error", "cancelled", "halted"],
  compacting: [
    "compacting",
    "session",
    "run",
    "thinking",
    "responding",
    "completed",
    "halted",
    "error",
    "idle",
    "reconnecting",
  ],
  completed: ["completed", "idle", "session", "run", "thinking", "responding", "reconnecting"],
  halted: ["halted", "idle", "session", "run", "thinking", "responding", "reconnecting"],
  cancelled: ["cancelled", "idle", "session", "run", "thinking", "responding", "reconnecting"],
  error: ["error", "idle", "session", "run", "thinking", "responding", "reconnecting"],
}

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const parseBoundedIntEnv = (
  value: string | undefined,
  fallback: number,
  min: number,
  max: number,
): number => {
  const parsed = parseNumberish(value)
  if (parsed == null || !Number.isFinite(parsed)) return fallback
  const rounded = Math.round(parsed)
  if (rounded < min) return min
  if (rounded > max) return max
  return rounded
}

export interface ActivityTransitionInput {
  readonly to: ActivityPrimary
  readonly label?: string | null
  readonly detail?: ActivityDetail | null
  readonly source?: ActivityDetail["source"]
  readonly eventType?: string | null
  readonly now?: number
}

export interface ActivityTransitionResult {
  readonly snapshot: ActivitySnapshot
  readonly applied: boolean
  readonly reason: "applied" | "disabled" | "same" | "hysteresis" | "illegal"
}

export interface ThinkingSignal {
  readonly kind: "delta" | "summary"
  readonly text: string
  readonly eventType: string
}

export const resolveRuntimeBehaviorFlags = (env: NodeJS.ProcessEnv): RuntimeBehaviorFlags => ({
  activityEnabled: parseBoolEnv(env.BREADBOARD_ACTIVITY_ENABLED, true),
  lifecycleToastsEnabled: parseBoolEnv(env.BREADBOARD_ACTIVITY_LIFECYCLE_TOASTS, false),
  thinkingEnabled: parseBoolEnv(env.BREADBOARD_THINKING_ENABLED, true),
  allowFullThinking: parseBoolEnv(env.BREADBOARD_THINKING_FULL_OPT_IN, false),
  allowRawThinkingPeek: parseBoolEnv(env.BREADBOARD_THINKING_PEEK_RAW_ALLOWED, false),
  inlineThinkingBlockEnabled: parseBoolEnv(env.BREADBOARD_THINKING_INLINE_COLLAPSIBLE, false),
  markdownCoalescingEnabled: parseBoolEnv(env.BREADBOARD_MARKDOWN_COALESCING_ENABLED, true),
  adaptiveMarkdownCadenceEnabled: parseBoolEnv(env.BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE, false),
  transitionDebug: parseBoolEnv(env.BREADBOARD_ACTIVITY_TRANSITION_DEBUG, false),
  minDisplayMs: parseBoundedIntEnv(env.BREADBOARD_ACTIVITY_MIN_DISPLAY_MS, DEFAULT_MIN_DISPLAY_MS, 0, 60_000),
  statusUpdateMs: parseBoundedIntEnv(env.BREADBOARD_STATUS_UPDATE_MS, DEFAULT_STATUS_UPDATE_MS, 0, 10_000),
  thinkingMaxChars: parseBoundedIntEnv(env.BREADBOARD_THINKING_MAX_CHARS, DEFAULT_THINKING_MAX_CHARS, 16, 20_000),
  thinkingMaxLines: parseBoundedIntEnv(env.BREADBOARD_THINKING_MAX_LINES, DEFAULT_THINKING_MAX_LINES, 1, 200),
  adaptiveMarkdownMinChunkChars: parseBoundedIntEnv(
    env.BREADBOARD_MARKDOWN_ADAPTIVE_MIN_CHUNK_CHARS,
    DEFAULT_ADAPTIVE_MIN_CHUNK_CHARS,
    1,
    200,
  ),
  adaptiveMarkdownMinCoalesceMs: parseBoundedIntEnv(
    env.BREADBOARD_MARKDOWN_ADAPTIVE_MIN_COALESCE_MS,
    DEFAULT_ADAPTIVE_MIN_COALESCE_MS,
    0,
    5000,
  ),
  adaptiveMarkdownBurstChars: parseBoundedIntEnv(
    env.BREADBOARD_MARKDOWN_ADAPTIVE_BURST_CHARS,
    DEFAULT_ADAPTIVE_BURST_CHARS,
    1,
    10_000,
  ),
  subagentWorkGraphEnabled: parseBoolEnv(env.BREADBOARD_SUBAGENTS_V2_ENABLED, false),
  subagentStripEnabled: parseBoolEnv(env.BREADBOARD_SUBAGENTS_STRIP_ENABLED, false),
  subagentToastsEnabled: parseBoolEnv(env.BREADBOARD_SUBAGENTS_TOASTS_ENABLED, false),
  subagentTaskboardEnabled: parseBoolEnv(env.BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED, false),
  subagentFocusEnabled: parseBoolEnv(env.BREADBOARD_SUBAGENTS_FOCUS_ENABLED, false),
  subagentCoalesceMs: parseBoundedIntEnv(
    env.BREADBOARD_SUBAGENTS_COALESCE_MS,
    DEFAULT_SUBAGENT_COALESCE_MS,
    0,
    5000,
  ),
  subagentMaxWorkItems: parseBoundedIntEnv(
    env.BREADBOARD_SUBAGENTS_MAX_WORK_ITEMS,
    DEFAULT_SUBAGENT_MAX_WORK_ITEMS,
    1,
    5000,
  ),
  subagentMaxStepsPerTask: parseBoundedIntEnv(
    env.BREADBOARD_SUBAGENTS_MAX_STEPS_PER_TASK,
    DEFAULT_SUBAGENT_MAX_STEPS_PER_TASK,
    1,
    5000,
  ),
})

export const createRuntimeTelemetry = (): RuntimeTelemetry => ({
  statusTransitions: 0,
  suppressedTransitions: 0,
  illegalTransitions: 0,
  markdownFlushes: 0,
  thinkingUpdates: 0,
  adaptiveCadenceAdjustments: 0,
  workgraphFlushes: 0,
  workgraphEvents: 0,
  workgraphMaxQueueDepth: 0,
})

export const bumpTelemetry = (
  telemetry: RuntimeTelemetry,
  key: keyof RuntimeTelemetry,
  by = 1,
): RuntimeTelemetry => ({ ...telemetry, [key]: telemetry[key] + by })

export const defaultActivityLabel = (primary: ActivityPrimary): string => PRIMARY_LABELS[primary]

export const isLegalActivityTransition = (from: ActivityPrimary, to: ActivityPrimary): boolean =>
  LEGAL_TRANSITIONS[from].includes(to)

export const activityPriority = (primary: ActivityPrimary): number => PRIMARY_PRIORITY[primary]

export const createActivitySnapshot = (
  primary: ActivityPrimary,
  now = Date.now(),
  seq = 0,
): ActivitySnapshot => ({
  primary,
  label: defaultActivityLabel(primary),
  detail: null,
  updatedAt: now,
  displayedAt: now,
  seq,
})

const detailsEqual = (a?: ActivityDetail | null, b?: ActivityDetail | null): boolean =>
  (a?.message ?? null) === (b?.message ?? null) &&
  (a?.eventType ?? null) === (b?.eventType ?? null) &&
  (a?.source ?? null) === (b?.source ?? null)

export const reduceActivityTransition = (
  current: ActivitySnapshot,
  input: ActivityTransitionInput,
  flags: RuntimeBehaviorFlags,
): ActivityTransitionResult => {
  if (!flags.activityEnabled) {
    return { snapshot: current, applied: false, reason: "disabled" }
  }
  const now = typeof input.now === "number" && Number.isFinite(input.now) ? input.now : Date.now()
  const detail: ActivityDetail | null =
    input.detail ?? input.source ?? input.eventType
      ? {
          ...(input.detail ?? {}),
          ...(input.source ? { source: input.source } : {}),
          ...(input.eventType ? { eventType: input.eventType } : {}),
        }
      : null
  const nextLabel = input.label?.trim() || defaultActivityLabel(input.to)
  if (current.primary === input.to && current.label === nextLabel && detailsEqual(current.detail, detail)) {
    return { snapshot: current, applied: false, reason: "same" }
  }
  if (!isLegalActivityTransition(current.primary, input.to)) {
    return { snapshot: current, applied: false, reason: "illegal" }
  }
  const elapsed = Math.max(0, now - current.displayedAt)
  const incomingPriority = activityPriority(input.to)
  const currentPriority = activityPriority(current.primary)
  const terminalStates: ActivityPrimary[] = ["completed", "halted", "cancelled", "error"]
  const leavingTerminal = terminalStates.includes(current.primary) && !terminalStates.includes(input.to)
  const resumingAfterPermission =
    current.primary === "permission_resolved" &&
    (input.to === "thinking" || input.to === "responding" || input.to === "tool_call")
  const resumingAfterReconnect =
    current.primary === "reconnecting" &&
    (input.to === "thinking" || input.to === "responding" || input.to === "run")
  const resumingAfterCompaction =
    current.primary === "compacting" &&
    (input.to === "session" || input.to === "run" || input.to === "thinking" || input.to === "responding")
  if (
    !leavingTerminal &&
    !resumingAfterPermission &&
    !resumingAfterReconnect &&
    !resumingAfterCompaction &&
    elapsed < flags.minDisplayMs &&
    incomingPriority <= currentPriority
  ) {
    return { snapshot: current, applied: false, reason: "hysteresis" }
  }
  return {
    snapshot: {
      primary: input.to,
      label: nextLabel,
      detail,
      updatedAt: now,
      displayedAt: now,
      seq: current.seq + 1,
    },
    applied: true,
    reason: "applied",
  }
}

const truncateByLines = (text: string, maxLines: number): { text: string; truncated: boolean } => {
  const lines = text.split(/\r?\n/)
  if (lines.length <= maxLines) return { text, truncated: false }
  return { text: lines.slice(0, maxLines).join("\n"), truncated: true }
}

const truncateByChars = (text: string, maxChars: number): { text: string; truncated: boolean } => {
  if (text.length <= maxChars) return { text, truncated: false }
  return { text: `${text.slice(0, Math.max(0, maxChars - 1))}…`, truncated: true }
}

export const createThinkingArtifact = (
  mode: ThinkingMode,
  now = Date.now(),
  id?: string,
): ThinkingArtifact => ({
  id: id ?? `thinking-${now.toString(36)}`,
  mode,
  startedAt: now,
  updatedAt: now,
  finalizedAt: null,
  summary: "",
  rawText: mode === "full" ? "" : null,
  summaryTruncated: false,
  rawTruncated: false,
  sourceEventTypes: [],
})

export const appendThinkingArtifact = (
  artifact: ThinkingArtifact,
  signal: ThinkingSignal,
  flags: RuntimeBehaviorFlags,
  now = Date.now(),
): ThinkingArtifact => {
  const baseSummary = artifact.summary ? `${artifact.summary}\n${signal.text}` : signal.text
  const lineSummary = truncateByLines(baseSummary, flags.thinkingMaxLines)
  const charSummary = truncateByChars(lineSummary.text, flags.thinkingMaxChars)
  const nextSummary = charSummary.text
  const nextRaw =
    artifact.mode === "full"
      ? (() => {
          const rawBase = `${artifact.rawText ?? ""}${signal.text}`
          const rawTrim = truncateByChars(rawBase, flags.thinkingMaxChars * 4)
          return { text: rawTrim.text, truncated: rawTrim.truncated }
        })()
      : { text: null as string | null, truncated: false }
  return {
    ...artifact,
    updatedAt: now,
    summary: nextSummary,
    summaryTruncated: Boolean(artifact.summaryTruncated || lineSummary.truncated || charSummary.truncated),
    rawText: nextRaw.text,
    rawTruncated: Boolean(artifact.rawTruncated || nextRaw.truncated),
    sourceEventTypes: [...(artifact.sourceEventTypes ?? []), signal.eventType].slice(-20),
  }
}

export const finalizeThinkingArtifact = (artifact: ThinkingArtifact, now = Date.now()): ThinkingArtifact => ({
  ...artifact,
  updatedAt: now,
  finalizedAt: now,
})

export const normalizeThinkingSignal = (eventType: string, payload: unknown): ThinkingSignal | null => {
  const obj = isRecord(payload) ? payload : {}
  const delta =
    extractRawString(obj, ["delta", "text", "summary", "thought_summary"]) ??
    (typeof obj.reasoning === "string" ? obj.reasoning : undefined)
  if (!delta || !delta.trim()) return null
  const kind = eventType.includes("thought_summary") ? "summary" : "delta"
  return {
    kind,
    text: delta,
    eventType,
  }
}
