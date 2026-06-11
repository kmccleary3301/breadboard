import { ApiError } from "../../api/client.js"
import type {
  CTreeNode,
  CTreeSnapshotSummary,
  SessionEvent,
} from "../../api/types.js"
import { reduceCTreeModel } from "../../repl/ctrees/reducer.js"
import { BRAND_COLORS, SEMANTIC_COLORS } from "../../repl/designSystem.js"
import { createEmptyTodoStore, reduceTodoStore } from "../../repl/todos/todoStore.js"
import { stripAnsi } from "../../repl/stringUtils.js"
import { INLINE_THINKING_MARKER } from "../../repl/transcriptUtils.js"
import type {
  CheckpointSummary,
  CTreeSnapshot,
  PermissionRequest,
  SkillCatalog,
  SkillSelection,
  SkillCatalogSources,
  ThinkingMode,
  TodoUpdate,
  TodoUpdatePatch,
} from "../../repl/types.js"
import {
  appendThinkingArtifact,
  appendThinkingPreviewState,
  closeThinkingPreviewState,
  createThinkingArtifact,
  createThinkingPreviewState,
  finalizeThinkingArtifact,
  normalizeThinkingSignal,
} from "./controllerActivityRuntime.js"
import { restartOwnedEngine } from "../../engine/engineSupervisor.js"
import {
  DEBUG_EVENTS,
  MAX_RETRIES,
  STREAM_STALL_TIMEOUT_MS,
  STOP_SOFT_TIMEOUT_MS,
  createSlotId,
  extractCtreeSnapshotSummary,
  extractRawString,
  extractString,
  formatErrorPayload,
  isRecord,
  normalizeModeValue,
  normalizePermissionMode,
  parseNumberish,
  sleep,
} from "./controllerUtils.js"
import { isLifecycleNoiseHint } from "./hintPolicy.js"
import { resolveSubagentUiPolicy, type SubagentUiPolicy } from "./subagentUiPolicy.js"
import { formatOneLinePreview } from "./textBudget.js"
type CompletionView = {
  completed: boolean
  status: string
  toolLine: string
  hint?: string
  conversationLine?: string
  warningSlot?: { text: string; color?: string }
  errorNotice?: { message: string; detail?: string[] }
}

const MAX_SEEN_EVENT_IDS = 2000
const formatDurationMs = (ms: number): string => {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const hours = Math.floor(minutes / 60)
  if (hours > 0) {
    const remMinutes = minutes % 60
    return `${hours}h ${remMinutes}m`
  }
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

const extractDurationMs = (payload: Record<string, unknown>): number | null => {
  const sources = [
    payload,
    isRecord(payload.summary) ? payload.summary : null,
    isRecord(payload.completion_summary) ? payload.completion_summary : null,
    isRecord(payload.usage) ? payload.usage : null,
  ].filter(Boolean) as Record<string, unknown>[]
  const numberFrom = (obj: Record<string, unknown>, keys: string[]): number | null => {
    for (const key of keys) {
      const value = parseNumberish(obj[key])
      if (value != null && Number.isFinite(value)) return value
    }
    return null
  }
  const msKeys = [
    "duration_ms",
    "durationMs",
    "elapsed_ms",
    "elapsedMs",
    "latency_ms",
    "latencyMs",
    "latency",
  ]
  const secKeys = [
    "duration_seconds",
    "durationSeconds",
    "elapsed_seconds",
    "elapsedSeconds",
    "latency_seconds",
    "latencySeconds",
  ]
  for (const source of sources) {
    const ms = numberFrom(source, msKeys)
    if (ms != null) return ms
  }
  for (const source of sources) {
    const seconds = numberFrom(source, secKeys)
    if (seconds != null) return seconds * 1000
  }
  return null
}

const clockNow = (controller: any): number => controller?.clock?.now?.() ?? Date.now()

const notePendingStart = (controller: { pendingStartedAt: number | null }): void => {
  if (controller.pendingStartedAt == null) {
    controller.pendingStartedAt = clockNow(controller)
  }
}

const takePendingDurationMs = (controller: { pendingStartedAt: number | null }): number | null => {
  const startedAt = controller.pendingStartedAt
  controller.pendingStartedAt = null
  if (typeof startedAt === "number" && Number.isFinite(startedAt)) {
    const elapsed = clockNow(controller) - startedAt
    return elapsed >= 0 ? elapsed : 0
  }
  return null
}

const extractNestedRecord = (
  source: Record<string, unknown>,
  keys: ReadonlyArray<string>,
): Record<string, unknown> | null => {
  for (const key of keys) {
    const value = source[key]
    if (isRecord(value)) return value
  }
  return null
}

const extractPermissionScope = (
  controller: { normalizeScope?: (value: unknown) => "session" | "project" | "global" },
  payload: Record<string, unknown>,
  action: Record<string, unknown> | null,
): "session" | "project" | "global" => {
  const scope =
    payload.default_scope ??
    payload.defaultScope ??
    payload.scope ??
    payload.effective_scope ??
    payload.effectiveScope ??
    action?.scope ??
    action?.default_scope
  return controller.normalizeScope?.(scope) ?? "project"
}

const extractPermissionString = (
  payload: Record<string, unknown>,
  action: Record<string, unknown> | null,
  keys: ReadonlyArray<string>,
): string | null => {
  const keyList = [...keys]
  const direct = extractString(payload, keyList)
  if (direct) return direct
  return action ? (extractString(action, keyList) ?? null) : null
}

const buildPermissionRequest = (
  controller: any,
  event: SessionEvent,
  requestId: string,
  payload: Record<string, unknown>,
  action: Record<string, unknown> | null,
  input: {
    readonly tool: string
    readonly kind: string
    readonly summary: string
    readonly diffText?: string | null
    readonly ruleSuggestion?: string | null
    readonly rewindable?: boolean
  },
): PermissionRequest => {
  const actor = isRecord(event.actor) ? event.actor : extractNestedRecord(payload, ["actor", "agent", "agent_info"])
  const task = extractNestedRecord(payload, ["task", "task_info", "work_item"]) ?? extractNestedRecord(action ?? {}, ["task", "task_info"])
  const defaultScope = extractPermissionScope(controller, payload, action)
  const effectiveScope =
    extractPermissionString(payload, action, ["effective_scope", "effectiveScope", "scope", "approval_scope", "approvalScope"]) ??
    defaultScope
  const reason =
    extractPermissionString(payload, action, ["reason", "why", "justification", "approval_reason", "approvalReason"]) ??
    input.summary
  const cwd = extractPermissionString(payload, action, ["cwd", "working_dir", "workingDirectory", "directory", "workspace"])
  const launchPermissionMode =
    extractPermissionString(payload, action, ["launch_permission_mode", "launchPermissionMode", "permission_mode", "permissionMode"]) ??
    controller?.permissionMode ??
    controller?.config?.permissionMode ??
    null
  const enginePermissionMode =
    extractPermissionString(payload, action, ["engine_permission_mode", "enginePermissionMode", "engine_mode", "engineMode"]) ??
    extractPermissionString(payload, null, ["policy_mode", "policyMode"]) ??
    null
  const agentId =
    extractPermissionString(payload, action, ["agent_id", "agentId", "agent"]) ??
    (actor ? extractString(actor, ["agent_id", "agentId", "id", "name"]) : null)
  const agentLabel =
    extractPermissionString(payload, action, ["agent_label", "agentLabel", "agent_name", "agentName"]) ??
    (actor ? extractString(actor, ["label", "name", "agent_name", "role"]) : null)
  const taskId =
    extractPermissionString(payload, action, ["task_id", "taskId", "work_id", "workId"]) ??
    (task ? extractString(task, ["task_id", "taskId", "work_id", "workId", "id"]) : null)
  const taskLabel =
    extractPermissionString(payload, action, ["task_label", "taskLabel", "task_name", "taskName", "title"]) ??
    (task ? extractString(task, ["label", "title", "description", "name"]) : null)
  return {
    requestId,
    tool: input.tool,
    kind: input.kind,
    rewindable: input.rewindable ?? (payload.rewindable === false ? false : true),
    summary: input.summary,
    diffText: input.diffText ?? null,
    ruleSuggestion: input.ruleSuggestion ?? null,
    defaultScope,
    effectiveScope,
    cwd,
    reason,
    launchPermissionMode,
    enginePermissionMode,
    agentId,
    agentLabel,
    taskId,
    taskLabel,
    createdAt: clockNow(controller),
  }
}

const resolveThinkingMode = (controller: {
  viewPrefs?: { showReasoning?: boolean }
  runtimeFlags?: { allowFullThinking?: boolean; thinkingEnabled?: boolean }
}): ThinkingMode =>
  controller.viewPrefs?.showReasoning && controller.runtimeFlags?.allowFullThinking === true
    ? "full"
    : "summary"

const maybeStartThinkingPreview = (controller: any, mode: ThinkingMode, now = clockNow(controller)): void => {
  if (controller.runtimeFlags?.thinkingPreviewEnabled === false) {
    controller.thinkingPreview = null
    return
  }
  if (controller.thinkingPreview && controller.thinkingPreview.lifecycle !== "closed") {
    controller.thinkingPreview = closeThinkingPreviewState(controller.thinkingPreview, now)
    controller.bumpRuntimeTelemetry?.("thinkingPreviewClosed")
  }
  controller.thinkingPreview = createThinkingPreviewState(mode, now)
  controller.bumpRuntimeTelemetry?.("thinkingPreviewOpened")
}

const appendThinkingPreview = (
  controller: any,
  signal: { kind: "delta" | "summary"; text: string; eventType: string },
  now = clockNow(controller),
): void => {
  if (controller.runtimeFlags?.thinkingPreviewEnabled === false) return
  const mode: ThinkingMode = resolveThinkingMode(controller)
  const rawAllowed =
    controller.runtimeFlags?.allowFullThinking === true &&
    controller.runtimeFlags?.allowRawThinkingPeek === true &&
    controller.viewPrefs?.showReasoning === true
  const allowedText = signal.kind === "summary" || rawAllowed || DEBUG_EVENTS ? signal.text : ""
  if (!allowedText.trim()) return
  const seed = controller.thinkingPreview ?? createThinkingPreviewState(mode, now)
  const opened = !controller.thinkingPreview
  controller.thinkingPreview = appendThinkingPreviewState(seed, { ...signal, text: allowedText }, controller.runtimeFlags, now)
  if (opened) {
    controller.bumpRuntimeTelemetry?.("thinkingPreviewOpened")
  }
}

const closeThinkingPreview = (controller: any, now = clockNow(controller)): void => {
  const current = controller.thinkingPreview
  if (!current || current.lifecycle === "closed") return
  controller.thinkingPreview = closeThinkingPreviewState(current, now)
  controller.bumpRuntimeTelemetry?.("thinkingPreviewClosed")
}

const COMPLETION_SENTINEL = ">>>>>> END RESPONSE"

const hasAssistantCompletionSentinel = (text: string): boolean => text.includes(COMPLETION_SENTINEL)

const stripAssistantCompletionSentinel = (text: string): string => {
  if (!hasAssistantCompletionSentinel(text)) return text
  return text.replace(COMPLETION_SENTINEL, "").replace(/\s+$/g, "")
}

const settleFromAssistantCompletionSentinel = (controller: any, eventType: string): void => {
  if (controller.completionSeen) return
  controller.finalizeStreamingEntry()
  controller.clearStopRequest?.()
  controller.completionReached = true
  controller.completionSeen = true
  controller.activePromptOutcomeUnresolved = false
  controller.lastCompletion = {
    completed: true,
    summary: {
      completed: true,
      method: "assistant_content",
      reason: "explicit_completion_marker",
      source: "assistant_content",
    },
  }
  controller.pendingResponse = false
  controller.pendingResponseEventCountAtSubmit = null
  controller.ownedEngineRecoveryAttempts = 0
  controller.pendingStartedAt = null
  settlePendingToolLogEntries(controller, "success")
  controller.thinkingInlineEntryId = null
  closeThinkingPreview(controller, clockNow(controller))
  if (controller.thinkingArtifact) {
    controller.thinkingArtifact = finalizeThinkingArtifact(controller.thinkingArtifact, clockNow(controller))
  }
  controller.setActivityStatus?.("Finished", {
    to: "completed",
    eventType,
    source: "runtime",
  })
}

const settlePendingToolLogEntries = (controller: any, status: "success" | "error"): void => {
  if (!Array.isArray(controller.toolEvents)) return
  let changed = false
  controller.toolEvents = controller.toolEvents.map((entry: any) => {
    if (entry?.status !== "pending") return entry
    changed = true
    return { ...entry, status }
  })
  if (changed && !controller.eventsScheduled) controller.emitChange?.()
}

const surfaceTerminalError = (
  controller: any,
  message: string,
  eventType: string,
  detail?: string[] | null,
  options?: { includeConversation?: boolean },
): void => {
  controller.finalizeStreamingEntry()
  const displayMessage = compactDiagnosticMessage(message)
  if (markTerminalErrorUnseen(controller, message)) {
    if (options?.includeConversation) {
      controller.addConversation("system", `[error] ${displayMessage}`)
    }
    controller.setGuardrailNotice?.(`Error: ${displayMessage}`, detail && detail.length > 0 ? detail.join("\n") : undefined)
    controller.addTool(
      "error",
      `[error] ${displayMessage}`,
      "error",
      detail && detail.length > 0
        ? {
            display: {
              title: "Runtime error",
              summary: displayMessage,
              detail,
            },
          }
        : undefined,
    )
  }
  controller.activePromptOutcomeUnresolved = false
  controller.pendingResponse = false
  controller.pendingResponseEventCountAtSubmit = null
  controller.ownedEngineRecoveryAttempts = 0
  controller.pendingStartedAt = null
  controller.removeLiveSlot?.("provider-retry")
  settlePendingToolLogEntries(controller, "error")
  controller.thinkingInlineEntryId = null
  closeThinkingPreview(controller, clockNow(controller))
  if (controller.thinkingArtifact) {
    controller.thinkingArtifact = finalizeThinkingArtifact(controller.thinkingArtifact, clockNow(controller))
  }
  controller.setActivityStatus?.("Error received", { to: "error", eventType, source: "event" })
}

type CtreeTranscriptNotice =
  | { severity: "warning"; message: string }
  | { severity: "error"; message: string; detail?: string[] }

const extractCtreeTranscriptNotice = (payload: Record<string, unknown>): CtreeTranscriptNotice | null => {
  const node = isRecord(payload.node) ? payload.node : null
  const nodePayload = node && isRecord(node.payload) ? node.payload : null
  if (!nodePayload) return null
  const transcript = isRecord(nodePayload.transcript) ? nodePayload.transcript : null
  const source = transcript ?? nodePayload

  const streamingDisabled = isRecord(source.streaming_disabled) ? source.streaming_disabled : null
  if (streamingDisabled) {
    const provider = extractString(streamingDisabled, ["provider"]) ?? "unknown"
    const runtime = extractString(streamingDisabled, ["runtime"])
    const reason = formatErrorPayload(streamingDisabled)
    const providerLabel = runtime ? `Provider ${provider} (${runtime})` : `Provider ${provider}`
    return {
      severity: "warning",
      message: `${providerLabel} rejected streaming: ${reason}. Falling back to non-streaming.`,
    }
  }

  const providerRetry = isRecord(source.provider_retry) ? source.provider_retry : null
  if (providerRetry) {
    const route = extractString(providerRetry, ["route"]) ?? "unknown"
    const attempt = parseNumberish(providerRetry.attempt)
    const reason = formatErrorPayload(providerRetry)
    const attemptLabel = attempt != null ? ` (attempt ${attempt})` : ""
    const compactReason = compactDiagnosticMessage(reason)
    const authScopeReason = compactReason.match(/^OpenAI auth failed: missing scope ([^.]+(?:\.[^.]+)*)\.$/i)
    return {
      severity: "warning",
      message: authScopeReason
        ? `Provider retry blocked: missing OpenAI scope ${authScopeReason[1]}.`
        : `Retrying provider route ${route}${attemptLabel}: ${compactReason}`,
    }
  }

  const circuitOpen = isRecord(source.circuit_open) ? source.circuit_open : null
  if (circuitOpen) {
    const model = extractString(circuitOpen, ["model"]) ?? "unknown"
    const notice = extractString(circuitOpen, ["notice", "message"]) ?? formatErrorPayload(circuitOpen)
    return {
      severity: "warning",
      message: `Provider circuit open for ${model}: ${notice}`,
    }
  }

  const runLoopException = isRecord(source.run_loop_exception) ? source.run_loop_exception : null
  if (runLoopException) {
    const message = formatErrorPayload(runLoopException)
    const traceback = extractRawString(runLoopException, ["traceback"])
    const detail = traceback
      ? traceback.split(/\r?\n/).map((line) => line.trimEnd()).filter(Boolean).slice(0, 8)
      : []
    return {
      severity: "error",
      message,
      detail,
    }
  }

  return null
}

const noticeKey = (notice: CtreeTranscriptNotice): string =>
  `${notice.severity}:${semanticDiagnosticKey(notice.message)}`.replace(/\s+/g, " ").trim()

const semanticDiagnosticKey = (message: string): string => {
  const normalized = message.replace(/\s+/g, " ").trim()
  const lower = normalized.toLowerCase()
  const scopeMatch = lower.match(/missing scopes?:?\s+([a-z0-9_.:-]+)/i)
  const scope = scopeMatch?.[1]?.replace(/[.:-]+$/, "")
  if (
    lower.includes("insufficient_quota") ||
    lower.includes("exceeded your current quota") ||
    lower.includes("provider quota exceeded")
  ) {
    return "provider-quota"
  }
  if (
    lower.includes("context_length_exceeded") ||
    lower.includes("maximum context length") ||
    lower.includes("provider context limit exceeded")
  ) {
    return "provider-context-limit"
  }
  if (lower.includes("rate limit") || lower.includes("rate_limit_error") || lower.includes("provider rate limit hit")) {
    return "provider-rate-limit"
  }
  if (lower.includes("retrying provider route") && scope) {
    return `provider-retry-auth-scope:${scope}`
  }
  if ((lower.includes("error code: 401") || lower.includes("insufficient permissions")) && scope) {
    return `provider-auth-scope:${scope}`
  }
  if (lower.includes("error code: 401") || lower.includes("insufficient permissions")) {
    return "provider-auth"
  }
  return normalized
}

const terminalErrorKey = (message: string): string => `error:${semanticDiagnosticKey(message)}`

const compactDiagnosticMessage = (message: string): string => {
  const normalized = message.replace(/\s+/g, " ").trim()
  const lower = normalized.toLowerCase()
  const scopeMatch = lower.match(/missing scopes?:?\s+([a-z0-9_.:-]+)/i)
  const scope = scopeMatch?.[1]?.replace(/[.:-]+$/, "")
  if ((lower.includes("error code: 401") || lower.includes("insufficient permissions")) && scope) {
    return `OpenAI auth failed: missing scope ${scope}.`
  }
  if (
    lower.includes("insufficient_quota") ||
    lower.includes("exceeded your current quota") ||
    (lower.includes("quota") && lower.includes("billing"))
  ) {
    return "Provider quota exceeded. Check billing/quota or switch routes."
  }
  if (
    lower.includes("context_length_exceeded") ||
    lower.includes("maximum context length") ||
    (lower.includes("context length") && lower.includes("tokens"))
  ) {
    return "Provider context limit exceeded. Compact context or reduce prompt size."
  }
  if (lower.includes("rate limit") || lower.includes("rate_limit_error")) {
    return "Provider rate limit hit. Retry later or switch routes."
  }
  return normalized
}

const markTerminalErrorUnseen = (controller: any, message: string): boolean => {
  const key = terminalErrorKey(message)
  const seen = controller.__breadboardSeenTerminalErrorKeys instanceof Set
    ? controller.__breadboardSeenTerminalErrorKeys
    : new Set<string>()
  controller.__breadboardSeenTerminalErrorKeys = seen
  if (seen.has(key)) return false
  seen.add(key)
  if (seen.size > 100) {
    const first = seen.values().next().value
    if (first) seen.delete(first)
  }
  return true
}

const isStreamingFallbackTranscriptNotice = (notice: CtreeTranscriptNotice): boolean =>
  notice.severity === "warning" &&
  /rejected streaming|streaming_disabled|response\.keep_alive|response\.created|Falling back to non-streaming|You exceeded your current quota/i.test(notice.message)

const isProviderRetryTranscriptNotice = (notice: CtreeTranscriptNotice): boolean =>
  notice.severity === "warning" &&
  /^(?:Retrying provider route|Provider retry blocked:)/i.test(notice.message)

const shouldSurfaceTranscriptNotice = (controller: any, notice: CtreeTranscriptNotice): boolean => {
  const key = noticeKey(notice)
  const seen = controller.__breadboardSeenTranscriptNoticeKeys instanceof Set
    ? controller.__breadboardSeenTranscriptNoticeKeys
    : new Set<string>()
  controller.__breadboardSeenTranscriptNoticeKeys = seen
  if (seen.has(key)) return false
  seen.add(key)
  if (seen.size > 100) {
    const first = seen.values().next().value
    if (first) seen.delete(first)
  }
  return true
}

const userFacingTranscriptNotice = (notice: CtreeTranscriptNotice): string => {
  if (isStreamingFallbackTranscriptNotice(notice)) {
    return "Streaming fallback active; using non-streaming response for this turn."
  }
  return notice.message
}

const extractCompletionErrorNotice = (payload: unknown): { message: string; detail?: string[] } | null => {
  const data = isRecord(payload) ? payload : null
  if (!data) return null
  const summary = isRecord(data.summary) ? data.summary : null
  const error = isRecord(summary?.error) ? summary.error : isRecord(data.error) ? data.error : null
  if (!error) return null
  const message = formatErrorPayload(error).trim()
  if (!message) return null
  const traceback = extractRawString(error, ["traceback", "stack"])
  const detail = traceback
    ? traceback.split(/\r?\n/).map((line) => line.trimEnd()).filter(Boolean).slice(0, 8)
    : []
  return { message, detail: detail.length > 0 ? detail : undefined }
}

const markThinkingActive = (controller: any, eventType: string): void => {
  controller.setActivityStatus?.("Assistant thinking…", {
    to: "thinking",
    eventType,
    source: "event",
  })
}

const markAssistantResponding = (controller: any, eventType: string): void => {
  if (!controller.pendingResponse) return
  controller.setActivityStatus?.("Assistant responding…", {
    to: "responding",
    eventType,
    source: "event",
  })
}

const maybeLifecycleToast = (controller: any, message: string, eventType: string): void => {
  if (controller.runtimeFlags?.lifecycleToastsEnabled !== true) return
  const now = clockNow(controller)
  const cooldownMs = Math.max(300, Number(controller.runtimeFlags?.statusUpdateMs ?? 120))
  if (
    controller.lastLifecycleToastMessage === message &&
    Number.isFinite(controller.lastLifecycleToastAt) &&
    now - Number(controller.lastLifecycleToastAt) < cooldownMs
  ) {
    return
  }
  controller.lastLifecycleToastAt = now
  controller.lastLifecycleToastMessage = message
  controller.upsertLiveSlot?.(
    "lifecycle-toast",
    `[lifecycle] ${message}`,
    BRAND_COLORS.duneOrange,
    "pending",
    1100,
    eventType,
  )
}

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
  return formatOneLinePreview(value, { maxChars, fallback: "task", ellipsis: "..." }).text
}

