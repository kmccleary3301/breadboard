import { createParser, ParsedEvent, ReconnectInterval, EventSourceParseCallback } from "eventsource-parser"
import { loadAppConfig } from "../config/appConfig.js"
import type { SessionEvent } from "./types.js"
import { ApiError } from "./client.js"

export interface StreamConfig {
  readonly baseUrl: string
  readonly authToken?: string
  readonly streamSchema?: number | null
  readonly streamIncludeLegacy?: boolean | null
}

export interface EventStreamOptions {
  readonly signal?: AbortSignal
  readonly query?: Record<string, string | number | boolean>
  readonly config?: StreamConfig
  readonly lastEventId?: string
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const readNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

const normalizeEvent = (
  raw: unknown,
  sessionIdFallback: string,
  fallbackId: string | null,
  syntheticId: () => string,
): SessionEvent | null => {
  if (!isRecord(raw)) return null
  const type =
    readString(raw.type) ??
    readString(raw.event) ??
    readString(raw.kind)
  if (!type) return null
  const sessionId = readString(raw.session_id) ?? readString(raw.sessionId) ?? sessionIdFallback
  const payload =
    raw.payload ??
    raw.data ??
    raw.message ??
    raw.body ??
    {}
  const timestampRaw =
    readNumber(raw.timestamp) ??
    readNumber(raw.time) ??
    readNumber(raw.ts)
  const timestampMsRaw =
    readNumber(raw.timestamp_ms) ??
    readNumber(raw.timestampMs) ??
    readNumber(raw.ts_ms)
  const timestampFallback = Date.now()
  const timestampValue = timestampRaw ?? timestampMsRaw ?? timestampFallback
  const timestampMs =
    timestampMsRaw ??
    (timestampValue > 10_000_000_000 ? timestampValue : Math.round(timestampValue * 1000))
  const id =
    readString(raw.id) ??
    readString(raw.event_id) ??
    readString(raw.eventId) ??
    fallbackId ??
    syntheticId()
  const turn =
    readNumber(raw.turn) ??
    readNumber(raw.turn_id) ??
    readNumber(raw.turnId) ??
    null
  const seq = readNumber(raw.seq) ?? undefined
  const v = readNumber(raw.v) ?? undefined
  const schemaRev = readString(raw.schema_rev) ?? readString(raw.schemaRev)
  const runId = readString(raw.run_id) ?? readString(raw.runId)
  const threadId = readString(raw.thread_id) ?? readString(raw.threadId)
  const spanId = readString(raw.span_id) ?? readString(raw.spanId)
  const parentSpanId = readString(raw.parent_span_id) ?? readString(raw.parentSpanId)
  const visibility = readString(raw.visibility) ?? undefined
  const actor = isRecord(raw.actor) ? raw.actor : undefined
  const tags = Array.isArray(raw.tags) ? raw.tags.filter((tag) => typeof tag === "string") : undefined
  const turnId =
    readString(raw.turn_id) ??
    readString(raw.turnId) ??
    (typeof raw.turn_id === "number" ? raw.turn_id : undefined) ??
    (typeof raw.turnId === "number" ? raw.turnId : undefined)
  return {
    id,
    type: type as SessionEvent["type"],
    session_id: sessionId,
    turn,
    timestamp: timestampMs,
    timestamp_ms: timestampMs,
    seq: seq ?? undefined,
    v,
    schema_rev: schemaRev ?? null,
    run_id: runId ?? null,
    thread_id: threadId ?? null,
    turn_id: turnId ?? null,
    span_id: spanId ?? null,
    parent_span_id: parentSpanId ?? null,
    actor: actor ?? null,
    visibility: visibility ?? null,
    tags: tags ?? null,
    payload: payload as SessionEvent["payload"],
  }
}

export const streamSessionEvents = async function* (
  sessionId: string,
  options: EventStreamOptions = {},
): AsyncGenerator<SessionEvent, void, void> {
  const config = options.config ?? loadAppConfig()
  const url = new URL(`/sessions/${sessionId}/events`, config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
  const query: Record<string, string | number | boolean> = { ...(options.query ?? {}) }
  if (!("schema" in query) && !("v" in query)) {
    query.schema = config.streamSchema ?? 2
  }
  if (!("include_legacy" in query)) {
    query.include_legacy = config.streamIncludeLegacy ?? false
  }
  if (!("replay" in query) && !options.lastEventId) {
    query.replay = true
  }
  if (Object.keys(query).length > 0) {
    for (const [key, value] of Object.entries(query)) {
      url.searchParams.set(key, String(value))
    }
  }
  const controller = new AbortController()
  const signal = options.signal
  const abortHandler = signal ? () => controller.abort() : undefined
  if (signal) {
    signal.addEventListener("abort", abortHandler as EventListener, { once: true })
  }
  const response = await fetch(url, {
    method: "GET",
    headers: {
      ...(config.authToken ? { Authorization: `Bearer ${config.authToken}` } : {}),
      ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
    },
    signal: controller.signal,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new ApiError(`Streaming request failed with status ${response.status}`, response.status, text)
  }
  if (!response.body) {
    throw new Error("Streaming response provided no body")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const buffer: SessionEvent[] = []
  let syntheticIdCounter = 0
  const nextSyntheticId = () => `synthetic-${syntheticIdCounter++}`
  const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
    if (event.type === "event" && "data" in event && event.data) {
      try {
        const raw = JSON.parse(event.data) as unknown
        const normalized = normalizeEvent(raw, sessionId, event.id ?? null, nextSyntheticId)
        if (normalized) buffer.push(normalized)
      } catch {
        // ignore malformed event
      }
    }
  }) as EventSourceParseCallback)

  const readWithAbort = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
    if (!signal) {
      return reader.read()
    }
    if (signal.aborted) {
      return { done: true, value: undefined }
    }
    return await new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
      const onAbort = () => resolve({ done: true, value: undefined })
      signal.addEventListener("abort", onAbort, { once: true })
      reader
        .read()
        .then((result) => resolve(result))
        .catch((error) => reject(error))
        .finally(() => {
          signal.removeEventListener("abort", onAbort)
        })
    })
  }

  try {
    while (true) {
      const { value, done } = await readWithAbort()
      if (done || signal?.aborted) {
        parser.reset()
        break
      }
      if (value) {
        parser.feed(decoder.decode(value, { stream: true }))
        while (buffer.length > 0) {
          const next = buffer.shift()
          if (next) {
            yield next
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
    if (response.body) {
      await response.body.cancel().catch(() => undefined)
    }
    if (signal) {
      signal.removeEventListener("abort", abortHandler as EventListener)
    }
  }
}
