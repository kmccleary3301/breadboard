import { createParser, type ParsedEvent, type ReconnectInterval, type EventSourceParseCallback } from "eventsource-parser"
import type { SessionEvent } from "./types.js"
import { ApiError, type BreadboardClientConfig } from "./client.js"

export interface StreamConfig extends BreadboardClientConfig {}
export interface EventStreamOptions { readonly signal?: AbortSignal; readonly query?: Record<string, string | number | boolean>; readonly config: StreamConfig; readonly lastEventId?: string }
export interface EventStreamHandlers { readonly onEvent: (event: SessionEvent) => void; readonly onOpen?: () => void; readonly onError?: (error: Event | Error) => void }
export interface OpenEventStreamOptions extends EventStreamOptions { readonly eventTypes?: readonly string[]; readonly initialRetryMs?: number; readonly maxRetryMs?: number }
export interface EventStreamHandle { close(): void }

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)
const stringValue = (value: unknown): string | null => typeof value === "string" && value.trim() ? value : null
const numberValue = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null
const normalize = (raw: unknown, sessionId: string, fallbackId: string | null, nextId: () => string): SessionEvent | null => {
  if (!record(raw)) return null
  const type = stringValue(raw.type) ?? stringValue(raw.event) ?? stringValue(raw.kind)
  if (!type) return null
  const timestamp = numberValue(raw.timestamp) ?? numberValue(raw.time) ?? numberValue(raw.ts) ?? Date.now()
  const timestampMs = numberValue(raw.timestamp_ms) ?? numberValue(raw.timestampMs) ?? numberValue(raw.ts_ms) ?? (timestamp > 10_000_000_000 ? timestamp : Math.round(timestamp * 1000))
  return { id: stringValue(raw.id) ?? stringValue(raw.event_id) ?? stringValue(raw.eventId) ?? fallbackId ?? nextId(), type: type as SessionEvent["type"], session_id: stringValue(raw.session_id) ?? stringValue(raw.sessionId) ?? sessionId, turn: numberValue(raw.turn) ?? numberValue(raw.turn_id) ?? numberValue(raw.turnId), timestamp: timestampMs, timestamp_ms: timestampMs, seq: numberValue(raw.seq) ?? undefined, v: numberValue(raw.v) ?? undefined, schema_rev: stringValue(raw.schema_rev) ?? stringValue(raw.schemaRev), run_id: stringValue(raw.run_id) ?? stringValue(raw.runId), thread_id: stringValue(raw.thread_id) ?? stringValue(raw.threadId), turn_id: stringValue(raw.turn_id) ?? stringValue(raw.turnId) ?? (typeof raw.turn_id === "number" ? raw.turn_id : undefined) ?? (typeof raw.turnId === "number" ? raw.turnId : undefined), span_id: stringValue(raw.span_id) ?? stringValue(raw.spanId), parent_span_id: stringValue(raw.parent_span_id) ?? stringValue(raw.parentSpanId), actor: record(raw.actor) ? raw.actor : null, visibility: stringValue(raw.visibility), tags: Array.isArray(raw.tags) ? raw.tags.filter((tag): tag is string => typeof tag === "string") : null, payload: (raw.payload ?? raw.data ?? raw.message ?? raw.body ?? {}) as SessionEvent["payload"] }
}
const streamUrl = (sessionId: string, config: StreamConfig, query: Record<string, string | number | boolean>, lastEventId?: string): URL => {
  const url = new URL(`v1/sessions/${encodeURIComponent(sessionId)}/events`, config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
  if (!("schema" in query) && !("v" in query)) query.schema = config.streamSchema ?? 2
  if (!("include_legacy" in query)) query.include_legacy = config.streamIncludeLegacy ?? false
  if (!("replay" in query) && !lastEventId) query.replay = true
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value))
  if (lastEventId) url.searchParams.set("from_id", lastEventId)
  return url
}
const resolveToken = async (config: StreamConfig, signal?: AbortSignal): Promise<string | undefined> => {
  if (signal?.aborted) return undefined
  const token = typeof config.authToken === "function" ? await config.authToken() : config.authToken
  return signal?.aborted ? undefined : token
}

