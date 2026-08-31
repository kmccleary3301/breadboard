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

const SESSION_EVENT_FIELDS = new Set([
  "schema_version",
  "event_id",
  "seq",
  "timestamp",
  "work_item_id",
  "parent_work_item_id",
  "attempt_id",
  "session_id",
  "span_id",
  "visibility",
  "kind",
  "payload",
  "payload_schema_version",
])

const SESSION_EVENT_VISIBILITY_FIELDS = new Set([
  "model_visible",
  "provider_visible",
  "host_visible",
  "redaction_state",
])

const hasExactFields = (
  value: Record<string, unknown>,
  fields: ReadonlySet<string>,
): boolean => {
  const keys = Object.keys(value)
  return keys.length === fields.size && keys.every((key) => fields.has(key))
}


const requiredString = (value: unknown, field: string): string => {
  if (typeof value !== "string" || value.length === 0) throw new Error(`Invalid session event ${field}`)
  return value
}

const nullableString = (value: unknown, field: string): string | null => {
  if (value === null) return null
  return requiredString(value, field)
}

const requiredBoolean = (value: unknown, field: string): boolean => {
  if (typeof value !== "boolean") throw new Error(`Invalid session event ${field}`)
  return value
}

const decodeSessionEvent = (
  raw: unknown,
  expectedSessionId: string,
  sseId: string | undefined,
): SessionEvent => {
  if (!record(raw)) throw new Error("Invalid session event envelope")
  if (!hasExactFields(raw, SESSION_EVENT_FIELDS)) throw new Error("Invalid session event fields")
  if (raw.schema_version !== "bb.kernel_event.v2") {
    throw new Error("Invalid session event schema_version")
  }
  const eventId = requiredString(raw.event_id, "event_id")
  const sequence = raw.seq
  if (typeof sequence !== "number" || !Number.isSafeInteger(sequence) || sequence < 0) {
    throw new Error("Invalid session event seq")
  }
  if (sseId === undefined) throw new Error("Session event is missing an SSE id")
  if (sseId !== String(sequence)) throw new Error("Session event SSE id does not match seq")
  const sessionId = requiredString(raw.session_id, "session_id")
  if (sessionId !== expectedSessionId) throw new Error("Session event belongs to another session")
  if (!record(raw.visibility)) throw new Error("Invalid session event visibility")
  if (!hasExactFields(raw.visibility, SESSION_EVENT_VISIBILITY_FIELDS)) {
    throw new Error("Invalid session event visibility fields")
  }
  if (!record(raw.payload)) throw new Error("Invalid session event payload")

  return {
    schema_version: "bb.kernel_event.v2",
    event_id: eventId,
    seq: sequence,
    timestamp: requiredString(raw.timestamp, "timestamp"),
    work_item_id: nullableString(raw.work_item_id, "work_item_id"),
    parent_work_item_id: nullableString(raw.parent_work_item_id, "parent_work_item_id"),
    attempt_id: nullableString(raw.attempt_id, "attempt_id"),
    session_id: sessionId,
    span_id: nullableString(raw.span_id, "span_id"),
    visibility: {
      model_visible: requiredBoolean(raw.visibility.model_visible, "visibility.model_visible"),
      provider_visible: requiredBoolean(raw.visibility.provider_visible, "visibility.provider_visible"),
      host_visible: requiredBoolean(raw.visibility.host_visible, "visibility.host_visible"),
      redaction_state: requiredString(raw.visibility.redaction_state, "visibility.redaction_state"),
    },
    kind: requiredString(raw.kind, "kind"),
    payload: raw.payload,
    payload_schema_version: requiredString(raw.payload_schema_version, "payload_schema_version"),
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
    const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
      if (event.type !== "event" || !("data" in event) || !event.data) return
      buffer.push(decodeSessionEvent(JSON.parse(event.data), sessionId, event.id))
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

const resumeCursor = (event: SessionEvent): string => String(event.seq)

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
