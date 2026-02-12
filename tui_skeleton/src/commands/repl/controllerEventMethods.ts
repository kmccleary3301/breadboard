import { ApiError } from "../../api/client.js"
import type {
  CTreeNode,
  CTreeSnapshotSummary,
  SessionEvent,
} from "../../api/types.js"
import { reduceCTreeModel } from "../../repl/ctrees/reducer.js"
import { BRAND_COLORS, SEMANTIC_COLORS } from "../../repl/designSystem.js"
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
} from "../../repl/types.js"
import {
  appendThinkingArtifact,
  createThinkingArtifact,
  finalizeThinkingArtifact,
  normalizeThinkingSignal,
} from "./controllerActivityRuntime.js"
import {
  DEBUG_EVENTS,
  MAX_RETRIES,
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
import { resolveSubagentUiPolicy, type SubagentUiPolicy } from "./subagentUiPolicy.js"
type CompletionView = {
  completed: boolean
  status: string
  toolLine: string
  hint?: string
  conversationLine?: string
  warningSlot?: { text: string; color?: string }
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

const notePendingStart = (controller: { pendingStartedAt: number | null }): void => {
  if (controller.pendingStartedAt == null) {
    controller.pendingStartedAt = Date.now()
  }
}

const takePendingDurationMs = (controller: { pendingStartedAt: number | null }): number | null => {
  const startedAt = controller.pendingStartedAt
  controller.pendingStartedAt = null
  if (typeof startedAt === "number" && Number.isFinite(startedAt)) {
    const elapsed = Date.now() - startedAt
    return elapsed >= 0 ? elapsed : 0
  }
  return null
}

const resolveThinkingMode = (controller: {
  viewPrefs?: { showReasoning?: boolean }
  runtimeFlags?: { allowFullThinking?: boolean; thinkingEnabled?: boolean }
}): ThinkingMode =>
  controller.viewPrefs?.showReasoning && controller.runtimeFlags?.allowFullThinking === true
    ? "full"
    : "summary"

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
  const now = Date.now()
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

const emitSubagentToast = (
  controller: any,
  payload: Record<string, unknown>,
  eventType: string,
  policy: SubagentUiPolicy,
): void => {
  if (!policy.routeTaskEventsToLiveSlots) return
  const taskId = extractString(payload, ["task_id", "taskId", "id"]) ?? "task"
  const statusRaw = resolveSubagentStatus(payload)
  const status = normalizeSubagentStatus(statusRaw)
  const description =
    extractString(payload, ["description", "title", "summary", "prompt", "subagent_type", "subagentType"]) ?? taskId
  const now = Date.now()
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
  return typeof seq === "number" && Number.isFinite(seq) ? seq : Date.now()
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
    id: controller.nextConversationId?.() ?? `conv-thinking-${Date.now().toString(36)}`,
    speaker: "assistant" as const,
    text: `${marker}\n${line}`,
    phase: "final" as const,
    createdAt: resolveEventTimestamp(controller),
  }
  controller.conversation.push(entry)
  controller.thinkingInlineEntryId = entry.id
}

const formatCompletion = (payload: unknown): CompletionView => {
  const data = isRecord(payload) ? payload : {}
  const completed = Boolean(
    data.completed ??
      data.complete ??
      data.success ??
      data.ok ??
      (typeof data.status === "string" && data.status.toLowerCase().includes("complete")),
  )
  const reason = extractString(data, ["reason", "message", "detail", "status"]) ?? ""
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
    extractString(data, ["conversation", "final_message", "summary", "result", "output"]) ?? undefined
  const warningSource = isRecord(data.warning) ? data.warning : isRecord(data.guardrail) ? data.guardrail : null
  const warningText =
    (warningSource ? extractString(warningSource, ["text", "summary", "message", "detail"]) : undefined) ??
    extractString(data, ["warning", "guardrail", "notice"])
  const warningColor = warningSource ? extractString(warningSource, ["color"]) : undefined
  const warningSlot = warningText
    ? { text: warningText, color: warningColor ?? SEMANTIC_COLORS.warning }
    : undefined
  return { completed, status, toolLine, hint, conversationLine, warningSlot }
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
  let callEntry = callId ? this.toolLogEntryByCallId.get(callId) ?? null : null
  if (shouldLog) {
    if (callEntry) {
      this.updateToolEntry(callEntry, { text: toolText, status: "pending", ...(display ? { display } : {}) })
    } else {
      callEntry = this.addTool("call", toolText, "pending", { callId, display }).id
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
  const todos = this.extractTodosFromPayload(payload)
  if (todos) {
    this.todos = todos
    this.pushHint(`Todos updated (${todos.length}).`)
  }
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
  const display = this.resolveToolDisplayPayload(payload)
  const toolText = this.formatToolDisplayText(display ? { ...payload, display } : payload)
  const cleanedToolText = stripAnsi(toolText).trim()
  const shouldLog = Boolean(cleanedToolText && cleanedToolText !== "Tool")
  if (callEntryId) {
    this.updateToolEntry(callEntryId, {
      ...(shouldLog ? { text: toolText } : {}),
      status: resultWasError ? "error" : "success",
      ...(display ? { display } : {}),
    })
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
  const todos = this.extractTodosFromPayload(payload)
  if (todos) {
    this.todos = todos
    this.pushHint(`Todos updated (${todos.length}).`)
  }
}

export async function streamLoop(this: any): Promise<void> {
  const appConfig = this.providers.args.config
  const retrySuffix = Number.isFinite(MAX_RETRIES) ? `/${MAX_RETRIES}` : ""
  while (!this.abortRequested) {
    this.abortController = new AbortController()
    try {
      const resumeFrom = this.lastEventId ?? undefined
      const warnOnReconnect = this.hasStreamedOnce
      let warned = false
      for await (const event of this.providers.sdk.stream(this.sessionId, {
        signal: this.abortController.signal,
        lastEventId: resumeFrom,
      })) {
        if (warnOnReconnect && !warned) {
          this.pushHint("Reconnected to stream; some history may be missing.")
          warned = true
        }
        this.hasStreamedOnce = true
        this.disconnected = false
        this.consecutiveFailures = 0
        this.stats.eventCount += 1
        this.stats.lastTurn = event.turn ?? this.stats.lastTurn
        const seqValue = typeof event.seq === "number" && Number.isFinite(event.seq) ? event.seq : null
        if (seqValue !== null) {
          this.lastEventId = String(seqValue)
        } else if (typeof event.id === "string" && /^[0-9]+$/.test(event.id.trim())) {
          this.lastEventId = event.id.trim()
        }
        this.enqueueEvent(event)
      }
      if (!this.awaitingRestart) {
        this.pendingResponse = false
        this.consecutiveFailures += 1
        const retriesExhausted =
          Number.isFinite(MAX_RETRIES) && this.consecutiveFailures > MAX_RETRIES
        if (retriesExhausted) {
          this.pushHint("Stream ended unexpectedly and reconnection attempts exhausted.")
          this.setActivityStatus?.("Disconnected", { to: "halted", eventType: "stream.end", source: "runtime" })
          this.disconnected = true
          this.emitChange()
          break
        }
        const retryDelay = Math.min(4000, 500 * 2 ** (this.consecutiveFailures - 1))
        this.pushHint(
          `Stream ended unexpectedly. Retrying in ${retryDelay}ms (attempt ${this.consecutiveFailures}${retrySuffix}).`,
        )
        maybeLifecycleToast(this, "Reconnecting", "stream.reconnect")
        this.setActivityStatus?.(`Reconnecting (${this.consecutiveFailures}${retrySuffix})`, {
          to: "reconnecting",
          eventType: "stream.reconnect",
          source: "runtime",
        })
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
      if (error instanceof ApiError && error.status === 409) {
        this.pendingResponse = false
        this.pushHint("Stream resume window exceeded; reconnecting without history.")
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
      const message = error instanceof Error ? error.message : String(error)
      const retriesExhausted =
        Number.isFinite(MAX_RETRIES) && this.consecutiveFailures > MAX_RETRIES
      if (retriesExhausted) {
        this.pendingResponse = false
        this.pushHint(`Stream interruption: ${message}. Giving up after ${MAX_RETRIES} attempts.`)
        this.setActivityStatus?.("Disconnected", { to: "halted", eventType: "stream.error", source: "runtime" })
        this.disconnected = true
        this.emitChange()
        break
      }
      this.pendingResponse = false
      this.pushHint(
        `Stream interruption: ${message}. Retrying in ${delay}ms (attempt ${this.consecutiveFailures}${retrySuffix}).`,
      )
      maybeLifecycleToast(this, "Reconnecting", "stream.error")
      this.setActivityStatus?.(`Reconnecting (${this.consecutiveFailures}${retrySuffix})`, {
        to: "reconnecting",
        eventType: "stream.error",
        source: "runtime",
      })
      this.emitChange()
      await sleep(delay)
    }
  }
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
  if (this.eventsScheduled) return
  this.eventsScheduled = true
  queueMicrotask(() => {
    this.eventsScheduled = false
    const queue = this.pendingEvents
    this.pendingEvents = []
    for (const item of queue) {
      this.applyEvent(item)
    }
    this.emitChange()
  })
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
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
      }
      if (this.runtimeFlags?.thinkingEnabled !== false) {
        const mode: ThinkingMode = resolveThinkingMode(this)
        this.thinkingArtifact = createThinkingArtifact(mode, Date.now())
      } else {
        this.thinkingArtifact = null
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
      this.finalizeStreamingEntry()
      markAssistantResponding(this, event.type)
      this.setStreamingConversation("assistant", "")
      break
    }
    case "assistant.message.delta": {
      markAssistantResponding(this, event.type)
      const payload = isRecord(event.payload) ? event.payload : {}
      const delta = extractRawString(payload, ["delta", "text"])
      if (delta) {
        this.appendAssistantDelta(delta)
      }
      break
    }
    case "assistant.message.end": {
      this.finalizeStreamingEntry()
      break
    }
    case "assistant_message": {
      markAssistantResponding(this, event.type)
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const text = typeof raw === "string" ? raw : ""
      const normalizedText = this.normalizeAssistantText(text)
      const trimmed = normalizedText.trim()
      if (!trimmed || trimmed.toLowerCase() === "none") break
      this.addConversation("assistant", normalizedText, "streaming")
      this.appendMarkdownChunk(normalizedText)
      // Non-streaming assistant_message events can finalize immediately.
      this.finalizeStreamingEntry()
      break
    }
    case "assistant_delta": {
      markAssistantResponding(this, event.type)
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const delta = typeof raw === "string" ? raw : ""
      this.appendAssistantDelta(delta)
      break
    }
    case "assistant.reasoning.delta":
    case "assistant.thought_summary.delta": {
      const signal = normalizeThinkingSignal(event.type, event.payload)
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
        const seed = this.thinkingArtifact ?? createThinkingArtifact(mode, Date.now())
        this.thinkingArtifact = appendThinkingArtifact(seed, signal, this.runtimeFlags, Date.now())
        this.bumpRuntimeTelemetry?.("thinkingUpdates")
      }
      if (signal) {
        appendInlineThinkingBlock(this, signal)
      }
      if ((this.viewPrefs.showReasoning && this.runtimeFlags?.allowFullThinking === true) || DEBUG_EVENTS) {
        const rawPeekAllowed =
          this.runtimeFlags?.allowRawThinkingPeek === true || capabilities.rawThinkingPeek === true
        const peekText = signal?.kind === "summary" || rawPeekAllowed ? delta : ""
        if (!peekText.trim()) break
        const now = Date.now()
        if (!shouldThrottleReasoningPeek(this, now)) {
          this.addTool("status", `[reasoning] ${peekText}`, "pending")
          this.lastThinkingPeekAt = now
        }
      }
      break
    }
    case "user_message": {
      const raw = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      const text = typeof raw === "string" ? raw : ""
      const normalized = text.trim()
      if (this.pendingUserEcho && normalized && this.pendingUserEcho === normalized) {
        this.pendingUserEcho = null
        break
      }
      this.addConversation("user", text)
      break
    }
    case "user.message": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const raw = extractString(payload, ["text", "content", "message"]) ?? JSON.stringify(event.payload)
      const text = typeof raw === "string" ? raw : ""
      const normalized = text.trim()
      if (this.pendingUserEcho && normalized && this.pendingUserEcho === normalized) {
        this.pendingUserEcho = null
        break
      }
      this.addConversation("user", text)
      break
    }
    case "user.command": {
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
      const defaultScope = this.normalizeScope(payload.default_scope)
      const request: PermissionRequest = {
        requestId,
        tool,
        kind,
        rewindable,
        summary,
        diffText,
        ruleSuggestion,
        defaultScope,
        createdAt: Date.now(),
      }
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
      const request: PermissionRequest = {
        requestId,
        tool,
        kind,
        rewindable: payload.rewindable === false ? false : true,
        summary,
        diffText: diffText ?? null,
        ruleSuggestion: extractString(payload, ["rule", "rule_suggestion"]) ?? null,
        defaultScope: this.normalizeScope(payload.default_scope),
        createdAt: Date.now(),
      }
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
      const responseLabel = response ? response.replace(/[_-]+/g, " ") : "response received"
      const denied = Boolean(responseLabel.match(/\b(deny|denied|reject|rejected|block|blocked)\b/i))
      this.addTool("status", `[permission] ${responseLabel}`, "success")
      this.pushHint(`Permission ${responseLabel}.`)
      this.setPermissionActive(null)
      this.pendingResponse = !denied
      this.setActivityStatus?.(denied ? "Permission denied" : "Permission response received", {
        to: denied ? "halted" : "permission_resolved",
        eventType: event.type,
        source: "event",
      })
      break
    }
    case "permission.decision": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const response = extractString(payload, ["decision", "response"]) ?? "decision received"
      const responseLabel = response.replace(/[_-]+/g, " ")
      const denied = Boolean(responseLabel.match(/\b(deny|denied|reject|rejected|block|blocked)\b/i))
      this.addTool("status", `[permission] ${responseLabel}`, "success")
      this.pushHint(`Permission ${responseLabel}.`)
      this.setPermissionActive(null)
      this.pendingResponse = !denied
      this.setActivityStatus?.(denied ? "Permission denied" : "Permission response received", {
        to: denied ? "halted" : "permission_resolved",
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
      }
      this.pushHint("Skills catalog updated.")
      break
    }
    case "skills_selection": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const selection = (payload.selection ?? payload) as SkillSelection
      this.skillsSelection = selection
      if (this.skillsMenu.status === "ready") {
        this.skillsMenu = {
          status: "ready",
          catalog: this.skillsMenu.catalog,
          selection,
          sources: this.skillsMenu.sources,
        }
      }
      this.pushHint("Skills selection updated.")
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
      break
    }
    case "agent.spawn":
    case "agent.status":
    case "agent.end": {
      const payload = isRecord(event.payload) ? event.payload : {}
      const policy = resolveSubagentUiPolicy(this.runtimeFlags ?? {}, process.env)
      const status = event.type.split(".")[1] ?? "update"
      const description = extractString(payload, ["summary", "description", "title"]) ?? "Subagent"
      const taskId = extractString(payload, ["task_id", "id"]) ?? undefined
      const lineParts = [status, description, taskId].filter(Boolean)
      const line = lineParts.length > 0 ? lineParts.join(" · ") : JSON.stringify(payload)
      if (policy.routeTaskEventsToToolRail) {
        this.addTool("status", `[agent] ${line}`, status === "end" ? "success" : "pending")
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
      if (outputKey) {
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
      const payload = isRecord(event.payload) ? event.payload : {}
      const link = extractString(payload, ["url", "href", "path"]) ?? JSON.stringify(payload)
      this.addTool("status", `[log] ${link}`)
      this.pushHint("Log link available.")
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
      this.pendingStartedAt = null
      this.thinkingInlineEntryId = null
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
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
      this.pushHint(`[error] ${message}`)
      this.addTool("error", `[error] ${message}`, "error")
      this.pendingResponse = false
      this.pendingStartedAt = null
      this.thinkingInlineEntryId = null
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
      }
      this.setActivityStatus?.("Error received", { to: "error", eventType: event.type, source: "event" })
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
      this.lastCompletion = {
        completed: view.completed,
        summary: (event.payload && (event.payload.summary as Record<string, unknown> | undefined)) ?? null,
      }
      this.pendingResponse = false
      this.thinkingInlineEntryId = null
      this.setActivityStatus?.(view.status, {
        to: view.completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
      }
      if (view.conversationLine) {
        const lastEntry = this.conversation.length > 0 ? this.conversation[this.conversation.length - 1] : undefined
        if (!(lastEntry && lastEntry.speaker === "system" && lastEntry.text === view.conversationLine)) {
          this.addConversation("system", view.conversationLine)
        }
      }
      if (cookedHint) this.pushHint(cookedHint)
      if (view.warningSlot) {
        this.upsertLiveSlot("guardrail", view.warningSlot.text, view.warningSlot.color, "error")
        this.setGuardrailNotice(view.warningSlot.text, cookedHint ?? view.conversationLine)
      } else {
        this.removeLiveSlot("guardrail")
        this.clearGuardrailNotice()
      }
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
      const reason = typeof payload.reason === "string" ? payload.reason : undefined
      this.pendingResponse = false
      this.pendingStartedAt = null
      this.thinkingInlineEntryId = null
      this.setActivityStatus?.(completed ? "Finished" : "Halted", {
        to: completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
      }
      if (!this.completionSeen) {
        this.completionSeen = true
        this.completionReached = completed
      }
      const hint = reason ? `Run finished (${reason}).` : "Run finished."
      this.pushHint(hint)
      break
    }
    case "run_finished": {
      this.finalizeStreamingEntry()
      this.clearStopRequest()
      if (typeof event.payload?.eventCount === "number" && Number.isFinite(event.payload.eventCount)) {
        this.stats.eventCount = event.payload.eventCount
      }
      const completed = Boolean(event.payload?.completed)
      const reason = typeof event.payload?.reason === "string" ? event.payload.reason : undefined
      this.pendingResponse = false
      this.pendingStartedAt = null
      this.thinkingInlineEntryId = null
      this.setActivityStatus?.(completed ? "Finished" : "Halted", {
        to: completed ? "completed" : "halted",
        eventType: event.type,
        source: "event",
      })
      if (this.thinkingArtifact) {
        this.thinkingArtifact = finalizeThinkingArtifact(this.thinkingArtifact, Date.now())
      }
      if (!this.completionSeen) {
        this.completionSeen = true
        this.completionReached = completed
      }
      const hint = reason ? `Run finished (${reason}).` : "Run finished."
      this.pushHint(hint)
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
  return text
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
  this.stopRequestedAt = Date.now()
  if (this.stopTimer) clearTimeout(this.stopTimer)
  this.stopTimer = setTimeout(() => {
    if (this.stopRequestedAt && !this.completionSeen && !this.disconnected) {
      this.pushHint("Still stopping…")
      this.setActivityStatus?.("Still stopping…", { to: "cancelled", eventType: "stop.timeout", source: "runtime" })
      this.emitChange()
    }
  }, STOP_SOFT_TIMEOUT_MS)
}

export function clearStopRequest(this: any): void {
  if (this.stopTimer) {
    clearTimeout(this.stopTimer)
    this.stopTimer = null
  }
  this.stopRequestedAt = null
}