export const streamSessionEvents = async function* (sessionId: string, options: EventStreamOptions): AsyncGenerator<SessionEvent, void, void> {
  if (options.signal?.aborted) return
  const controller = new AbortController(); const abort = options.signal ? () => controller.abort() : undefined
  options.signal?.addEventListener("abort", abort as EventListener, { once: true })
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const token = await resolveToken(options.config, options.signal); if (options.signal?.aborted) return
    const response = await fetch(streamUrl(sessionId, options.config, { ...(options.query ?? {}) }, options.lastEventId), { method: "GET", headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}) }, signal: controller.signal })
    if (!response.ok) throw new ApiError(`Streaming request failed with status ${response.status}`, response.status, await response.text().catch(() => ""))
    if (!response.body) throw new Error("Streaming response provided no body")
    reader = response.body.getReader(); const decoder = new TextDecoder(); const buffer: SessionEvent[] = []; let counter = 0
    const parser = createParser(((event: ParsedEvent | ReconnectInterval) => { if (event.type === "event" && "data" in event && event.data) { try { const parsed = normalize(JSON.parse(event.data), sessionId, event.id ?? null, () => `synthetic-${counter++}`); if (parsed) buffer.push(parsed) } catch { /* malformed events are ignored */ } } }) as EventSourceParseCallback)
    while (true) { if (options.signal?.aborted) break; const { value, done } = await reader.read(); if (done) break; if (value) { parser.feed(decoder.decode(value, { stream: true })); while (buffer.length) { const event = buffer.shift(); if (event) yield event } } }
    parser.reset()
  } finally { if (reader) { await reader.cancel().catch(() => undefined); reader.releaseLock() } options.signal?.removeEventListener("abort", abort as EventListener) }
}

export const openEventStream = (sessionId: string, handlers: EventStreamHandlers, options: OpenEventStreamOptions): EventStreamHandle => {
  const EventSourceCtor = globalThis.EventSource; if (typeof EventSourceCtor !== "function") throw new Error("EventSource is not available in this runtime")
  const query = { ...(options.query ?? {}) }; const config = options.config; let closed = false; let source: EventSource | undefined; let timer: ReturnType<typeof setTimeout> | undefined; let retry = options.initialRetryMs ?? 500; const maxRetry = options.maxRetryMs ?? 10_000; let counter = 0
  const closeSource = () => { source?.close(); source = undefined }
  const connect = () => { if (closed) return; closeSource(); source = new EventSourceCtor(streamUrl(sessionId, config, query, options.lastEventId)); source.onopen = () => { retry = options.initialRetryMs ?? 500; handlers.onOpen?.() }; const message = (event: MessageEvent<string>) => { try { const parsed = normalize(JSON.parse(event.data), sessionId, event.lastEventId || null, () => `synthetic-${counter++}`); if (parsed) handlers.onEvent(parsed) } catch (error) { handlers.onError?.(error instanceof Error ? error : new Error("Malformed SSE event")) } }; source.onmessage = message; source.onerror = (error) => { handlers.onError?.(error); closeSource(); if (!closed) { timer = setTimeout(connect, retry); retry = Math.min(maxRetry, Math.max(retry * 2, retry + 1)) } }; for (const type of options.eventTypes ?? []) source.addEventListener(type, message as EventListener) }
  const abort = () => { closed = true; if (timer) clearTimeout(timer); closeSource() }; options.signal?.addEventListener("abort", abort, { once: true }); if (!options.signal?.aborted) connect(); else abort()
  return { close() { closed = true; if (timer) clearTimeout(timer); options.signal?.removeEventListener("abort", abort); closeSource() } }
}
