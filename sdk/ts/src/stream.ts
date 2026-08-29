import { createParser, type EventSourceParseCallback, type ParsedEvent, type ReconnectInterval } from "eventsource-parser"
import { ApiError, type BreadboardClientConfig } from "./client.js"
import type { SessionEvent } from "./types.js"
import {
  PUBLIC_BINDINGS_BY_OPERATION_ID,
  type PublicOperationBinding,
} from "./generated/public-bindings.js"

export const bindGeneratedRoute = (
  binding: PublicOperationBinding,
  pathValues: Readonly<Record<string, string>> = {},
): string => {
  const placeholders: string[] = []
  for (const match of binding.path.matchAll(/\{([^{}]+)\}/g)) {
    const name = match[1]
    if (!name) throw new Error(`Invalid path placeholder in ${binding.operationId}`)
    placeholders.push(name)
  }
  for (const name of Object.keys(pathValues)) {
    if (!placeholders.includes(name)) throw new Error(`Unknown path parameter ${name} for ${binding.operationId}`)
  }
  let route = binding.path
  for (const name of placeholders) {
    const value = pathValues[name]
    if (value === undefined) throw new Error(`Missing path parameter ${name} for ${binding.operationId}`)
    route = route.replace(`{${name}}`, value)
  }
  if (/\{[^{}]+\}/.test(route)) throw new Error(`Unresolved path parameter for ${binding.operationId}`)
  return route
}
export interface StreamConfig extends BreadboardClientConfig {}

