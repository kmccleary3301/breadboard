import type { StreamEventEnvelope } from "./hostController"

export type TranscriptEntryKind =
  | "assistant"
  | "assistant_delta"
  | "user"
  | "tool_call"
  | "tool_result"
  | "permission_request"
  | "permission_response"
  | "task_event"
  | "warning"
  | "error"
  | "event"

export type TranscriptEntry = {
  id: string
  type: string
  kind: TranscriptEntryKind
  summary: string
  detail?: string
  tool?: string
  status?: string
  requestId?: string
  decision?: string
  filePath?: string
  artifactPath?: string
  payloadPreview?: string
}

export type TranscriptRenderState = {
  totalEvents: number
  lastEventType: string | null
  lines: string[]
  entries: TranscriptEntry[]
}

export type ReduceOptions = {
  maxLines?: number
  maxEntries?: number
}

const toRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.length > 0 ? value : null

const normalizeType = (eventType: string): string => {
  if (eventType === "tool.result") return "tool_result"
  if (eventType === "assistant.message.delta") return "assistant_delta"
  if (eventType === "assistant.message.start") return "assistant_message_start"
  if (eventType === "assistant.message.end") return "assistant_message_end"
  return eventType
}

const summarizePayload = (payload: Record<string, unknown>): string => {
  const delta = toRecord(payload.delta)
  const detail = toRecord(payload.detail)
  const text =
    readString(payload.text) ??
    readString(payload.message) ??
    readString(payload.error) ??
    readString(delta?.text) ??
    readString(detail?.text) ??
    readString(payload.tool_name) ??
    readString(payload.tool) ??
    readString(payload.title) ??
    readString(payload.summary)
  if (text) return text.slice(0, 180)
  const compact = JSON.stringify(payload)
  return compact && compact !== "{}" ? compact.slice(0, 180) : ""
}

const previewPayload = (payload: Record<string, unknown>): string => {
  const compact = JSON.stringify(payload)
  if (!compact || compact === "{}") return ""
  return compact.length > 240 ? `${compact.slice(0, 240)}...` : compact
}

const buildEntry = (event: StreamEventEnvelope, normalizedType: string, payload: Record<string, unknown>): TranscriptEntry => {
  const base: TranscriptEntry = {
    id: event.id,
    type: normalizedType,
    kind: "event",
    summary: summarizePayload(payload) || normalizedType,
  }
  if (normalizedType === "assistant_message" || normalizedType === "assistant_message_end") {
    return {
      ...base,
      kind: "assistant",
      summary: summarizePayload(payload) || "assistant response",
      detail: readString(payload.text) ?? undefined,
    }
  }
  if (normalizedType === "assistant_delta" || normalizedType === "assistant_message_start") {
    return {
      ...base,
      kind: "assistant_delta",
      summary: summarizePayload(payload) || "assistant streaming...",
      detail: readString(payload.text) ?? undefined,
    }
  }
  if (normalizedType === "user_message") {
    return {
      ...base,
      kind: "user",
      summary: summarizePayload(payload) || "user message",
      detail: readString(payload.text) ?? undefined,
    }
  }
  if (normalizedType === "tool_call") {
    const tool = readString(payload.tool)
    const action = readString(payload.action)
    return {
      ...base,
      kind: "tool_call",
      summary: [tool, action].filter(Boolean).join(" · ") || "tool call",
      detail: summarizePayload(payload),
      tool: tool ?? undefined,
      payloadPreview: previewPayload(payload),
    }
  }
  if (normalizedType === "tool_result") {
    const tool = readString(payload.tool)
    const status = readString(payload.status) ?? (payload.error === true ? "error" : "ok")
    const artifact = toRecord(payload.artifact_ref)
    return {
      ...base,
      kind: "tool_result",
      summary: summarizePayload(payload) || "tool result",
      detail: summarizePayload(payload),
      tool: tool ?? undefined,
      status,
      artifactPath: readString(artifact?.path) ?? readString(payload.artifact_path) ?? undefined,
      filePath: readString(payload.path) ?? undefined,
      payloadPreview: previewPayload(payload),
    }
  }
  if (normalizedType === "permission_request") {
    return {
      ...base,
      kind: "permission_request",
      summary: summarizePayload(payload) || "permission requested",
      detail: readString(payload.summary) ?? summarizePayload(payload),
      requestId: readString(payload.request_id) ?? readString(payload.id) ?? undefined,
      tool: readString(payload.tool) ?? undefined,
      payloadPreview: previewPayload(payload),
    }
  }
  if (normalizedType === "permission_response") {
    return {
      ...base,
      kind: "permission_response",
      summary: summarizePayload(payload) || "permission decision",
      requestId: readString(payload.request_id) ?? readString(payload.id) ?? undefined,
      decision: readString(payload.decision) ?? readString(payload.response) ?? undefined,
      payloadPreview: previewPayload(payload),
    }
  }
  if (normalizedType === "task_event") {
    return {
      ...base,
      kind: "task_event",
      summary: summarizePayload(payload) || "task update",
      detail: readString(payload.description) ?? readString(payload.summary) ?? undefined,
      status: readString(payload.status) ?? undefined,
      payloadPreview: previewPayload(payload),
    }
  }
  if (normalizedType === "warning") {
    return { ...base, kind: "warning", summary: summarizePayload(payload) || "warning", payloadPreview: previewPayload(payload) }
  }
  if (normalizedType === "error") {
    return { ...base, kind: "error", summary: summarizePayload(payload) || "error", payloadPreview: previewPayload(payload) }
  }
  return { ...base, payloadPreview: previewPayload(payload) }
}

export const reduceTranscriptEvents = (
  input: TranscriptRenderState,
  events: StreamEventEnvelope[],
  options: ReduceOptions = {},
): TranscriptRenderState => {
  const maxLines = Math.max(20, options.maxLines ?? 200)
  const maxEntries = Math.max(20, options.maxEntries ?? 200)
  const next: TranscriptRenderState = {
    totalEvents: input.totalEvents,
    lastEventType: input.lastEventType,
    lines: input.lines.slice(),
    entries: input.entries.slice(),
  }
  for (const event of events) {
    const normalizedType = normalizeType(event.type)
    const payload = toRecord(event.payload) ?? {}
    const summary = summarizePayload(payload)
    next.totalEvents += 1
    next.lastEventType = normalizedType
    const line = summary.length > 0 ? `[${normalizedType}] ${summary}` : `[${normalizedType}]`
    next.lines.push(line)
    next.entries.push(buildEntry(event, normalizedType, payload))
    if (next.lines.length > maxLines) {
      next.lines.splice(0, next.lines.length - maxLines)
    }
    if (next.entries.length > maxEntries) {
      next.entries.splice(0, next.entries.length - maxEntries)
    }
  }
  return next
}