const emitSubagentToast = (
  controller: any,
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
  const ledger = controller.subagentToastLedger as Map<string, { status: string; at: number }>
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

const shouldThrottleReasoningPeek = (controller: any, now: number): boolean => {
  const cadenceMs = Math.max(50, Number(controller.runtimeFlags?.statusUpdateMs ?? 120))
  const lastUpdatedAt = Number(controller.lastThinkingPeekAt ?? 0)
  if (!Number.isFinite(lastUpdatedAt)) return false
  return now - lastUpdatedAt < cadenceMs
}

const resolveEventTimestamp = (controller: any): number => {
  const seq = controller.currentEventSeq
  return typeof seq === "number" && Number.isFinite(seq) ? seq : clockNow(controller)
}

const isInlineThinkingBlockEnabled = (controller: any): boolean => {
  const flags = controller.runtimeFlags ?? {}
  const capabilities = controller.providerCapabilities ?? {}
  if (flags.inlineThinkingBlockEnabled !== true) return false
  if (flags.thinkingEnabled === false) return false
  if (capabilities.inlineThinkingBlock === false) return false
  return capabilities.reasoningEvents !== false || capabilities.thoughtSummaryEvents !== false
}

const appendInlineThinkingBlock = (
  controller: any,
  signal: { kind: "delta" | "summary"; text: string },
): void => {
  if (!isInlineThinkingBlockEnabled(controller)) return
  const normalized = signal.text.replace(/\r\n?/g, "\n").trim()
  if (!normalized) return
  const allowRawInline =
    controller.runtimeFlags?.allowFullThinking === true &&
    controller.runtimeFlags?.allowRawThinkingPeek === true &&
    controller.viewPrefs?.showReasoning === true
  if (signal.kind === "delta" && !allowRawInline) return
  const marker = INLINE_THINKING_MARKER
  const linePrefix = signal.kind === "summary" ? "summary: " : "delta: "
  const line = `${linePrefix}${normalized}`
  const maxLines = 12
  if (controller.thinkingInlineEntryId) {
    const index = controller.conversation.findIndex(
      (entry: { id: string }) => entry.id === controller.thinkingInlineEntryId,
    )
    if (index >= 0) {
      const existing = controller.conversation[index]
      const body = String(existing.text ?? "")
      const lines = body
        .replace(/\r\n?/g, "\n")
        .split("\n")
        .filter((item: string) => item.length > 0)
      const markerPresent = lines[0] === marker
      const payloadLines = markerPresent ? lines.slice(1) : lines
      if (payloadLines[payloadLines.length - 1] === line) return
      const nextPayload = [...payloadLines, line].slice(-maxLines)
      const nextText = `${marker}\n${nextPayload.join("\n")}`
      controller.conversation[index] = {
        ...existing,
        speaker: "assistant",
        text: nextText,
        phase: "final",
      }
      return
    }
  }
  const entry = {
    id: controller.nextConversationId?.() ?? `conv-thinking-${clockNow(controller).toString(36)}`,
    speaker: "assistant" as const,
    text: `${marker}\n${line}`,
    phase: "final" as const,
    createdAt: resolveEventTimestamp(controller),
  }
  controller.conversation.push(entry)
  controller.thinkingInlineEntryId = entry.id
}

const hasEquivalentFinalAssistantEntry = (controller: any, text: string): boolean => {
  const normalized = String(text ?? "").trim()
  if (!normalized) return false
  const conversation = Array.isArray(controller.conversation) ? controller.conversation : []
  for (let index = conversation.length - 1; index >= 0; index -= 1) {
    const entry = conversation[index]
    if (!entry || entry.speaker !== "assistant" || entry.phase !== "final") continue
    const previous = String(entry.text ?? "").trim()
    if (!previous) continue
    if (previous === normalized || previous.startsWith(normalized) || normalized.startsWith(previous)) {
      return true
    }
    break
  }
  return false
}

const hasUnresolvedSubmittedPromptOutcome = (controller: any): boolean => {
  if (controller.activePromptOutcomeUnresolved === true) return true
  if (controller.completionSeen === true || controller.lastCompletion != null) return false
  const conversation = Array.isArray(controller.conversation) ? controller.conversation : []
  return conversation.some((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").trim().length > 0)
}

const normalizeRecoveryTaskStatus = (value: unknown): string => {
  const raw = String(value ?? "").toLowerCase()
  if (raw.includes("complete") || raw.includes("done") || raw.includes("success")) return "completed"
  if (raw.includes("fail") || raw.includes("error")) return "failed"
  if (raw.includes("block") || raw.includes("wait")) return "blocked"
  if (raw.includes("cancel") || raw.includes("stop")) return "stopped"
  if (raw.includes("run") || raw.includes("start") || raw.includes("progress")) return "running"
  return raw || "unknown"
}

const maybeAddRecoveryTaskSummary = (controller: any): void => {
  const tasks = Array.isArray(controller.tasks) ? controller.tasks : []
  if (tasks.length === 0 || typeof controller.addTool !== "function") return
  const counts = new Map<string, number>()
  for (const task of tasks) {
    const status = normalizeRecoveryTaskStatus(task?.status)
    counts.set(status, (counts.get(status) ?? 0) + 1)
  }
  const count = (status: string) => counts.get(status) ?? 0
  const taskIds = tasks
    .map((task: any) => String(task?.id ?? "").trim())
    .filter(Boolean)
    .slice(0, 4)
    .join(", ")
  const artifactRefs = tasks
    .map((task: any) => String(task?.artifactPath ?? "").trim())
    .filter(Boolean)
    .slice(0, 3)
    .join(", ")
  const key = tasks
    .map((task: any) => `${String(task?.id ?? "")}:${normalizeRecoveryTaskStatus(task?.status)}:${String(task?.artifactPath ?? "")}`)
    .sort()
    .join("|")
  if (controller.recoveryTaskSummaryKey === key) return
  controller.recoveryTaskSummaryKey = key
  const lines = [
    "[recovery tasks]",
    `tasks=${tasks.length} running=${count("running")} blocked=${count("blocked")} failed=${count("failed")} completed=${count("completed")} stopped=${count("stopped")}`,
    "state=preserved-local",
    "next=/tasks to inspect preserved task summaries; /retry may resubmit the prior prompt; /resume starts a fresh owned session when available",
  ]
  if (taskIds) lines.push(`task_ids=${taskIds}`)
  if (artifactRefs) lines.push(`artifact_refs=${artifactRefs}`)
  controller.addTool("status", lines.join("\n"), "warning")
}

const eventVisibility = (event: SessionEvent): string | null =>
  typeof event.visibility === "string" && event.visibility.trim().length > 0 ? event.visibility.trim() : null

const isTranscriptVisibleEvent = (event: SessionEvent): boolean => {
  const visibility = eventVisibility(event)
  return visibility == null || visibility === "transcript"
}

const extractExplicitCompletionConversationLine = (data: Record<string, unknown>): string | undefined => {
  const display = isRecord(data.display) ? data.display : isRecord(data.ui) ? data.ui : null
  const visibility =
    (display ? extractString(display, ["visibility", "surface"]) : undefined) ??
    extractString(data, ["visibility", "surface"])
  if (visibility !== "transcript") return undefined
  return extractString(data, ["conversation", "final_message", "summary", "result", "output"])
}

const formatCompletion = (payload: unknown): CompletionView => {
  const data = isRecord(payload) ? payload : {}
  const completed = Boolean(
    data.completed ??
      (isRecord(data.summary) ? data.summary.completed : undefined) ??
      data.complete ??
      data.success ??
      data.ok ??
      (typeof data.status === "string" && data.status.toLowerCase().includes("complete")),
  )
  const reason =
    extractString(data, ["reason", "message", "detail", "status"]) ??
    (isRecord(data.summary) ? extractString(data.summary, ["reason", "message", "detail", "status"]) : undefined) ??
    ""
  const reasonText = reason.trim()
  const status = completed ? "Finished" : reasonText ? `Halted (${reasonText})` : "Halted"
  const toolLine = completed ? (reasonText ? `completed (${reasonText})` : "completed") : reasonText || "halted"
  const durationMs = extractDurationMs(data)
  const hint = durationMs
    ? `✻ Cooked for ${formatDurationMs(durationMs)}`
    : completed
      ? "✻ Completed"
      : "✻ Halted"
  const conversationLine =
    extractExplicitCompletionConversationLine(data) ??
    (!completed && reasonText ? `[halted] ${reasonText}` : undefined)
  const warningSource = isRecord(data.warning) ? data.warning : isRecord(data.guardrail) ? data.guardrail : null
  const warningText =
    (warningSource ? extractString(warningSource, ["text", "summary", "message", "detail"]) : undefined) ??
    extractString(data, ["warning", "guardrail", "notice"])
  const warningColor = warningSource ? extractString(warningSource, ["color"]) : undefined
  const warningSlot = warningText
    ? { text: warningText, color: warningColor ?? SEMANTIC_COLORS.warning }
    : undefined
  const errorNotice = !completed ? extractCompletionErrorNotice(data) : null
  return { completed, status, toolLine, hint, conversationLine, warningSlot, errorNotice: errorNotice ?? undefined }
}

const DEFAULT_TODO_SCOPE_KEY = "main"
const resolveTodoCoalesceMs = (): number => {
  const raw = (process.env.BREADBOARD_TODO_COALESCE_MS ?? "").trim()
  if (!raw) return 0
  const parsed = Number(raw)
  if (!Number.isFinite(parsed)) return 0
  return Math.max(0, Math.min(1000, Math.floor(parsed)))
}

export const markTodoScopesStale = (controller: any): void => {
  const stale = controller.todoScopeStaleByKey
  if (!stale || typeof stale !== "object") return
  for (const key of Object.keys(stale)) {
    stale[key] = true
  }
}

const normalizeScopeKey = (value: string): string => {
  const trimmed = value.trim()
  if (!trimmed) return DEFAULT_TODO_SCOPE_KEY
  // Keep scope keys terminal-friendly (used in headers and commands).
  return trimmed.replace(/\s+/g, "_").slice(0, 64)
}

const resolveTodoScope = (
  payload: Record<string, unknown>,
  todoEnvelope: Record<string, unknown> | null,
): { scopeKey: string; scopeLabel: string | null } => {
  const scopeFromTodo = todoEnvelope ? extractString(todoEnvelope, ["scope_key", "scopeKey", "scope"]) : null
  const scopeFromPayload = extractString(payload, [
    "scope_key",
    "scopeKey",
    "scope",
    "lane_id",
    "laneId",
    "task_id",
    "taskId",
    "agent_id",
    "agentId",
  ])
  const scopeKey = normalizeScopeKey(scopeFromTodo ?? scopeFromPayload ?? DEFAULT_TODO_SCOPE_KEY)
  const labelFromTodo = todoEnvelope ? extractString(todoEnvelope, ["scope_label", "scopeLabel", "label"]) : null
  const labelFromPayload = extractString(payload, ["lane_label", "laneLabel", "subagent_type", "subagentType"])
  const scopeLabel = (labelFromTodo ?? labelFromPayload ?? null)?.trim() || null
  return { scopeKey, scopeLabel }
}

const parseTodoSourceRevision = (todoEnvelope: Record<string, unknown> | null): number | null => {
  if (!todoEnvelope) return null
  const rev =
    parseNumberish(todoEnvelope.revision) ??
    parseNumberish(todoEnvelope.rev) ??
    parseNumberish(todoEnvelope.seq) ??
    parseNumberish(todoEnvelope.version)
  return rev != null && Number.isFinite(rev) ? Math.floor(rev) : null
}

const ensureTodoScope = (controller: any, scopeKey: string, scopeLabel: string | null): void => {
  const key = normalizeScopeKey(scopeKey)
  if (!controller.todoStoresByScope) {
    controller.todoStoresByScope = { [DEFAULT_TODO_SCOPE_KEY]: createEmptyTodoStore() }
  }
  if (!controller.todoStoresPendingByScope) {
    controller.todoStoresPendingByScope = {}
  }
  if (!controller.todoStoresPendingDirtyByScope) {
    controller.todoStoresPendingDirtyByScope = {}
  }
  if (!controller.todoStoresPendingClearStaleByScope) {
    controller.todoStoresPendingClearStaleByScope = {}
  }
  if (!controller.todoStoresPendingHintByScope) {
    controller.todoStoresPendingHintByScope = {}
  }
  if (!controller.todoStoresCoalesceTimersByScope) {
    controller.todoStoresCoalesceTimersByScope = new Map()
  }
  if (!controller.todoScopeOrder) {
    controller.todoScopeOrder = [DEFAULT_TODO_SCOPE_KEY]
  }
  if (!controller.todoScopeLabelsByKey) {
    controller.todoScopeLabelsByKey = { [DEFAULT_TODO_SCOPE_KEY]: DEFAULT_TODO_SCOPE_KEY }
  }
  if (!controller.todoScopeStaleByKey) {
    controller.todoScopeStaleByKey = { [DEFAULT_TODO_SCOPE_KEY]: false }
  }
  if (!controller.todoSourceRevisionByScope) {
    controller.todoSourceRevisionByScope = {}
  }
  if (!controller.todoStoresByScope[key]) {
    controller.todoStoresByScope[key] = createEmptyTodoStore()
    if (Array.isArray(controller.todoScopeOrder) && !controller.todoScopeOrder.includes(key)) {
      controller.todoScopeOrder = [...controller.todoScopeOrder, key]
    }
    if (controller.todoScopeStaleByKey) {
      controller.todoScopeStaleByKey[key] = false
    }
  }
  if (!controller.todoStoresPendingByScope[key]) {
    controller.todoStoresPendingByScope[key] = controller.todoStoresByScope[key]
  }
  if (scopeLabel && controller.todoScopeLabelsByKey) {
    controller.todoScopeLabelsByKey[key] = scopeLabel
  } else if (controller.todoScopeLabelsByKey && !controller.todoScopeLabelsByKey[key]) {
    controller.todoScopeLabelsByKey[key] = key
  }
}

const extractProjectedTodoUpdate = (
  controller: any,
  payload: Record<string, unknown>,
  at: number,
): { update: TodoUpdate; sourceRevision: number | null; scopeKey: string; scopeLabel: string | null } | null => {
  const todo = payload.todo
  if (!todo) return null

  if (Array.isArray(todo)) {
    const items = controller.extractTodosFromPayload(todo)
    if (!items) return null
    const { scopeKey, scopeLabel } = resolveTodoScope(payload, null)
    return { update: { op: "replace", at, scopeKey, items }, sourceRevision: null, scopeKey, scopeLabel }
  }

  if (!isRecord(todo)) return null
  const envelope = todo as Record<string, unknown>
  const { scopeKey, scopeLabel } = resolveTodoScope(payload, envelope)
  const sourceRevision = parseTodoSourceRevision(envelope)

  const opRaw = String(envelope.op ?? envelope.operation ?? envelope.type ?? "").trim().toLowerCase()
  const op = opRaw === "snapshot" ? "replace" : opRaw === "reset" ? "clear" : opRaw

  if (op === "clear") {
    return { update: { op: "clear", at, scopeKey }, sourceRevision, scopeKey, scopeLabel }
  }

  if (op === "replace") {
    const items = controller.extractTodosFromPayload(envelope.items ?? envelope.todos ?? envelope)
    if (!items) return null
    return { update: { op: "replace", at, scopeKey, items }, sourceRevision, scopeKey, scopeLabel }
  }

  if (op === "delete" || op === "remove") {
    const idsRaw = envelope.ids ?? envelope.id ?? envelope.todo_id ?? envelope.todoId
    const ids = Array.isArray(idsRaw)
      ? idsRaw.map((v) => String(v)).filter((v) => v.trim().length > 0)
      : typeof idsRaw === "string" && idsRaw.trim()
        ? [idsRaw.trim()]
        : []
    if (ids.length === 0) return null
    return { update: { op: "delete", at, scopeKey, ids }, sourceRevision, scopeKey, scopeLabel }
  }

  if (op === "upsert" || op === "add") {
    const parsed = controller.extractTodosFromPayload([envelope.item ?? envelope.todo ?? envelope])
    const item = parsed && parsed.length > 0 ? parsed[0] : null
    if (!item) return null
    const positionRaw = parseNumberish(envelope.position)
    const position = positionRaw != null && Number.isFinite(positionRaw) ? Math.floor(positionRaw) : null
    return { update: { op: "upsert", at, scopeKey, item, position }, sourceRevision, scopeKey, scopeLabel }
  }

  if (op === "patch" || op === "update") {
    const patchesRaw = envelope.patches ?? envelope.patch ?? []
    const patchesList = Array.isArray(patchesRaw) ? patchesRaw : isRecord(patchesRaw) ? [patchesRaw] : []
    const patches = patchesList
      .map((entry) => {
        if (!isRecord(entry)) return null
        const id =
          extractString(entry, ["id", "todo_id", "todoId"]) ??
          extractString(envelope, ["id", "todo_id", "todoId"]) ??
          null
        if (!id) return null
        const patch: Record<string, unknown> = {}
        const title = extractString(entry, ["title"])
        const status = extractString(entry, ["status"])
        const priority = parseNumberish((entry as any).priority)
        if (title != null) patch.title = title
        if (status != null) patch.status = status
        if (priority != null && Number.isFinite(priority)) patch.priority = priority
        if ((entry as any).metadata != null) patch.metadata = (entry as any).metadata
        if (Object.keys(patch).length === 0) return null
        return { id, patch: patch as any } as TodoUpdatePatch
      })
      .filter((value): value is TodoUpdatePatch => value != null)
    if (patches.length === 0) return null
    return { update: { op: "patch", at, scopeKey, patches }, sourceRevision, scopeKey, scopeLabel }
  }

  return null
}

const maybeApplyTodoUpdateFromPayload = (controller: any, payload: Record<string, unknown>): number | null => {
  const at = clockNow(controller)
  const projected = extractProjectedTodoUpdate(controller, payload, at)
  const hasExplicitTodo = Object.prototype.hasOwnProperty.call(payload, "todo") && payload.todo != null
  const fallback: { update: TodoUpdate; sourceRevision: number | null; scopeKey: string; scopeLabel: string | null } | null =
    projected ??
    (() => {
      if (hasExplicitTodo) return null
      const items = controller.extractTodosFromPayload(payload)
      if (!items) return null
      const { scopeKey, scopeLabel } = resolveTodoScope(payload, null)
      return { update: { op: "replace", at, scopeKey, items } as TodoUpdate, sourceRevision: null, scopeKey, scopeLabel }
    })()

  if (!fallback) return null

  const { update, sourceRevision, scopeKey, scopeLabel } = fallback
  ensureTodoScope(controller, scopeKey, scopeLabel)

  const prevKnown = controller.todoSourceRevisionByScope?.[scopeKey]
  const hasKnownRevision = typeof prevKnown === "number" && Number.isFinite(prevKnown)
  if (sourceRevision == null && hasKnownRevision) {
    // Once the engine starts emitting revisioned updates, ignore unversioned fallbacks so they
    // cannot override newer authoritative state.
    return null
  }

  if (sourceRevision != null) {
    if (hasKnownRevision && sourceRevision <= prevKnown) {
      return null
    }
    controller.todoSourceRevisionByScope[scopeKey] = sourceRevision
  }

  const committed = controller.todoStoresByScope?.[scopeKey] ?? createEmptyTodoStore()
  const pending = controller.todoStoresPendingByScope?.[scopeKey] ?? committed
  const nextPending = reduceTodoStore(pending, update)
  if (nextPending === pending) return null

  controller.todoStoresPendingByScope[scopeKey] = nextPending
  controller.todoStoresPendingDirtyByScope[scopeKey] = true
  controller.todoStoresPendingClearStaleByScope[scopeKey] = true
  if (update.op === "replace") {
    controller.todoStoresPendingHintByScope[scopeKey] = update.items.length
  } else {
    delete controller.todoStoresPendingHintByScope[scopeKey]
  }

  const coalesceMs = resolveTodoCoalesceMs()
  const hasListeners = typeof controller.listenerCount === "function" ? controller.listenerCount("change") > 0 : true
  if (coalesceMs <= 0 || !hasListeners) {
    controller.flushTodoCoalescedUpdates?.({ scopeKey, emit: false })
    return 0
  }

  const existing = controller.todoStoresCoalesceTimersByScope.get(scopeKey) ?? null
  if (existing) {
    clearTimeout(existing)
  }
  const timer = setTimeout(() => {
    controller.todoStoresCoalesceTimersByScope.delete(scopeKey)
    controller.flushTodoCoalescedUpdates?.({ scopeKey, emit: true })
  }, coalesceMs)
  controller.todoStoresCoalesceTimersByScope.set(scopeKey, timer)
  return 0
}

export function handleToolCall(
  this: any,
  payload: Record<string, unknown>,
  callIdOverride?: string | null,
  allowExisting = true,
  logEntry = true,
): string | null {
  this.finalizeStreamingEntry()
  if (this.pendingResponse) {
    this.setActivityStatus?.("Tool call in progress…", { to: "tool_call", eventType: "tool_call", source: "event" })
  }
  const callId = callIdOverride ?? this.resolveToolCallId(payload)
  if (callId && !allowExisting && (this.toolSlotsByCallId.has(callId) || this.toolLogEntryByCallId.has(callId))) {
    return callId
  }
  this.stats.toolCount += 1
  const display = this.resolveToolDisplayPayload(payload)
  const toolText = this.formatToolDisplayText(display ? { ...payload, display } : payload)
  const cleanedToolText = stripAnsi(toolText).trim()
  const shouldLog = logEntry && Boolean(cleanedToolText && cleanedToolText !== "Tool")
  const hasDiffBlocks = Array.isArray(display?.diff_blocks) && display.diff_blocks.length > 0
  let callEntry = callId ? this.toolLogEntryByCallId.get(callId) ?? null : null
  if (shouldLog) {
    if (callEntry) {
      this.updateToolEntry(callEntry, { text: toolText, status: "pending", ...(display ? { display } : {}) })
    } else {
      callEntry = this.addTool("call", toolText, "pending", { callId, display }).id
      this.bumpRuntimeTelemetry?.(hasDiffBlocks ? "optimisticDiffRows" : "optimisticToolRows")
    }
  }
  const slotId = createSlotId()
  const slot = this.formatToolSlot(payload)
  if (callId) {
    this.toolSlotsByCallId.set(callId, slotId)
    if (callEntry) {
      this.toolLogEntryByCallId.set(callId, callEntry)
    }
  } else {
    this.toolSlotFallback.push(slotId)
  }
  this.upsertLiveSlot(slotId, slot.text, slot.color, "pending")
  if (this.viewPrefs.toolInline) {
    this.addConversation("system", `[tool] ${slot.text}`)
  }
  maybeApplyTodoUpdateFromPayload(this, payload)
  return callId
}

export function handleToolResult(this: any, payload: Record<string, unknown>, callIdOverride?: string | null): void {
  this.finalizeStreamingEntry()
  if (this.pendingResponse) {
    this.setActivityStatus?.("Tool result received", { to: "tool_result", eventType: "tool_result", source: "event" })
  }
  this.stats.toolCount += 1
  const resultWasError = this.isToolResultError(payload)
  const callId = callIdOverride ?? this.resolveToolCallId(payload)
  const callEntryId = callId ? this.toolLogEntryByCallId.get(callId) : null
  const execOutput = callId ? this.toolExecOutputByCallId.get(callId) ?? null : null
  const display = this.mergeToolExecOutputIntoDisplay(this.resolveToolDisplayPayload(payload), execOutput)
  const toolText = this.formatToolDisplayText(display ? { ...payload, display } : payload)
  const cleanedToolText = stripAnsi(toolText).trim()
  const shouldLog = Boolean(cleanedToolText && cleanedToolText !== "Tool")
  const hasDiffBlocks = Array.isArray(display?.diff_blocks) && display.diff_blocks.length > 0
  let reconciledExistingRow = false
  if (callEntryId) {
    this.updateToolEntry(callEntryId, {
      ...(shouldLog ? { text: toolText } : {}),
      status: resultWasError ? "error" : "success",
      ...(display ? { display } : {}),
    })
    reconciledExistingRow = true
  } else if (shouldLog) {
    const header = toolText.split(/\r?\n/)[0]?.trim()
    let fallbackId: string | null = null
    if (header) {
      for (let i = this.toolEvents.length - 1; i >= 0; i -= 1) {
        const entry = this.toolEvents[i]
        if (!entry || entry.kind !== "call") continue
        const entryHeader = entry.text.split(/\r?\n/)[0]?.trim()
        if (entryHeader === header) {
          fallbackId = entry.id
          break
        }
      }
    }
    if (fallbackId) {
      this.updateToolEntry(fallbackId, {
        text: toolText,
        status: resultWasError ? "error" : "success",
        ...(display ? { display } : {}),
      })
      reconciledExistingRow = true
    } else {
      this.addTool("result", toolText, resultWasError ? "error" : "success", { callId, display })
    }
  }
  if (callId) {
    const slotId = this.toolSlotsByCallId.get(callId)
    if (slotId) {
      this.toolSlotsByCallId.delete(callId)
      this.finalizeLiveSlot(slotId, resultWasError ? "error" : "success")
    }
    if (callEntryId) {
      this.toolLogEntryByCallId.delete(callId)
    }
  } else {
    const slotId = this.toolSlotFallback.pop()
    if (slotId) this.finalizeLiveSlot(slotId, resultWasError ? "error" : "success")
  }
  if (reconciledExistingRow) {
    this.bumpRuntimeTelemetry?.(hasDiffBlocks ? "optimisticDiffReconciled" : "optimisticToolReconciled")
  }
  if (this.viewPrefs.toolInline) {
    const summary =
      this.extractDiffSummary(payload) ??
      (typeof payload.result === "string"
        ? payload.result
        : typeof payload.output === "string"
          ? payload.output
          : JSON.stringify(payload))
    const trimmed = summary.length > 400 ? `${summary.slice(0, 400)}…` : summary
    this.addConversation("system", `[tool result] ${trimmed}`)
  }
  if (callId) {
    this.toolCallArgsById.delete(callId)
    this.toolExecOutputByCallId.delete(callId)
  }
  maybeApplyTodoUpdateFromPayload(this, payload)
}

export async function streamLoop(this: any): Promise<void> {
  const appConfig = this.providers.args.config
  const retrySuffix = Number.isFinite(MAX_RETRIES) ? `/${MAX_RETRIES}` : ""
  const streamStallTimeoutMs = Number.isFinite(STREAM_STALL_TIMEOUT_MS) && STREAM_STALL_TIMEOUT_MS > 0 ? STREAM_STALL_TIMEOUT_MS : 0
  while (!this.abortRequested) {
    this.abortController = new AbortController()
    this.streamGeneration = Math.max(0, Number(this.streamGeneration ?? 0)) + 1
    const streamGeneration = this.streamGeneration
    let streamStallTriggered = false
    let stallTimer: NodeJS.Timeout | null = null
    const clearStreamStallTimer = () => {
      if (stallTimer) {
        clearTimeout(stallTimer)
        stallTimer = null
      }
    }
    const armStreamStallTimer = () => {
      clearStreamStallTimer()
      if (!(streamStallTimeoutMs > 0)) return
      if (!this.pendingResponse || this.abortController.signal.aborted) return
      stallTimer = setTimeout(() => {
        if (!this.pendingResponse || this.abortController.signal.aborted) return
        streamStallTriggered = true
        try {
          this.abortController.abort()
        } catch {
          // ignore abort races
        }
      }, streamStallTimeoutMs)
    }
const keepPendingDuringReconnect = () =>
      Boolean(this.pendingResponse && !this.completionSeen && !this.abortRequested)
    const attemptOwnedEngineRestart = async (eventType: string, message: string): Promise<boolean> => {
      if (this.lifecycleSnapshot?.mode !== "local-owned") return false
      this.ownedEngineRecoveryAttempts = Math.max(0, this.ownedEngineRecoveryAttempts ?? 0) + 1
      if (Number.isFinite(MAX_RETRIES) && this.ownedEngineRecoveryAttempts > MAX_RETRIES) {
        this.pushHint(`Engine interrupted: ${message}. Engine recovery attempts exhausted after ${MAX_RETRIES} attempts.`)
        this.setActivityStatus?.("Recovery needed (engine restart attempts exhausted)", {
          to: "halted",
          eventType: `${eventType}.exhausted`,
          source: "runtime",
        })
        this.disconnected = true
        this.emitChange?.()
        return false
      }
      this.lifecycleRestartCount = (this.lifecycleRestartCount ?? 0) + 1
      this.pushHint(`Engine interrupted: ${message}. Restarting owned engine (attempt ${this.ownedEngineRecoveryAttempts}${retrySuffix}).`)
      maybeLifecycleToast(this, "Restarting engine", eventType)
      this.setActivityStatus?.(`BreadBoard engine interrupted. Restarting (${this.ownedEngineRecoveryAttempts}${retrySuffix})`, {
        to: "reconnecting",
        eventType,
        source: "runtime",
      })
      this.emitChange?.()
      try {
        await restartOwnedEngine()
        this.setActivityStatus?.("Reconnecting (engine restarted)", {
          to: "reconnecting",
          eventType: `${eventType}.restarted`,
          source: "runtime",
        })
        this.emitChange?.()
        return true
      } catch (restartError) {
        const detail = restartError instanceof Error ? restartError.message : String(restartError)
        this.pushHint(`Owned engine restart failed: ${detail}`)
        this.setActivityStatus?.("Recovery needed (engine restart failed)", {
          to: "halted",
          eventType: `${eventType}.restart_failed`,
          source: "runtime",
        })
        this.disconnected = true
        this.emitChange?.()
        return false
      }
    }
    try {
      const resumeFrom = this.lastEventId ?? undefined
      const warnOnReconnect = this.hasStreamedOnce
      let warned = false
      armStreamStallTimer()
      for await (const event of this.providers.sdk.stream(this.sessionId, {
        signal: this.abortController.signal,
        lastEventId: resumeFrom,
      })) {
        clearStreamStallTimer()
        if (warnOnReconnect && !warned) {
          this.pushHint("Reconnected to stream; some history may be missing.")
          markTodoScopesStale(this)
          warned = true
        }
        this.hasStreamedOnce = true
        this.disconnected = false
        this.consecutiveFailures = 0
        this.stats.eventCount += 1
        if (
          typeof event.turn === "number" &&
          Number.isFinite(event.turn) &&
          this.submissionHistory.length === 0 &&
          this.stats.lastTurn == null
        ) {
          this.stats.lastTurn = Math.max(this.stats.lastTurn ?? 0, event.turn)
        }
        const seqValue = typeof event.seq === "number" && Number.isFinite(event.seq) ? event.seq : null
        if (seqValue !== null) {
          this.lastEventId = String(seqValue)
        } else if (typeof event.id === "string" && /^[0-9]+$/.test(event.id.trim())) {
          this.lastEventId = event.id.trim()
        }
        if (!enqueueStreamEventForGeneration.call(this, event, streamGeneration)) {
          break
        }
        if (event.type === "completion" || event.type === "run_finished") {
          clearStreamStallTimer()
        } else {
          armStreamStallTimer()
        }
      }
      clearStreamStallTimer()
      if (this.abortRequested) {
        this.pendingResponse = false
        this.emitChange()
        break
      }
      if (!this.pendingResponse && (this.completionSeen === true || this.lastCompletion != null)) {
        this.consecutiveFailures = 0
        await sleep(250)
        continue
      }
      if (!this.awaitingRestart) {
        if (!keepPendingDuringReconnect()) {
          this.pendingResponse = false
        }
        this.consecutiveFailures += 1
        const retriesExhausted = Number.isFinite(MAX_RETRIES) && this.consecutiveFailures > MAX_RETRIES
        if (retriesExhausted) {
          this.pendingResponse = false
          if (streamStallTriggered) {
            const stallDuration = formatDurationMs(streamStallTimeoutMs)
            this.pushHint(`Stream stalled and reconnection attempts exhausted after ${stallDuration}.`)
            this.setGuardrailNotice?.("Stream stalled and disconnected.", `No events arrived for ${stallDuration} while the run was still pending.`)
            this.upsertLiveSlot?.("guardrail", "Stream stalled and disconnected.", SEMANTIC_COLORS.warning, "error")
          } else {
            this.pushHint("Stream ended unexpectedly and reconnection attempts exhausted.")
          }
          this.setActivityStatus?.("Disconnected", { to: "halted", eventType: streamStallTriggered ? "stream.stall" : "stream.end", source: "runtime" })
          this.disconnected = true
          this.emitChange()
          break
        }
        const retryDelay = Math.min(4000, 500 * 2 ** (this.consecutiveFailures - 1))
        if (streamStallTriggered) {
          const stallDuration = formatDurationMs(streamStallTimeoutMs)
          this.pushHint(`Stream stalled after ${stallDuration}. Retrying in ${retryDelay}ms (attempt ${this.consecutiveFailures}${retrySuffix}).`)
          this.upsertLiveSlot?.("guardrail", "Stream stalled. Reconnecting…", SEMANTIC_COLORS.warning, "warning")
          maybeLifecycleToast(this, "Reconnecting", "stream.stall")
          this.setActivityStatus?.(`Reconnecting (${this.consecutiveFailures}${retrySuffix})`, {
            to: "reconnecting",
            eventType: "stream.stall",
            source: "runtime",
          })
        } else {
          this.pushHint(`Stream ended unexpectedly. Retrying in ${retryDelay}ms (attempt ${this.consecutiveFailures}${retrySuffix}).`)
          maybeLifecycleToast(this, "Reconnecting", "stream.reconnect")
          this.setActivityStatus?.(`Reconnecting (${this.consecutiveFailures}${retrySuffix})`, {
            to: "reconnecting",
            eventType: "stream.reconnect",
            source: "runtime",
          })
        }
        this.emitChange()
        await sleep(retryDelay)
        continue
      }
      this.awaitingRestart = false
      this.consecutiveFailures = 0
      this.setActivityStatus?.("Restarting…", { to: "run", eventType: "stream.restart", source: "runtime" })
      this.pendingResponse = true
      this.emitChange()
      await sleep(250)
    } catch (error) {
      clearStreamStallTimer()
      if (error instanceof ApiError && error.status === 409) {
        if (!keepPendingDuringReconnect()) {
          this.pendingResponse = false
        }
        this.pushHint("Stream resume window exceeded; reconnecting without history.")
        markTodoScopesStale(this)
        if (this.ctreeTreeRequested && this.ctreeSource !== "disk") {
          this.pushHint("CTree resume window exceeded; switching to disk tree view.")
          void this.refreshCtreeTree({ source: "disk" })
        }
        this.lastEventId = null
        maybeLifecycleToast(this, "Reconnecting", "stream.resume.reset")
        this.setActivityStatus?.("Reconnecting (resume reset)", {
          to: "reconnecting",
          eventType: "stream.resume.reset",
          source: "runtime",
        })
        this.emitChange()
        await sleep(250)
        continue
      }
      if (error instanceof ApiError && error.status === 404 && this.lifecycleSnapshot?.mode === "local-owned") {
        const submitEventCount = Number(this.pendingResponseEventCountAtSubmit ?? NaN)
        const noEventsAfterSubmit =
          this.pendingResponse &&
          this.activePromptOutcomeUnresolved === true &&
          Number.isFinite(submitEventCount) &&
          this.stats.eventCount <= submitEventCount
        if (noEventsAfterSubmit) {
          const payload = Array.isArray(this.submissionHistory) ? this.submissionHistory[this.submissionHistory.length - 1] : null
          if (payload?.content) {
            const restarted = await attemptOwnedEngineRestart("stream.session.missing.no_events", error.message)
            if (restarted) {
              this.pendingResponse = false
              const recovered = await this.recoverIdleSessionAfterEngineRestart?.({ preserveLocalTranscript: true })
              if (recovered) {
                this.setActivityStatus?.("Resending after engine recovery", {
                  to: "run",
                  eventType: "stream.session.missing.recovered",
                  source: "runtime",
                })
                this.emitChange?.()
                try {
                  await this.dispatchSubmission?.(payload, "Working…", { attemptedOwnedRecovery: true })
                  await sleep(250)
                  continue
                } catch {
                  // Fall through to the normal recovery-needed surface.
                }
              }
            }
          }
        }
        const canRecoverCompletedLocalTranscript =
          !this.pendingResponse && (this.completionSeen === true || this.lastCompletion != null)
        if (!this.pendingResponse && (this.conversation?.length === 0 && this.toolEvents?.length === 0 || canRecoverCompletedLocalTranscript)) {
          const recovered = await this.recoverIdleSessionAfterEngineRestart?.({
            preserveLocalTranscript: canRecoverCompletedLocalTranscript,
          })
          if (recovered) {
            await sleep(250)
            continue
          }
        }
        if (hasUnresolvedSubmittedPromptOutcome(this)) {
          this.pushHint("Submitted prompt outcome is unknown after engine recovery; use /retry to resend if needed.")
        }
        maybeAddRecoveryTaskSummary(this)
        this.pendingResponse = false
        this.pushHint("Owned engine is reachable again, but the previous session is no longer available.")
        this.setActivityStatus?.("Recovery needed (session missing)", {
          to: "halted",
          eventType: "stream.session.missing",
          source: "runtime",
        })
        this.disconnected = true
        this.emitChange()
        break
      }
      if (this.abortController.signal.aborted) {
        if (this.abortRequested) {
          this.pendingResponse = false
          this.setActivityStatus?.("Aborted", { to: "cancelled", eventType: "stream.abort", source: "runtime" })
          this.disconnected = true
          this.emitChange()
          break
        }
        if (this.awaitingRestart) {
          this.awaitingRestart = false
          this.consecutiveFailures = 0
          this.pendingResponse = true
          this.setActivityStatus?.("Restarting…", { to: "run", eventType: "stream.restart", source: "runtime" })
          this.emitChange()
          await sleep(250)
          continue
        }
      }
      this.consecutiveFailures += 1
      const delay = Math.min(4000, 500 * 2 ** (this.consecutiveFailures - 1))
      const message =
        streamStallTriggered && streamStallTimeoutMs > 0
          ? `No events arrived for ${formatDurationMs(streamStallTimeoutMs)}.`
          : error instanceof Error
            ? error.message
            : String(error)
      const retriesExhausted = Number.isFinite(MAX_RETRIES) && this.consecutiveFailures > MAX_RETRIES
      if (!streamStallTriggered && this.lifecycleSnapshot?.mode === "local-owned" && this.consecutiveFailures === 1) {
        const restarted = await attemptOwnedEngineRestart("engine.restart", message)
        if (restarted) {
          await sleep(250)
          continue
        }
        break
      }
      if (retriesExhausted) {
        this.pendingResponse = false
        if (streamStallTriggered) {
          this.setGuardrailNotice?.("Stream stalled and disconnected.", message)
          this.upsertLiveSlot?.("guardrail", "Stream stalled and disconnected.", SEMANTIC_COLORS.warning, "error")
        }
        this.pushHint(`Stream interruption: ${message}. Giving up after ${MAX_RETRIES} attempts.`)
        this.setActivityStatus?.("Disconnected", { to: "halted", eventType: streamStallTriggered ? "stream.stall" : "stream.error", source: "runtime" })
        this.disconnected = true
        this.emitChange()
        break
      }
      if (!keepPendingDuringReconnect()) {
        this.pendingResponse = false
      }
      if (streamStallTriggered) {
        this.upsertLiveSlot?.("guardrail", "Stream stalled. Reconnecting…", SEMANTIC_COLORS.warning, "warning")
        maybeLifecycleToast(this, "Reconnecting", "stream.stall")
      } else {
        maybeLifecycleToast(this, "Reconnecting", "stream.error")
      }
      this.pushHint(`Stream interruption: ${message}. Retrying in ${delay}ms (attempt ${this.consecutiveFailures}${retrySuffix}).`)
      this.setActivityStatus?.(`Reconnecting (${this.consecutiveFailures}${retrySuffix})`, {
        to: "reconnecting",
        eventType: streamStallTriggered ? "stream.stall" : "stream.error",
        source: "runtime",
      })
      this.emitChange()
      await sleep(delay)
    } finally {
      clearStreamStallTimer()
    }
  }
}

export function enqueueStreamEventForGeneration(this: any, event: SessionEvent, streamGeneration: number): boolean {
  if (
    typeof streamGeneration === "number" &&
    Number.isFinite(streamGeneration) &&
    typeof this.streamGeneration === "number" &&
    this.streamGeneration !== streamGeneration
  ) {
    return false
  }
  this.enqueueEvent(event)
  return true
}

export function enqueueEvent(this: any, event: SessionEvent): void {
  const eventId =
    typeof event.id === "string" && event.id.trim().length > 0
      ? event.id
      : typeof event.seq === "number"
        ? String(event.seq)
        : null
  if (eventId) {
    if (this.seenEventIds?.has(eventId)) {
      return
    }
    if (this.seenEventIds) {
      this.seenEventIds.add(eventId)
      this.seenEventIdQueue.push(eventId)
      if (this.seenEventIdQueue.length > MAX_SEEN_EVENT_IDS) {
        const stale = this.seenEventIdQueue.shift()
        if (stale) {
          this.seenEventIds.delete(stale)
        }
      }
    }
  }
  this.pendingEvents.push(event)
  this.runtimeTelemetry = {
    ...this.runtimeTelemetry,
    eventMaxQueueDepth: Math.max(this.runtimeTelemetry.eventMaxQueueDepth ?? 0, this.pendingEvents.length),
  }
  if (shouldFlushEventImmediately(event.type)) {
    this.flushPendingEvents()
    return
  }
  const maxBatch = Math.max(1, Number(this.runtimeFlags?.eventCoalesceMaxBatch ?? 128))
  if (this.pendingEvents.length >= maxBatch) {
    this.flushPendingEvents()
    return
  }
  if (this.eventsScheduled) return
  this.eventsScheduled = true
  const coalesceMs = Math.max(0, Number(this.runtimeFlags?.eventCoalesceMs ?? 0))
  if (coalesceMs <= 0) {
    queueMicrotask(() => {
      if (!this.eventsScheduled) return
      this.flushPendingEvents()
    })
    return
  }
  this.eventFlushTimer = setTimeout(() => {
    this.flushPendingEvents()
  }, coalesceMs)
}

const IMMEDIATE_EVENT_TYPES = new Set<string>([
  "assistant.message.delta",
  "assistant_delta",
  "assistant_message",
  "assistant.reasoning.delta",
  "assistant.thought_summary.delta",
  "tool.exec.stdout.delta",
  "tool.exec.stderr.delta",
  "completion",
  "error",
])

const shouldFlushEventImmediately = (eventType: string): boolean => IMMEDIATE_EVENT_TYPES.has(String(eventType || ""))

export function flushPendingEvents(this: any): void {
  if (this.eventFlushTimer) {
    clearTimeout(this.eventFlushTimer)
    this.eventFlushTimer = null
  }
  this.eventsScheduled = false
  const queue = this.pendingEvents
  if (!Array.isArray(queue) || queue.length === 0) return
  this.pendingEvents = []
  if (queue.length > 1) {
    this.bumpRuntimeTelemetry?.("eventCoalesced", queue.length - 1)
  }
  this.bumpRuntimeTelemetry?.("eventFlushes")
  for (const item of queue) {
    this.applyEvent(item)
  }
  this.emitChange()
}

export function applyEvent(this: any, event: SessionEvent): void {
  this.pushRawEvent(event)
  const prevSeq = this.currentEventSeq
  let nextSeq: number | null = null
  if (typeof event.seq === "number" && Number.isFinite(event.seq)) {
    nextSeq = event.seq
    if (typeof this.eventClock === "number" && nextSeq > this.eventClock) {
      this.eventClock = nextSeq
    }
  } else {
    this.eventClock = typeof this.eventClock === "number" ? this.eventClock + 1 : 1
    nextSeq = this.eventClock
  }
  this.currentEventSeq = nextSeq
  try {
  if (!DEBUG_EVENTS && Array.isArray(event.tags) && event.tags.includes("legacy")) {
    return
  }
  if (isRecord(event.payload)) {
    this.updateUsageFromPayload(event.payload)
  }
  const eventType = String(event.type)
  switch (eventType) {
    case "stream.hello": {
      if (DEBUG_EVENTS) {
        this.pushHint("Stream connected.")
      }
      break
    }
    case "stream.gap": {
      this.pushHint("Stream gap detected; some events may be missing.")
      maybeLifecycleToast(this, "Reconnecting", event.type)
      this.setActivityStatus?.("Reconnecting…", { to: "reconnecting", eventType: event.type, source: "event" })
      break
    }
    case "conversation.compact.start":
    case "conversation.compaction.start":
    case "session.compaction.start":
    case "context.compaction.start": {
      maybeLifecycleToast(this, "Compaction started", event.type)
      this.setActivityStatus?.("Compacting conversation…", { to: "compacting", eventType: event.type, source: "event" })
      this.pushHint("Compaction started.")
      break
    }
    case "conversation.compact.end":
    case "conversation.compaction.end":
    case "session.compaction.end":
    case "context.compaction.end":
    case "context_compacted":
    case "conversation.compacted": {
      maybeLifecycleToast(this, "Compaction complete", event.type)
      this.setActivityStatus?.("Compaction complete", { to: "session", eventType: event.type, source: "event" })
      this.pushHint("Compaction complete.")
      break
    }
    case "session.start": {
      this.setActivityStatus?.("Session started", { to: "session", eventType: event.type, source: "event" })
      break
    }
    case "session.meta": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const model = extractString(payload, ["model"])
      const mode = extractString(payload, ["mode"])
      const permissionMode = extractString(payload, ["permission_mode", "permissionMode"])
      if (model) this.stats.model = model
      if (mode) this.mode = normalizeModeValue(mode)
      if (permissionMode) this.permissionMode = normalizePermissionMode(permissionMode)
      break
    }
    case "run.start": {
      this.pendingResponse = true
      notePendingStart(this)
      markThinkingActive(this, event.type)
      maybeLifecycleToast(this, "Run started", event.type)
      this.pushHint("Run started.")
      break
    }
    case "turn_start": {
      this.finalizeStreamingEntry()
      this.clearStopRequest()
      this.pendingResponse = true
      notePendingStart(this)
      markThinkingActive(this, event.type)
      this.lastThinkingPeekAt = 0
      this.thinkingInlineEntryId = null
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, clockNow(this))
      }
      if (this.runtimeFlags?.thinkingEnabled !== false) {
        const mode: ThinkingMode = resolveThinkingMode(this)
        this.thinkingArtifact = createThinkingArtifact(mode, clockNow(this))
        maybeStartThinkingPreview(this, mode, clockNow(this))
      } else {
        this.thinkingArtifact = null
        this.thinkingPreview = null
      }
      const payload = isRecord(event.payload) ? event.payload : {}
      const mode = normalizeModeValue(extractString(payload, ["mode", "agent_mode", "phase"]))
      if (mode) {
        this.mode = mode
      }
      if (DEBUG_EVENTS || this.viewPrefs.rawStream) {
        const turnLabel = typeof event.turn === "number" ? `Turn ${event.turn} started` : "Turn started"
        this.addTool("status", `[turn] ${turnLabel}`)
      }
      break
    }
    case "assistant.message.start": {
      if (!isTranscriptVisibleEvent(event)) break
      this.finalizeStreamingEntry()
      markAssistantResponding(this, event.type)
      closeThinkingPreview(this, clockNow(this))
      this.setStreamingConversation("assistant", "")
      break
    }
    case "assistant.message.delta": {
      if (!isTranscriptVisibleEvent(event)) break
      markAssistantResponding(this, event.type)
      const payload = isRecord(event.payload) ? event.payload : {}
      const delta = extractRawString(payload, ["delta", "text"])
      if (delta) {
        this.appendAssistantDelta(delta)
      }
      break
    }
    case "assistant.message.end": {
      if (!isTranscriptVisibleEvent(event)) break
      // Keep the hot assistant entry alive until a real turn boundary. Some providers
      // emit a cumulative assistant_message after end but before completion.
      break
    }
    case "assistant_message": {
      if (!isTranscriptVisibleEvent(event)) break
      markAssistantResponding(this, event.type)
      closeThinkingPreview(this, clockNow(this))
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const text = typeof raw === "string" ? raw : ""
      const sentinelComplete = hasAssistantCompletionSentinel(text)
      const normalizedText = this.normalizeAssistantText(text)
      const trimmed = normalizedText.trim()
      if (!trimmed || trimmed.toLowerCase() === "none") break
      if (shouldIgnoreStaleCumulativeAssistantMessage(this, normalizedText)) break
      rebindFinalAssistantEntryForCumulativeMessage(this, normalizedText)
      this.setStreamingConversation("assistant", normalizedText)
      this.appendMarkdownChunk(normalizedText)
      if (sentinelComplete) {
        settleFromAssistantCompletionSentinel(this, event.type)
      }
      break
    }
    case "assistant_delta": {
      if (!isTranscriptVisibleEvent(event)) break
      markAssistantResponding(this, event.type)
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const delta = typeof raw === "string" ? raw : ""
      this.appendAssistantDelta(delta)
      break
    }
    case "assistant.reasoning.delta":
    case "assistant.thought_summary.delta": {
      const signal = normalizeThinkingSignal(event.type, event.payload, {
        provider: this.providerCapabilities?.provider ?? null,
      })
      const delta = signal?.text ?? ""
      const capabilities = this.providerCapabilities ?? {
        reasoningEvents: true,
        thoughtSummaryEvents: true,
        rawThinkingPeek: false,
        inlineThinkingBlock: false,
      }
      const signalAllowed =
        signal != null &&
        (signal.kind === "summary" ? capabilities.thoughtSummaryEvents !== false : capabilities.reasoningEvents !== false)
      if (signal && signalAllowed && this.runtimeFlags?.thinkingEnabled !== false) {
        const mode: ThinkingMode = resolveThinkingMode(this)
        const seed = this.thinkingArtifact ?? createThinkingArtifact(mode, clockNow(this))
        this.thinkingArtifact = appendThinkingArtifact(seed, signal, this.runtimeFlags, clockNow(this))
        this.bumpRuntimeTelemetry?.("thinkingUpdates")
        appendThinkingPreview(this, signal, clockNow(this))
      }
      if (signal) {
        appendInlineThinkingBlock(this, signal)
      }
      if (DEBUG_EVENTS) {
        const rawPeekAllowed =
          this.runtimeFlags?.allowRawThinkingPeek === true || capabilities.rawThinkingPeek === true
        const peekText = signal?.kind === "summary" || rawPeekAllowed ? delta : ""
        if (!peekText.trim()) break
        const now = clockNow(this)
        if (!shouldThrottleReasoningPeek(this, now)) {
          this.addTool("status", `[reasoning] ${peekText}`, "pending")
          this.lastThinkingPeekAt = now
        }
      }
      break
    }
    case "user_message": {
      if (!isTranscriptVisibleEvent(event)) break
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const text = typeof raw === "string" ? this.normalizeUserEchoText(raw) : ""
      const normalized = text.trim()
      if (this.pendingUserEcho && normalized && this.pendingUserEcho === normalized) {
        this.pendingUserEcho = null
        break
      }
      const lastConversation = this.conversation.at(-1)
      const lastUserText =
        lastConversation?.speaker === "user"
          ? this.normalizeUserEchoText(String(lastConversation.text ?? "")).trim()
          : ""
      if (this.pendingResponse && normalized && lastUserText === normalized) {
        this.pendingUserEcho = null
        break
      }
      this.addConversation("user", text)
      break
    }
    case "user.message": {
      if (!isTranscriptVisibleEvent(event)) break
      const payload = isRecord(event.payload) ? event.payload : {}
      const raw = extractString(payload, ["text", "content", "message"]) ?? JSON.stringify(event.payload)
      const text = typeof raw === "string" ? this.normalizeUserEchoText(raw) : ""
      const normalized = text.trim()
      if (this.pendingUserEcho && normalized && this.pendingUserEcho === normalized) {
        this.pendingUserEcho = null
        break
      }
      const lastConversation = this.conversation.at(-1)
      const lastUserText =
        lastConversation?.speaker === "user"
          ? this.normalizeUserEchoText(String(lastConversation.text ?? "")).trim()
          : ""
      if (this.pendingResponse && normalized && lastUserText === normalized) {
        this.pendingUserEcho = null
        break
      }
      this.addConversation("user", text)
      break
    }
    case "user.command": {
      if (!isTranscriptVisibleEvent(event)) break
      const payload = isRecord(event.payload) ? event.payload : {}
      const raw = extractString(payload, ["command", "text", "content"]) ?? JSON.stringify(event.payload)
      const command = typeof raw === "string" ? raw : ""
      this.addConversation("user", command.startsWith("/") ? command : `/${command}`)
      break
    }
    case "permission_request": {
      this.finalizeStreamingEntry()
      const payload = isRecord(event.payload) ? event.payload : {}
      const requestId =
        typeof payload.request_id === "string"
          ? payload.request_id
          : typeof payload.id === "string"
            ? payload.id
            : event.id
      const tool = extractString(payload, ["tool", "tool_name", "name"]) ?? "Tool"
      const kind = extractString(payload, ["kind", "category", "type"]) ?? tool
      const rewindable = payload.rewindable === false ? false : true
      const summary =
        extractString(payload, ["summary", "message", "prompt"]) ??
        `Permission required for ${tool}.`
      const diffText =
        typeof payload.diff === "string"
          ? payload.diff
          : typeof payload.diff_text === "string"
            ? payload.diff_text
            : null
      const ruleSuggestion =
        typeof payload.rule === "string"
          ? payload.rule
          : typeof payload.rule_suggestion === "string"
            ? payload.rule_suggestion
            : null
      const request = buildPermissionRequest(this, event, requestId, payload, null, {
        tool,
        kind,
        rewindable,
        summary,
        diffText,
        ruleSuggestion,
      })
      if (this.permissionActive) {
        this.permissionQueue.push(request)
      } else {
        this.permissionActive = request
      }
      this.pendingResponse = false
      this.setActivityStatus?.("Permission required", { to: "permission_required", eventType: event.type, source: "event" })
      this.pushHint(`Permission needed: ${tool}.`)
      this.addTool("status", `[permission] ${tool} (${kind})`, "pending")
      break
    }
    case "permission.request": {
      this.finalizeStreamingEntry()
      const payload = isRecord(event.payload) ? event.payload : {}
      const requestId = extractString(payload, ["request_id", "requestId", "id"]) ?? event.id
      const action = isRecord(payload.action) ? payload.action : {}
      const tool = extractString(payload, ["tool", "tool_name", "name"]) ?? extractString(action, ["tool_name", "tool"]) ?? "Tool"
      const kind = extractString(payload, ["kind", "category", "type"]) ?? tool
      const summary =
        extractString(payload, ["summary", "title", "message"]) ??
        extractString(action, ["command", "summary"]) ??
        `Permission required for ${tool}.`
      const diffText =
        extractString(payload, ["diff", "diff_text"]) ??
        extractString(action, ["diff_summary"])
      const request = buildPermissionRequest(this, event, requestId, payload, action, {
        tool,
        kind,
        rewindable: payload.rewindable === false ? false : true,
        summary,
        diffText: diffText ?? null,
        ruleSuggestion: extractString(payload, ["rule", "rule_suggestion"]) ?? null,
      })
      if (this.permissionActive) {
        this.permissionQueue.push(request)
      } else {
        this.permissionActive = request
      }
      this.pendingResponse = false
      this.setActivityStatus?.("Permission required", { to: "permission_required", eventType: event.type, source: "event" })
      this.pushHint(`Permission needed: ${tool}.`)
      this.addTool("status", `[permission] ${tool} (${kind})`, "pending")
      break
    }
    case "permission_response": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const response =
        extractString(payload, ["response", "decision"]) ??
        (isRecord(payload.responses) && typeof payload.responses.default === "string" ? payload.responses.default : undefined)
      const status = extractString(payload, ["status", "state"]) ?? ""
      const error = extractString(payload, ["error", "message", "detail"])
      const retryable = payload.retryable === true || payload.can_retry === true
      const responseLabel = response ? response.replace(/[_-]+/g, " ") : status ? status.replace(/[_-]+/g, " ") : "response received"
      const denied = Boolean(responseLabel.match(/\b(deny|denied|reject|rejected|block|blocked)\b/i))
      const failed = Boolean(error) || Boolean(responseLabel.match(/\b(error|failed|failure|timeout)\b/i))
      const retryText = retryable ? " • retryable" : ""
      this.addTool("status", `[permission] ${responseLabel}${retryText}${error ? `: ${error}` : ""}`, failed ? "error" : "success")
      this.pushHint(failed ? `Permission ${responseLabel}${retryText}.` : `Permission ${responseLabel}.`)
      this.setPermissionActive(null)
      this.pendingResponse = !denied && !failed
      this.setActivityStatus?.(
        failed ? (retryable ? "Permission error (retryable)" : "Permission error") : denied ? "Permission denied" : "Permission response received",
        {
        to: denied || failed ? "halted" : "permission_resolved",
        eventType: event.type,
        source: "event",
        },
      )
      break
    }
    case "permission.decision": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const response = extractString(payload, ["decision", "response"]) ?? "decision received"
      const status = extractString(payload, ["status", "state"]) ?? ""
      const error = extractString(payload, ["error", "message", "detail"])
      const retryable = payload.retryable === true || payload.can_retry === true
      const responseLabel = response.replace(/[_-]+/g, " ")
      const denied = Boolean(responseLabel.match(/\b(deny|denied|reject|rejected|block|blocked)\b/i))
      const failed = Boolean(error) || Boolean(status.match(/\b(error|failed|failure|timeout)\b/i))
      const retryText = retryable ? " • retryable" : ""
      this.addTool("status", `[permission] ${responseLabel}${retryText}${error ? `: ${error}` : ""}`, failed ? "error" : "success")
      this.pushHint(failed ? `Permission ${responseLabel}${retryText}.` : `Permission ${responseLabel}.`)
      this.setPermissionActive(null)
      this.pendingResponse = !denied && !failed
      this.setActivityStatus?.(failed ? (retryable ? "Permission error (retryable)" : "Permission error") : denied ? "Permission denied" : "Permission response received", {
        to: denied || failed ? "halted" : "permission_resolved",
        eventType: event.type,
        source: "event",
      })
      break
    }
    case "permission.timeout": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const requestId = extractString(payload, ["request_id", "requestId", "id"])
      const detail = requestId ? ` (${requestId})` : ""
      this.addTool("status", `[permission] timeout${detail}`, "error")
      this.pushHint("Permission request timed out.")
      this.setPermissionActive(null)
      this.pendingResponse = false
      this.setActivityStatus?.("Permission timeout", { to: "halted", eventType: event.type, source: "event" })
      break
    }
    case "checkpoint_list": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const rawList = Array.isArray(payload.checkpoints)
        ? payload.checkpoints
        : Array.isArray(payload.items)
          ? payload.items
          : Array.isArray(event.payload)
            ? (event.payload as unknown[])
            : []
      const parsed: CheckpointSummary[] = []
      for (const entry of rawList) {
        const summary = this.parseCheckpointSummary(entry)
        if (summary) parsed.push(summary)
      }
      parsed.sort((a, b) => b.createdAt - a.createdAt)
      this.rewindMenu = { status: "ready", checkpoints: parsed }
      this.setActivityStatus?.("Checkpoints ready", { to: "session", eventType: event.type, source: "event" })
      this.pushHint(`Loaded ${parsed.length} checkpoint${parsed.length === 1 ? "" : "s"}.`)
      break
    }
    case "skills_catalog": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const catalog = (payload.catalog ?? payload) as SkillCatalog
      const selection = (payload.selection ?? null) as SkillSelection | null
      const sources = (payload.sources ?? null) as SkillCatalogSources | null
      this.skillsCatalog = catalog
      this.skillsSelection = selection
      this.skillsSources = sources
      if (this.skillsMenu.status !== "hidden") {
        this.skillsMenu = { status: "ready", catalog, selection, sources }
        this.pushHint("Skills catalog updated.")
      }
      break
    }
    case "skills_selection": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const selection = (payload.selection ?? payload) as SkillSelection
      const previousSelection = this.skillsSelection
      const selectionChanged = JSON.stringify(previousSelection ?? null) !== JSON.stringify(selection ?? null)
      this.skillsSelection = selection
      if (this.skillsMenu.status === "ready") {
        this.skillsMenu = {
          status: "ready",
          catalog: this.skillsMenu.catalog,
          selection,
          sources: this.skillsMenu.sources,
        }
        if (selectionChanged) {
          this.pushHint("Skills selection updated.")
        }
      }
      break
    }
    case "task_event": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const policy = resolveSubagentUiPolicy(this.runtimeFlags ?? {}, process.env)
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
      const isStoppedAfterInterrupt = Boolean(this.stopRequestedAt && !this.pendingResponse)
      if (policy.routeTaskEventsToToolRail) {
        this.addTool("status", `[task] ${line}`, isError || isStoppedAfterInterrupt ? "error" : isComplete ? "success" : "pending")
      }
      this.handleTaskEvent(payload, {
        eventType: event.type,
        eventId: event.id,
        seq: nextSeq,
        timestamp: event.timestamp,
      })
      emitSubagentToast(this, payload, event.type, policy)
      break
    }
    case "agent.spawn":
    case "agent.status":
    case "agent.end": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const policy = resolveSubagentUiPolicy(this.runtimeFlags ?? {}, process.env)
      const eventStatus = event.type.split(".")[1] ?? "update"
      const status = resolveSubagentStatus(payload, eventStatus)
      const normalizedStatus = normalizeSubagentStatus(status)
      const description = extractString(payload, ["summary", "description", "title"]) ?? "Subagent"
      const taskId = extractString(payload, ["task_id", "id"]) ?? undefined
      const lineParts = [status, description, taskId].filter(Boolean)
      const line = lineParts.length > 0 ? lineParts.join(" · ") : JSON.stringify(payload)
      if (policy.routeTaskEventsToToolRail) {
        this.addTool(
          "status",
          `[agent] ${line}`,
          normalizedStatus === "completed" ? "success" : normalizedStatus === "failed" ? "error" : "pending",
        )
      }
      this.handleTaskEvent({ ...payload, status }, {
        eventType: event.type,
        eventId: event.id,
        seq: nextSeq,
        timestamp: event.timestamp,
      })
      emitSubagentToast(this, { ...payload, status }, event.type, policy)
      break
    }
    case "ctree_node": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const next: CTreeSnapshot = {
        ...(this.ctreeSnapshot ?? {}),
        ...(isRecord(payload.snapshot) ? { snapshot: payload.snapshot } : {}),
        ...(isRecord(payload.node) ? { last_node: payload.node } : {}),
      }
      this.ctreeSnapshot = next
      if (isRecord(payload.node) && isRecord(payload.snapshot)) {
        this.ctreeModel = reduceCTreeModel(this.ctreeModel, {
          type: "ctree_node",
          node: payload.node as unknown as CTreeNode,
          snapshot: payload.snapshot as unknown as CTreeSnapshotSummary,
        })
      }
      const transcriptNotice = extractCtreeTranscriptNotice(payload)
      if (transcriptNotice?.severity === "warning") {
        if (isStreamingFallbackTranscriptNotice(transcriptNotice)) {
          shouldSurfaceTranscriptNotice(this, transcriptNotice)
          this.scheduleCtreeRefresh()
          break
        }
        if (!shouldSurfaceTranscriptNotice(this, transcriptNotice)) {
          this.scheduleCtreeRefresh()
          break
        }
        const message = userFacingTranscriptNotice(transcriptNotice)
        if (isProviderRetryTranscriptNotice(transcriptNotice)) {
          this.upsertLiveSlot("provider-retry", `[warning] ${message}`, SEMANTIC_COLORS.warning, "warning", 2500)
          this.scheduleCtreeRefresh()
          break
        }
        this.finalizeStreamingEntry()
        this.addTool("status", `[warning] ${message}`, "error")
      } else if (transcriptNotice?.severity === "error") {
        surfaceTerminalError(this, transcriptNotice.message, event.type, transcriptNotice.detail, { includeConversation: true })
      }
      this.scheduleCtreeRefresh()
      break
    }
    case "ctree_snapshot": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const next: CTreeSnapshot = {
        ...(this.ctreeSnapshot ?? {}),
        ...(payload.snapshot !== undefined ? { snapshot: payload.snapshot as Record<string, unknown> | null } : {}),
        ...(payload.compiler !== undefined ? { compiler: payload.compiler as Record<string, unknown> | null } : {}),
        ...(payload.collapse !== undefined ? { collapse: payload.collapse as Record<string, unknown> | null } : {}),
        ...(payload.runner !== undefined ? { runner: payload.runner as Record<string, unknown> | null } : {}),
        ...(payload.last_node !== undefined ? { last_node: payload.last_node as Record<string, unknown> | null } : {}),
      }
      this.ctreeSnapshot = next
      if (isRecord(payload.snapshot)) {
        const summary = extractCtreeSnapshotSummary(payload.snapshot)
        this.ctreeModel = reduceCTreeModel(this.ctreeModel, {
          type: "ctree_snapshot",
          snapshot: summary,
        })
      }
      this.scheduleCtreeRefresh()
      break
    }
    case "checkpoint_restored": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const checkpointId = extractString(payload, ["checkpoint_id", "id"]) ?? null
      const mode = extractString(payload, ["mode"]) ?? null
      const prune = typeof payload.prune === "boolean" ? payload.prune : mode !== "code"
      if (checkpointId && prune) {
        const checkpoints: CheckpointSummary[] =
          this.rewindMenu.status === "ready" || this.rewindMenu.status === "error" || this.rewindMenu.status === "loading"
            ? this.rewindMenu.checkpoints
            : []
        const index = checkpoints.findIndex((entry) => entry.checkpointId === checkpointId)
        if (index >= 0) {
          const next = checkpoints.slice(index)
          this.rewindMenu = { status: "ready", checkpoints: next }
        }
      }
      this.closeRewindMenu()
      this.setActivityStatus?.("Rewind applied", { to: "session", eventType: event.type, source: "event" })
      this.pushHint(mode ? `Rewind restored (${mode}).` : "Rewind restored.")
      break
    }
    case "assistant.tool_call.start": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      const toolName = extractString(payload, ["tool_name", "tool", "name"])
      const normalizedPayload: Record<string, unknown> = {
        ...payload,
        call_id: callId ?? undefined,
        tool: toolName ?? payload.tool,
        action: "calling",
        ...(payload.display !== undefined ? { display: payload.display } : {}),
      }
      this.handleToolCall(normalizedPayload, callId ?? null, false, true)
      break
    }
    case "assistant.tool_call.delta": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      if (!callId) break
      const delta = extractRawString(payload, ["args_text_delta", "delta", "text"]) ?? ""
      const next = this.appendToolCallArgs(callId, delta)
      const slotId = this.toolSlotsByCallId.get(callId)
      if (slotId) {
        const toolName = extractString(payload, ["tool_name", "tool", "name"]) ?? "Tool"
        const preview = next.length > 120 ? `${next.slice(0, 120)}…` : next
        const text = preview ? `${toolName}: calling (${preview})` : `${toolName}: calling`
        this.upsertLiveSlot(slotId, text, BRAND_COLORS.duneOrange, "pending")
      }
      break
    }
    case "assistant.tool_call.end": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      if (!callId) break
      const toolName = extractString(payload, ["tool_name", "tool", "name"]) ?? "Tool"
      const argsText =
        extractString(payload, ["args_text", "args"]) ??
        this.toolCallArgsById.get(callId) ??
        ""
      const callEntryId = this.toolLogEntryByCallId.get(callId) ?? null
      const normalizedPayload: Record<string, unknown> = {
        ...payload,
        call_id: callId,
        tool: toolName,
        tool_name: toolName,
        ...(argsText ? { args_text: argsText } : {}),
      }
      const toolText = this.formatToolDisplayText(normalizedPayload)
      if (callEntryId) {
        this.updateToolEntry(callEntryId, { text: toolText, status: "pending" })
      }
      break
    }
    case "tool.exec.start": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      const execId = extractString(payload, ["exec_id", "execId"]) ?? null
      const toolName = extractString(payload, ["tool_name", "tool"]) ?? "Tool"
      const command = extractString(payload, ["command"]) ?? "running"
      const meta = { toolName, command }
      if (execId) this.toolExecMetaById.set(execId, meta)
      if (callId) this.toolExecMetaById.set(callId, meta)
      const slotKey = callId ?? execId
      if (slotKey) {
        let slotId = callId ? this.toolSlotsByCallId.get(callId) ?? null : null
        if (!slotId) {
          slotId = this.toolExecSlotsById.get(slotKey) ?? null
        }
        if (!slotId) {
          slotId = createSlotId()
          this.toolExecSlotsById.set(slotKey, slotId)
        }
        this.upsertLiveSlot(slotId, `${toolName}: ${command}`, BRAND_COLORS.duneOrange, "pending")
      }
      break
    }
    case "tool.exec.stdout.delta":
    case "tool.exec.stderr.delta": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      const execId = extractString(payload, ["exec_id", "execId"]) ?? null
      if (!callId && !execId) break
      const delta = extractRawString(payload, ["delta", "text"]) ?? ""
      const outputKey = callId ?? execId
      if (!outputKey) break
      if (event.type === "tool.exec.stdout.delta") {
        this.appendToolExecOutput(outputKey, "stdout", delta)
      } else {
        this.appendToolExecOutput(outputKey, "stderr", delta)
      }
      const slotKey = callId ?? execId
      const slotId = slotKey
        ? (callId ? this.toolSlotsByCallId.get(callId) ?? this.toolExecSlotsById.get(slotKey) ?? null : this.toolExecSlotsById.get(slotKey) ?? null)
        : null
      if (slotId) {
        const meta = (callId && this.toolExecMetaById.get(callId)) || (execId && this.toolExecMetaById.get(execId)) || {}
        const preview = this.formatToolExecPreview(this.toolExecOutputByCallId.get(outputKey) ?? null)
        const base = `${meta.toolName ?? "Tool"}: ${meta.command ?? "running"}`
        const text = preview ? `${base} • ${preview}` : base
        this.upsertLiveSlot(slotId, text, BRAND_COLORS.duneOrange, "pending")
      }
      break
    }
    case "tool.exec.end": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      const execId = extractString(payload, ["exec_id", "execId"]) ?? null
      const exitCode = parseNumberish(payload.exit_code ?? payload.exitCode)
      const slotKey = callId ?? execId
      if (slotKey) {
        const slotId = callId
          ? this.toolSlotsByCallId.get(callId) ?? this.toolExecSlotsById.get(slotKey) ?? null
          : this.toolExecSlotsById.get(slotKey) ?? null
        if (slotId) {
          this.finalizeLiveSlot(slotId, exitCode === 0 || exitCode == null ? "success" : "error")
        }
        this.toolExecSlotsById.delete(slotKey)
      }
      const outputKey = callId ?? execId
      if (outputKey && !callId) {
        this.takeToolExecOutput(outputKey)
      }
      if (execId) this.toolExecMetaById.delete(execId)
      if (callId) this.toolExecMetaById.delete(callId)
      break
    }
    case "tool_call": {
      const payload = isRecord(event.payload) ? event.payload : {}
      this.handleToolCall(payload, null, false, true)
      break
    }
    case "tool.result": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const callId = this.resolveToolCallId(payload)
      const normalizedPayload: Record<string, unknown> = {
        ...payload,
        call_id: callId ?? undefined,
        tool: payload.tool_name ?? payload.tool,
        status: payload.ok === false ? "error" : payload.status ?? undefined,
        result: payload.result ?? payload.output ?? payload,
        ...(payload.display !== undefined ? { display: payload.display } : {}),
      }
      this.handleToolResult(normalizedPayload, callId ?? null)
      break
    }
    case "tool_result": {
      const payload = isRecord(event.payload) ? event.payload : {}
      this.handleToolResult(payload)
      break
    }
    case "reward_update": {
      this.finalizeStreamingEntry()
      if (this.pendingResponse) {
        this.setActivityStatus?.("Reward update received", { to: "responding", eventType: event.type, source: "event" })
      }
      const summary = JSON.stringify(event.payload.summary ?? event.payload)
      this.addTool("reward", `[reward] ${summary}`, "success")
      this.upsertLiveSlot("reward", `Reward update: ${summary}`, SEMANTIC_COLORS.info, "success", 2000)
      break
    }
    case "log_link": {
      this.finalizeStreamingEntry()
      break
    }
    case "usage.update": {
      const payload = isRecord(event.payload) ? event.payload : {}
      if (DEBUG_EVENTS) {
        this.addTool("status", `[usage] ${JSON.stringify(payload)}`)
      }
      break
    }
    case "warning": {
      this.finalizeStreamingEntry()
      const payload = isRecord(event.payload) ? event.payload : {}
      const message = extractString(payload, ["message", "detail", "summary"]) ?? JSON.stringify(payload)
      this.pushHint(`[warning] ${message}`)
      this.addTool("status", `[warning] ${message}`, "error")
      break
    }
    case "interrupt":
    case "cancel.requested": {
      this.noteStopRequested()
      this.setActivityStatus?.("Stopping…", { to: "cancelled", eventType: event.type, source: "event" })
      this.addTool("status", `[${event.type}] stopping`, "pending")
      break
    }
    case "cancel.acknowledged": {
      this.clearStopRequest()
      this.pendingResponse = false
      this.activePromptOutcomeUnresolved = false
      this.pendingStartedAt = null
      settlePendingToolLogEntries(this, "error")
      this.thinkingInlineEntryId = null
      closeThinkingPreview(this, clockNow(this))
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, clockNow(this))
      }
      this.setActivityStatus?.("Cancelled", { to: "cancelled", eventType: event.type, source: "event" })
      this.addTool("status", "[cancel] acknowledged", "success")
      break
    }
    case "error": {
      this.finalizeStreamingEntry()
      const raw = JSON.stringify(event.payload)
      const message = formatErrorPayload(event.payload)
      if (DEBUG_EVENTS && message !== raw) {
        console.error(`[repl error payload] ${raw}`)
      }
      surfaceTerminalError(this, message, event.type)
      break
    }
    case "completion": {
      this.finalizeStreamingEntry()
      this.clearStopRequest()
      const view = formatCompletion(event.payload)
      const pendingDurationMs = takePendingDurationMs(this)
      const cookedHint = pendingDurationMs != null ? `✻ Cooked for ${formatDurationMs(pendingDurationMs)}` : view.hint
      if (DEBUG_EVENTS) {
        console.log(
          `[repl event] completion`,
          JSON.stringify({
            session: this.sessionId,
            completed: view.completed,
            hints: view.hint,
          }),
        )
      }
      this.completionReached = view.completed
      this.completionSeen = true
      this.activePromptOutcomeUnresolved = false
      this.lastCompletion = {
        completed: view.completed,
        summary: (event.payload && (event.payload.summary as Record<string, unknown> | undefined)) ?? null,
      }
      this.pendingResponse = false
      this.pendingResponseEventCountAtSubmit = null
      this.ownedEngineRecoveryAttempts = 0
      settlePendingToolLogEntries(this, view.completed ? "success" : "error")
      this.thinkingInlineEntryId = null
      closeThinkingPreview(this, clockNow(this))
      this.setActivityStatus?.(view.status, {
        to: view.completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, clockNow(this))
      }
      const completionErrorDisplayMessage = view.errorNotice ? compactDiagnosticMessage(view.errorNotice.message) : null
      const completionErrorText = completionErrorDisplayMessage ? `[error] ${completionErrorDisplayMessage}` : null
      const shouldSurfaceCompletionError = view.errorNotice
        ? markTerminalErrorUnseen(this, view.errorNotice.message)
        : false
      if (view.conversationLine && !hasEquivalentFinalAssistantEntry(this, view.conversationLine)) {
        const lastEntry = this.conversation.length > 0 ? this.conversation[this.conversation.length - 1] : undefined
        if (!(lastEntry && lastEntry.speaker === "system" && lastEntry.text === view.conversationLine)) {
          this.addConversation("system", view.conversationLine)
        }
      }
      if (completionErrorText && shouldSurfaceCompletionError) {
        const lastEntry = this.conversation.length > 0 ? this.conversation[this.conversation.length - 1] : undefined
        if (!(lastEntry && lastEntry.speaker === "system" && lastEntry.text === completionErrorText)) {
          this.addConversation("system", completionErrorText)
        }
      }
      if (cookedHint && !isLifecycleNoiseHint(cookedHint)) this.pushHint(cookedHint)
      if (completionErrorText && shouldSurfaceCompletionError) {
        this.addTool(
          "error",
          completionErrorText,
          "error",
          view.errorNotice?.detail && view.errorNotice.detail.length > 0
            ? {
                  display: {
                    title: "Runtime error",
                    summary: completionErrorDisplayMessage ?? view.errorNotice.message,
                    detail: view.errorNotice.detail,
                  },
              }
            : undefined,
        )
      }
      if (view.warningSlot) {
        this.upsertLiveSlot("guardrail", view.warningSlot.text, view.warningSlot.color, "error")
        this.setGuardrailNotice(view.warningSlot.text, cookedHint ?? view.conversationLine)
      } else if (completionErrorText && shouldSurfaceCompletionError) {
        this.upsertLiveSlot("guardrail", completionErrorText, SEMANTIC_COLORS.warning, "error")
        this.setGuardrailNotice(
          `Error: ${completionErrorDisplayMessage ?? view.errorNotice?.message ?? "Request failed"}`,
          view.errorNotice?.detail && view.errorNotice.detail.length > 0
            ? view.errorNotice.detail.join("\n")
            : cookedHint ?? view.conversationLine,
        )
      } else {
        this.removeLiveSlot("guardrail")
        this.clearGuardrailNotice()
      }
      this.removeLiveSlot("provider-retry")
      this.toolSlotsByCallId.forEach((slotId: string) => this.removeLiveSlot(slotId))
      this.toolSlotsByCallId.clear()
      this.toolSlotFallback.splice(0).forEach((slotId: string) => this.removeLiveSlot(slotId))
      this.removeLiveSlot("reward")
      break
    }
    case "run.end": {
      this.finalizeStreamingEntry()
      this.clearStopRequest()
      const payload = isRecord(event.payload) ? event.payload : {}
      const completed = Boolean(payload.completed)
      this.pendingResponse = false
      this.activePromptOutcomeUnresolved = false
      this.pendingStartedAt = null
      settlePendingToolLogEntries(this, completed ? "success" : "error")
      this.thinkingInlineEntryId = null
      closeThinkingPreview(this, clockNow(this))
      this.setActivityStatus?.(completed ? "Finished" : "Halted", {
        to: completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, clockNow(this))
      }
      if (!this.completionSeen) {
        this.completionSeen = true
        this.completionReached = completed
      }
      break
    }
    case "run_finished": {
      this.finalizeStreamingEntry()
      this.clearStopRequest()
      if (typeof event.payload?.eventCount === "number" && Number.isFinite(event.payload.eventCount)) {
        this.stats.eventCount = event.payload.eventCount
      }
      const completed = Boolean(event.payload?.completed)
      this.pendingResponse = false
      this.activePromptOutcomeUnresolved = false
      this.pendingStartedAt = null
      settlePendingToolLogEntries(this, completed ? "success" : "error")
      this.thinkingInlineEntryId = null
      closeThinkingPreview(this, clockNow(this))
      this.setActivityStatus?.(completed ? "Finished" : "Halted", {
        to: completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, clockNow(this))
      }
      if (!this.completionSeen) {
        this.completionSeen = true
        this.completionReached = completed
      }
      break
    }
    default:
      break
  }
  } finally {
    this.currentEventSeq = prevSeq
  }
}