export interface EventStreamOptions {
  readonly signal?: AbortSignal
  readonly query?: Record<string, string | number | boolean>
  readonly config: StreamConfig
  readonly lastEventId?: string
  readonly onOpen?: () => void
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

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const stringValue = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value : null

const numberValue = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

const normalize = (
  raw: unknown,
  sessionId: string,
  fallbackId: string | null,
  nextId: () => string,
): SessionEvent | null => {
  if (!record(raw)) return null
  const type = stringValue(raw.type) ?? stringValue(raw.event) ?? stringValue(raw.kind)
  if (!type) return null
  const timestamp = numberValue(raw.timestamp) ?? numberValue(raw.time) ?? numberValue(raw.ts) ?? Date.now()
  const timestampMs =
    numberValue(raw.timestamp_ms) ??
    numberValue(raw.timestampMs) ??
    numberValue(raw.ts_ms) ??
    (timestamp > 10_000_000_000 ? timestamp : Math.round(timestamp * 1000))
  return {
    id:
      stringValue(raw.id) ??
      stringValue(raw.event_id) ??
      stringValue(raw.eventId) ??
      fallbackId ??
      nextId(),
    type: type as SessionEvent["type"],
    session_id: stringValue(raw.session_id) ?? stringValue(raw.sessionId) ?? sessionId,
    turn: numberValue(raw.turn) ?? numberValue(raw.turn_id) ?? numberValue(raw.turnId),
    timestamp: timestampMs,
    timestamp_ms: timestampMs,
    seq: numberValue(raw.seq) ?? undefined,
    v: numberValue(raw.v) ?? undefined,
    schema_rev: stringValue(raw.schema_rev) ?? stringValue(raw.schemaRev),
    run_id: stringValue(raw.run_id) ?? stringValue(raw.runId),
    thread_id: stringValue(raw.thread_id) ?? stringValue(raw.threadId),
    turn_id:
      stringValue(raw.turn_id) ??
      stringValue(raw.turnId) ??
      (typeof raw.turn_id === "number" ? raw.turn_id : undefined) ??
      (typeof raw.turnId === "number" ? raw.turnId : undefined),
    span_id: stringValue(raw.span_id) ?? stringValue(raw.spanId),
    parent_span_id: stringValue(raw.parent_span_id) ?? stringValue(raw.parentSpanId),
    actor: record(raw.actor) ? raw.actor : null,
    visibility: stringValue(raw.visibility),
    tags: Array.isArray(raw.tags)
      ? raw.tags.filter((tag): tag is string => typeof tag === "string")
      : null,
    payload: (raw.payload ?? raw.data ?? raw.message ?? raw.body ?? {}) as SessionEvent["payload"],
  }
}

const sessionEventsBinding = PUBLIC_BINDINGS_BY_OPERATION_ID["session.events"]

const streamUrl = (
  sessionId: string,
  config: StreamConfig,
  query: Record<string, string | number | boolean>,
  lastEventId?: string,
): URL => {
  const url = new URL(
    bindGeneratedRoute(sessionEventsBinding, { session_id: encodeURIComponent(sessionId) }).replace(/^\/+/, ""),
    config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`,
  )
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

export const streamSessionEvents = async function* (
  sessionId: string,
  options: EventStreamOptions,
): AsyncGenerator<SessionEvent, void, void> {
  if (options.signal?.aborted) return
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener("abort", abort, { once: true })
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const token = await resolveToken(options.config, options.signal)
    if (options.signal?.aborted) return
    const response = await (options.config.fetch ?? globalThis.fetch)(streamUrl(sessionId, options.config, { ...(options.query ?? {}) }, options.lastEventId), {
      method: sessionEventsBinding.httpMethod,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
      },
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new ApiError(
        `Streaming request failed with status ${response.status}`,
        response.status,
        await response.text().catch(() => ""),
      )
    }
    if (!response.body) throw new Error("Streaming response provided no body")
    options.onOpen?.()

    reader = response.body.getReader()
    const decoder = new TextDecoder()
    const buffer: SessionEvent[] = []
    let counter = 0
    const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
      if (event.type !== "event" || !("data" in event) || !event.data) return
      try {
        const parsed = normalize(
          JSON.parse(event.data),
          sessionId,
          event.id ?? null,
          () => `synthetic-${counter++}`,
        )
        if (parsed) buffer.push(parsed)
      } catch {
        // Malformed events are ignored; the next canonical event remains usable.
      }
    }) as EventSourceParseCallback)

    while (!options.signal?.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      if (!value) continue
      parser.feed(decoder.decode(value, { stream: true }))
      while (buffer.length > 0) {
        const event = buffer.shift()
        if (event) yield event
      }
    }
    parser.reset()
  } finally {
    if (reader) {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    }
    options.signal?.removeEventListener("abort", abort)
  }
}

const resumeCursor = (event: SessionEvent): string | undefined => {
  if (event.seq !== undefined && Number.isSafeInteger(event.seq) && event.seq >= 0) {
    return String(event.seq)
  }
  return /^\d+$/.test(event.id) ? event.id : undefined
}

export const openEventStream = (
  sessionId: string,
  handlers: EventStreamHandlers,
  options: OpenEventStreamOptions,
): EventStreamHandle => {
  let closed = false
  let controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let retry = options.initialRetryMs ?? 500
  const maxRetry = options.maxRetryMs ?? 10_000
  let lastEventId = options.lastEventId

  const scheduleReconnect = (): void => {
    if (closed || options.signal?.aborted) return
    timer = setTimeout(() => void connect(), retry)
    retry = Math.min(maxRetry, Math.max(retry * 2, retry + 1))
  }

  const connect = async (): Promise<void> => {
    if (closed || options.signal?.aborted) return
    const attemptController = new AbortController()
    controller = attemptController
    try {
      for await (const event of streamSessionEvents(sessionId, {
        config: options.config,
        query: options.query,
        lastEventId,
        signal: attemptController.signal,
        onOpen: () => {
          retry = options.initialRetryMs ?? 500
          handlers.onOpen?.()
        },
      })) {
        if (closed) return
        lastEventId = resumeCursor(event) ?? lastEventId
        handlers.onEvent(event)
      }
    } catch (error) {
      if (!closed && !attemptController.signal.aborted) {
        handlers.onError?.(error instanceof Error ? error : new Error("Event stream failed"))
      }
    } finally {
      if (controller === attemptController) controller = undefined
    }
    scheduleReconnect()
  }

  const close = (): void => {
    closed = true
    clearTimeout(timer)
    timer = undefined
    controller?.abort()
    controller = undefined
    options.signal?.removeEventListener("abort", close)
  }

  options.signal?.addEventListener("abort", close, { once: true })
  if (!options.signal?.aborted) void connect()
  else close()
  return { close }
}
