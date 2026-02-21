import type { StreamEventEnvelope } from "./hostController"

export type TranscriptRenderState = {
  totalEvents: number
  lastEventType: string | null
  lines: string[]
}

export type ReduceOptions = {
  maxLines?: number
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

export const reduceTranscriptEvents = (
  input: TranscriptRenderState,
  events: StreamEventEnvelope[],
  options: ReduceOptions = {},
): TranscriptRenderState => {
  const maxLines = Math.max(20, options.maxLines ?? 200)
  const next: TranscriptRenderState = {
    totalEvents: input.totalEvents,
    lastEventType: input.lastEventType,
    lines: input.lines.slice(),
  }
  for (const event of events) {
    const normalizedType = normalizeType(event.type)
    const payload = toRecord(event.payload) ?? {}
    const summary = summarizePayload(payload)
    next.totalEvents += 1
    next.lastEventType = normalizedType
    const line = summary.length > 0 ? `[${normalizedType}] ${summary}` : `[${normalizedType}]`
    next.lines.push(line)
    if (next.lines.length > maxLines) {
      next.lines.splice(0, next.lines.length - maxLines)
    }
  }
  return next
}
