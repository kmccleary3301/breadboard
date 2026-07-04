import { createParser, ParsedEvent, ReconnectInterval, EventSourceParseCallback } from "eventsource-parser"
import { loadAppConfig } from "../config/appConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import type { SessionEvent } from "./types.js"
import { ApiError } from "./client.js"

export interface StreamConfig {
  readonly baseUrl: string
  readonly authToken?: string | (() => Promise<string | undefined>)
  readonly streamSchema?: number | null
  readonly streamIncludeLegacy?: boolean | null
}

export interface EventStreamOptions {
  readonly signal?: AbortSignal
  readonly query?: Record<string, string | number | boolean>
  readonly config?: StreamConfig
  readonly lastEventId?: string
}

export interface EventStreamHandlers {
  readonly onEvent: (event: SessionEvent) => void
  readonly onOpen?: () => void
  readonly onError?: (error: Event | Error) => void
}

export interface OpenEventStreamOptions extends EventStreamOptions {
  readonly eventTypes?: readonly string[]
  readonly initialRetryMs?: number
  readonly maxRetryMs?: number
}

export interface EventStreamHandle {
  close(): void
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const resolveStreamAuthToken = async (config: StreamConfig): Promise<string | undefined> =>
  typeof config.authToken === "function"
    ? await config.authToken()
    : config.authToken ?? (await resolveAuthToken(config.baseUrl))

const STREAM_ABORTED = Symbol("stream-aborted")

const resolveStreamAuthTokenWithAbort = async (
  config: StreamConfig,
  signal: AbortSignal | undefined,
): Promise<string | undefined | typeof STREAM_ABORTED> => {
  if (!signal) return await resolveStreamAuthToken(config)
  if (signal.aborted) return STREAM_ABORTED

  return await new Promise<string | undefined | typeof STREAM_ABORTED>((resolve, reject) => {
    const onAbort = () => resolve(STREAM_ABORTED)
    signal.addEventListener("abort", onAbort, { once: true })
    Promise.resolve()
      .then(() => resolveStreamAuthToken(config))
      .then(resolve, reject)
      .finally(() => {
        signal.removeEventListener("abort", onAbort)
      })
  })
}

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

export const openEventStream = (
  sessionId: string,
  handlers: EventStreamHandlers,
  options: OpenEventStreamOptions = {},
): EventStreamHandle => {
  const EventSourceCtor = globalThis.EventSource
  if (typeof EventSourceCtor !== "function") {
    throw new Error("EventSource is not available in this runtime")
  }
  const config = options.config ?? loadAppConfig()
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

  let closed = false
  let retryMs = options.initialRetryMs ?? 500
  const maxRetryMs = options.maxRetryMs ?? 10_000
  let source: EventSource | undefined
  let reconnectTimer: NodeJS.Timeout | number | undefined
  let syntheticIdCounter = 0
  const nextSyntheticId = () => `synthetic-${syntheticIdCounter++}`

  const buildEventSourceUrl = (): URL => {
    const url = new URL(`v1/sessions/${sessionId}/events`, config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
    for (const [key, value] of Object.entries(query)) {
      url.searchParams.set(key, String(value))
    }
    if (options.lastEventId) {
      url.searchParams.set("from_id", options.lastEventId)
    }
    return url
  }

  const closeSource = () => {
    if (source) {
      source.close()
      source = undefined
    }
  }

  const scheduleReconnect = (error: Event | Error) => {
    handlers.onError?.(error)
    closeSource()
    if (closed) return
    const delay = retryMs
    retryMs = Math.min(maxRetryMs, Math.max(delay * 2, delay + 1))
    reconnectTimer = setTimeout(connect, delay)
  }

  const handleMessage = (event: MessageEvent<string>) => {
    try {
      const raw = JSON.parse(event.data) as unknown
      const normalized = normalizeEvent(raw, sessionId, event.lastEventId || null, nextSyntheticId)
      if (normalized) handlers.onEvent(normalized)
    } catch (error) {
      handlers.onError?.(error instanceof Error ? error : new Error("Malformed SSE event"))
    }
  }

  function connect() {
    if (closed) return
    closeSource()
    source = new EventSourceCtor(buildEventSourceUrl())
    source.onopen = () => {
      retryMs = options.initialRetryMs ?? 500
      handlers.onOpen?.()
    }
    source.onmessage = handleMessage
    source.onerror = (event) => scheduleReconnect(event)
    for (const eventType of options.eventTypes ?? []) {
      source.addEventListener(eventType, handleMessage as EventListener)
    }
  }

  const abortHandler = () => {
    closed = true
    clearTimeout(reconnectTimer)
    closeSource()
  }
  options.signal?.addEventListener("abort", abortHandler, { once: true })
  if (options.signal?.aborted) {
    abortHandler()
  } else {
    connect()
  }

  return {
    close() {
      closed = true
      clearTimeout(reconnectTimer)
      options.signal?.removeEventListener("abort", abortHandler)
      closeSource()
    },
  }
}

export const streamSessionEvents = async function* (
  sessionId: string,
  options: EventStreamOptions = {},
): AsyncGenerator<SessionEvent, void, void> {
  const config = options.config ?? loadAppConfig()
  const url = new URL(`v1/sessions/${sessionId}/events`, config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
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
  if (signal?.aborted) {
    return
  }
  const abortHandler = signal ? () => controller.abort() : undefined
  let response: Response | undefined
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  if (signal) {
    signal.addEventListener("abort", abortHandler as EventListener, { once: true })
  }
  try {
    const authToken = await resolveStreamAuthTokenWithAbort(config, signal)
    if (authToken === STREAM_ABORTED || signal?.aborted) {
      return
    }
    const headers: Record<string, string> = {
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
    }
    try {
      response = await fetch(url, {
        method: "GET",
        headers,
        signal: controller.signal,
      })
    } catch (error) {
      if (signal?.aborted || controller.signal.aborted) {
        return
      }
      throw error
    }
    if (!response.ok) {
      const text = await response.text().catch(() => "")
      throw new ApiError(`Streaming request failed with status ${response.status}`, response.status, text)
    }
    if (!response.body) {
      throw new Error("Streaming response provided no body")
    }

    reader = response.body.getReader()
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

    const streamReader = reader
    const readWithAbort = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
      if (signal?.aborted || controller.signal.aborted) {
        return { done: true, value: undefined }
      }
      try {
        return await streamReader.read()
      } catch (error) {
        if (signal?.aborted || controller.signal.aborted) {
          return { done: true, value: undefined }
        }
        throw error
      }
    }

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
    if (reader) {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    } else if (response?.body) {
      await response.body.cancel().catch(() => undefined)
    }
    if (signal) {
      signal.removeEventListener("abort", abortHandler as EventListener)
    }
  }
}
