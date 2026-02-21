import type {
  ActivityDetail,
  ActivityPrimary,
  ActivitySnapshot,
  RuntimeBehaviorFlags,
  RuntimeTelemetry,
  ThinkingArtifact,
  ThinkingPreviewState,
  ThinkingMode,
} from "../../repl/types.js"
import { extractRawString, isRecord, parseNumberish } from "./controllerUtils.js"

const DEFAULT_MIN_DISPLAY_MS = 200
const DEFAULT_STATUS_UPDATE_MS = 120
const DEFAULT_EVENT_COALESCE_MS = 80
const DEFAULT_EVENT_COALESCE_MAX_BATCH = 128
const DEFAULT_THINKING_MAX_CHARS = 600
const DEFAULT_THINKING_MAX_LINES = 6
const DEFAULT_THINKING_PREVIEW_MAX_LINES = 5
const DEFAULT_THINKING_PREVIEW_TTL_MS = 1400
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
  readonly provider?: string | null
}

export const resolveRuntimeBehaviorFlags = (env: NodeJS.ProcessEnv): RuntimeBehaviorFlags => ({
  activityEnabled: parseBoolEnv(env.BREADBOARD_ACTIVITY_ENABLED, true),
  lifecycleToastsEnabled: parseBoolEnv(env.BREADBOARD_ACTIVITY_LIFECYCLE_TOASTS, false),
  thinkingEnabled: parseBoolEnv(env.BREADBOARD_THINKING_ENABLED, true),
  thinkingPreviewEnabled: parseBoolEnv(env.BREADBOARD_THINKING_PREVIEW_ENABLED, true),
  allowFullThinking: parseBoolEnv(env.BREADBOARD_THINKING_FULL_OPT_IN, false),
  allowRawThinkingPeek: parseBoolEnv(env.BREADBOARD_THINKING_PEEK_RAW_ALLOWED, false),
  inlineThinkingBlockEnabled: parseBoolEnv(env.BREADBOARD_THINKING_INLINE_COLLAPSIBLE, false),
  markdownCoalescingEnabled: parseBoolEnv(env.BREADBOARD_MARKDOWN_COALESCING_ENABLED, true),
  adaptiveMarkdownCadenceEnabled: parseBoolEnv(env.BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE, false),
  transitionDebug: parseBoolEnv(env.BREADBOARD_ACTIVITY_TRANSITION_DEBUG, false),
  minDisplayMs: parseBoundedIntEnv(env.BREADBOARD_ACTIVITY_MIN_DISPLAY_MS, DEFAULT_MIN_DISPLAY_MS, 0, 60_000),
  statusUpdateMs: parseBoundedIntEnv(env.BREADBOARD_STATUS_UPDATE_MS, DEFAULT_STATUS_UPDATE_MS, 0, 10_000),
  eventCoalesceMs: parseBoundedIntEnv(
    env.BREADBOARD_EVENT_COALESCE_MS,
    DEFAULT_EVENT_COALESCE_MS,
    0,
    5000,
  ),
  eventCoalesceMaxBatch: parseBoundedIntEnv(
    env.BREADBOARD_EVENT_COALESCE_MAX_BATCH,
    DEFAULT_EVENT_COALESCE_MAX_BATCH,
    1,
    10_000,
  ),
  thinkingMaxChars: parseBoundedIntEnv(env.BREADBOARD_THINKING_MAX_CHARS, DEFAULT_THINKING_MAX_CHARS, 16, 20_000),
  thinkingMaxLines: parseBoundedIntEnv(env.BREADBOARD_THINKING_MAX_LINES, DEFAULT_THINKING_MAX_LINES, 1, 200),
  thinkingPreviewMaxLines: parseBoundedIntEnv(
    env.BREADBOARD_THINKING_PREVIEW_MAX_LINES,
    DEFAULT_THINKING_PREVIEW_MAX_LINES,
    1,
    24,
  ),
  thinkingPreviewTtlMs: parseBoundedIntEnv(
    env.BREADBOARD_THINKING_PREVIEW_TTL_MS,
    DEFAULT_THINKING_PREVIEW_TTL_MS,
    0,
    60_000,
  ),
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
  statusCommits: 0,
  statusCoalesced: 0,
  markdownFlushes: 0,
  thinkingUpdates: 0,
  thinkingPreviewOpened: 0,
  thinkingPreviewClosed: 0,
  thinkingPreviewExpired: 0,
  adaptiveCadenceAdjustments: 0,
  eventFlushes: 0,
  eventCoalesced: 0,
  eventMaxQueueDepth: 0,
  optimisticToolRows: 0,
  optimisticToolReconciled: 0,
  optimisticDiffRows: 0,
  optimisticDiffReconciled: 0,
  workgraphFlushes: 0,
  workgraphEvents: 0,
  workgraphMaxQueueDepth: 0,
  workgraphLaneTransitions: 0,
  workgraphDroppedTransitions: 0,
  workgraphLaneChurn: 0,
  workgraphDroppedEvents: 0,
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

const normalizeThinkingText = (value: unknown): string | null => {
  if (typeof value === "string") {
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : null
  }
  if (Array.isArray(value)) {
    const parts = value
      .map((entry) => {
        if (typeof entry === "string") return entry
        const obj = isRecord(entry) ? entry : {}
        return (
          extractRawString(obj, ["text", "summary", "value", "content"]) ??
          extractRawString(obj, ["output_text", "reasoning"])
        )
      })
      .filter((entry): entry is string => Boolean(entry && entry.trim().length > 0))
    if (parts.length === 0) return null
    return parts.join("\n")
  }
  return null
}

const extractOpenAiThinkingText = (payload: Record<string, unknown>): string | null =>
  normalizeThinkingText(payload.summary) ??
  normalizeThinkingText(payload.reasoning) ??
  normalizeThinkingText(payload.reasoning_summary) ??
  normalizeThinkingText(payload.output_text) ??
  normalizeThinkingText(payload.output)

const extractAnthropicThinkingText = (payload: Record<string, unknown>): string | null =>
  normalizeThinkingText(payload.thinking_delta) ??
  normalizeThinkingText(payload.thinking) ??
  (() => {
    const block = isRecord(payload.content_block) ? payload.content_block : {}
    return normalizeThinkingText(block.thinking ?? block.text ?? block.summary)
  })() ??
  normalizeThinkingText(payload.delta) ??
  normalizeThinkingText(payload.text)

const truncateThinkingPreviewLine = (line: string, maxChars: number): string => {
  if (line.length <= maxChars) return line
  const safe = Math.max(1, maxChars - 1)
  return `${line.slice(0, safe)}…`
}

const splitThinkingPreviewLines = (text: string, maxChars = 240): string[] =>
  text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => truncateThinkingPreviewLine(line, maxChars))

const normalizeReasoningEventType = (
  eventType: string,
  payload: Record<string, unknown>,
): "summary" | "delta" => {
  if (eventType.includes("thought_summary")) return "summary"
  const hasSummaryShape =
    payload.summary != null || payload.thought_summary != null || payload.reasoning_summary != null
  return hasSummaryShape ? "summary" : "delta"
}

export const createThinkingPreviewState = (
  mode: ThinkingMode,
  now = Date.now(),
  id?: string,
): ThinkingPreviewState => ({
  id: id ?? `thinking-preview-${now.toString(36)}`,
  mode,
  lifecycle: "open",
  startedAt: now,
  updatedAt: now,
  closedAt: null,
  eventCount: 0,
  lines: [],
  truncated: false,
  sourceEventTypes: [],
})

export const appendThinkingPreviewState = (
  state: ThinkingPreviewState,
  signal: ThinkingSignal,
  flags: RuntimeBehaviorFlags,
  now = Date.now(),
): ThinkingPreviewState => {
  const maxLines = Math.max(1, Number(flags.thinkingPreviewMaxLines ?? DEFAULT_THINKING_PREVIEW_MAX_LINES))
  const nextLines = [...state.lines, ...splitThinkingPreviewLines(signal.text)]
  const normalizedLines = nextLines.slice(-maxLines)
  return {
    ...state,
    lifecycle: state.eventCount === 0 ? "open" : "updating",
    updatedAt: now,
    eventCount: state.eventCount + 1,
    lines: normalizedLines,
    truncated: state.truncated || nextLines.length > normalizedLines.length,
    sourceEventTypes: [...(state.sourceEventTypes ?? []), signal.eventType].slice(-20),
  }
}

export const closeThinkingPreviewState = (
  state: ThinkingPreviewState,
  now = Date.now(),
): ThinkingPreviewState => ({
  ...state,
  lifecycle: "closed",
  updatedAt: now,
  closedAt: now,
})

export const pruneThinkingPreviewState = (
  state: ThinkingPreviewState | null,
  flags: RuntimeBehaviorFlags,
  now = Date.now(),
): ThinkingPreviewState | null => {
  if (!state) return null
  if (state.lifecycle !== "closed") return state
  const ttlMs = Math.max(0, Number(flags.thinkingPreviewTtlMs ?? DEFAULT_THINKING_PREVIEW_TTL_MS))
  if (ttlMs <= 0) return null
  const closedAt = Number(state.closedAt ?? state.updatedAt)
  if (!Number.isFinite(closedAt)) return null
  return now - closedAt >= ttlMs ? null : state
}

export const normalizeThinkingSignal = (
  eventType: string,
  payload: unknown,
  options?: { provider?: string | null },
): ThinkingSignal | null => {
  const obj = isRecord(payload) ? payload : {}
  const provider = (options?.provider ?? "").trim().toLowerCase() || null
  const providerDelta =
    provider === "openai"
      ? extractOpenAiThinkingText(obj)
      : provider === "anthropic"
        ? extractAnthropicThinkingText(obj)
        : null
  const delta =
    providerDelta ??
    extractRawString(obj, ["delta", "text", "summary", "thought_summary", "thinking", "thinking_delta"]) ??
    normalizeThinkingText(obj.reasoning) ??
    normalizeThinkingText(obj.content)
  if (!delta || !delta.trim()) return null
  const kind = normalizeReasoningEventType(eventType, obj)
  return {
    kind,
    text: delta,
    eventType,
    provider,
  }
}