export function normalizeAssistantText(this: any, text: string): string {
  const trimmed = text.trim()
  if (trimmed.startsWith("<TOOL_CALL>") && trimmed.includes("mark_task_complete")) {
    return "No coding task detected. Describe a concrete change (e.g., \"Implement bubble sort in sorter.py\") or switch models with /model."
  }
  return stripAssistantCompletionSentinel(text)
}

const rebindFinalAssistantEntryForCumulativeMessage = (controller: any, normalizedText: string): boolean => {
  if (controller.streamingEntryId) return false
  const conversation = Array.isArray(controller.conversation) ? controller.conversation : []
  const lastEntry = conversation.length > 0 ? conversation[conversation.length - 1] : null
  if (!lastEntry || lastEntry.speaker !== "assistant" || lastEntry.phase !== "final") return false
  const previousText = String(lastEntry.text ?? "")
  if (!previousText.trim()) return false
  const compatible =
    normalizedText === previousText ||
    normalizedText.startsWith(previousText) ||
    previousText.startsWith(normalizedText)
  if (!compatible) return false
  controller.streamingEntryId = lastEntry.id
  conversation[conversation.length - 1] = {
    ...lastEntry,
    text: normalizedText,
    phase: "streaming",
  }
  return true
}

const isCompatibleCumulativeAssistantText = (previousText: string, nextText: string): boolean => {
  const previous = String(previousText ?? "")
  const next = String(nextText ?? "")
  if (!previous.trim() || !next.trim()) return true
  return next === previous || next.startsWith(previous) || previous.startsWith(next)
}

const shouldIgnoreStaleCumulativeAssistantMessage = (controller: any, normalizedText: string): boolean => {
  if (!controller.streamingEntryId) return false
  const conversation = Array.isArray(controller.conversation) ? controller.conversation : []
  const active = conversation.find((entry: { id: string }) => entry.id === controller.streamingEntryId)
  if (!active || active.speaker !== "assistant") return false
  if (isCompatibleCumulativeAssistantText(String(active.text ?? ""), normalizedText)) return false
  return hasEquivalentFinalAssistantEntry(controller, normalizedText)
}

export function normalizeUserEchoText(this: any, text: string): string {
  const normalized = text.replace(/\r\n/g, "\n")
  const promptSuffixMarkers = [
    "\n\nindustry_coder_refs/",
    "\n\n<TOOLS_AVAILABLE>",
    "\n\n**PRIMARY FORMAT",
    "\n\n# PRIMARY FORMAT",
  ]
  let cutoff = normalized.length
  for (const marker of promptSuffixMarkers) {
    const index = normalized.indexOf(marker)
    if (index >= 0) cutoff = Math.min(cutoff, index)
  }
  return normalized.slice(0, cutoff).trimEnd()
}

export function appendAssistantDelta(this: any, delta: string): void {
  if (!delta) return
  if (this.streamingEntryId) {
    const index = this.conversation.findIndex((entry: { id: string }) => entry.id === this.streamingEntryId)
    if (index >= 0) {
      const existing = this.conversation[index]
      const nextText = `${existing.text ?? ""}${delta}`
      this.conversation[index] = { ...existing, text: nextText, phase: "streaming" }
    } else {
      this.setStreamingConversation("assistant", delta)
    }
  } else {
    this.setStreamingConversation("assistant", delta)
  }
  this.appendMarkdownDelta(delta)
}

export function noteStopRequested(this: any): void {
  this.stopRequestedAt = clockNow(this)
  if (this.stopTimer) {
    const clear = this.clock?.clearTimeout?.bind(this.clock) ?? clearTimeout
    clear(this.stopTimer)
  }
  const schedule = this.clock?.setTimeout?.bind(this.clock) ?? setTimeout
  this.stopTimer = schedule(() => {
    if (this.stopRequestedAt && !this.completionSeen && !this.disconnected) {
      this.pushHint("Still stopping…")
      this.setActivityStatus?.("Still stopping…", { to: "cancelled", eventType: "stop.timeout", source: "runtime" })
      this.emitChange()
    }
  }, STOP_SOFT_TIMEOUT_MS)
}

export function clearStopRequest(this: any): void {
  if (this.stopTimer) {
    const clear = this.clock?.clearTimeout?.bind(this.clock) ?? clearTimeout
    clear(this.stopTimer)
    this.stopTimer = null
  }
  this.stopRequestedAt = null
}
