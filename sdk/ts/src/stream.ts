import { createParser, type EventSourceParseCallback, type ParsedEvent, type ReconnectInterval } from "eventsource-parser"
import { ApiError, type BreadboardClientConfig } from "./client.js"
import { assertProtectedBearerTransport } from "./transport-security.js"
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
type EventStreamQuery = Readonly<
  Record<string, string | number | boolean | undefined>
  & { resume_token?: number; limit?: number; follow?: boolean }
>

export interface EventStreamOptions {
  readonly signal?: AbortSignal
  readonly query?: EventStreamQuery
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
const LIFECYCLE_PAYLOAD_SCHEMA = "bb.payload.product_session.lifecycle.v1"
const EVENT_PAYLOAD_SCHEMAS = {
  "session.started": LIFECYCLE_PAYLOAD_SCHEMA,
  "input.accepted": LIFECYCLE_PAYLOAD_SCHEMA,
  "approval.requested": LIFECYCLE_PAYLOAD_SCHEMA,
  "approval.resolved": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.reconfigured": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.paused": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.resumed": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.completed": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.failed": LIFECYCLE_PAYLOAD_SCHEMA,
  "session.canceled": LIFECYCLE_PAYLOAD_SCHEMA,
  assistant_message: "bb.payload.message.assistant.v1",
  tool_call: "bb.payload.tool.called.v1",
  tool_result: "bb.payload.tool.completed.v1",
} as const satisfies Readonly<Record<string, string>>
type EventKind = keyof typeof EVENT_PAYLOAD_SCHEMAS
type EventPayloadSchema = (typeof EVENT_PAYLOAD_SCHEMAS)[EventKind]

const eventKind = (value: string): value is EventKind =>
  Object.hasOwn(EVENT_PAYLOAD_SCHEMAS, value)

const LIFECYCLE_PAYLOAD_FIELDS: Readonly<Record<string, ReadonlySet<string>>> = {
  "session.started": new Set(["effective_lock_hash", "task_hash"]),
  "input.accepted": new Set(["content_hash", "attachments"]),
  "approval.requested": new Set(["request_id", "operation"]),
  "approval.resolved": new Set(["request_id", "decision"]),
  "session.reconfigured": new Set(["effective_lock_hash", "reason"]),
  "session.paused": new Set(["reason"]),
  "session.resumed": new Set(),
  "session.completed": new Set(["outcome", "summary"]),
  "session.failed": new Set(["outcome", "error", "detail"]),
  "session.canceled": new Set(["outcome", "reason"]),
}

const KERNEL_PAYLOAD_FIELDS: Readonly<Record<string, ReadonlySet<string>>> = {
  assistant_message: new Set(["seq", "metadata", "message", "text", "source"]),
  tool_call: new Set(["seq", "metadata", "call", "call_id", "tool", "tool_name", "state"]),
  tool_result: new Set(["seq", "metadata", "message", "tool", "success", "status", "error", "call_id", "todo"]),
}

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

const RFC3339_DATETIME_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|[+-](\d{2}):(\d{2}))$/

const isRfc3339DateTime = (value: string): boolean => {
  const match = RFC3339_DATETIME_PATTERN.exec(value)
  if (match === null) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  const offsetHour = match[7] === undefined ? 0 : Number(match[7])
  const offsetMinute = match[8] === undefined ? 0 : Number(match[8])
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ]
  const normalizedValue = second === 60
    ? `${value.slice(0, 17)}59${value.slice(19)}`
    : value
  const parsedTime = Date.parse(normalizedValue)
  const parsedDate = new Date(parsedTime)
  const validLeapSecond = second !== 60
    || (
      Number.isFinite(parsedTime)
      && (
        (parsedDate.getUTCMonth() === 5 && parsedDate.getUTCDate() === 30)
        || (parsedDate.getUTCMonth() === 11 && parsedDate.getUTCDate() === 31)
      )
      && parsedDate.getUTCHours() === 23
      && parsedDate.getUTCMinutes() === 59
    )
  return year >= 1
    && month >= 1
    && month <= 12
    && day >= 1
    && day <= daysInMonth[month - 1]
    && hour <= 23
    && minute <= 59
    && second <= 60
    && offsetHour <= 23
    && offsetMinute <= 59
    && Number.isFinite(parsedTime)
    && validLeapSecond
}

const requiredRfc3339Timestamp = (value: unknown, field: string): string => {
  const text = requiredString(value, field)
  if (!isRfc3339DateTime(text)) throw new Error(`Invalid session event ${field}`)
  return text
}

const nullableString = (value: unknown, field: string): string | null => {
  if (value === null) return null
  return requiredString(value, field)
}

const requiredBoolean = (value: unknown, field: string): boolean => {
  if (typeof value !== "boolean") throw new Error(`Invalid session event ${field}`)
  return value
}
const sha256 = (value: unknown): boolean =>
  typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value)

const validateKernelPayload = (
  kind: string,
  payload: Record<string, unknown>,
): void => {
  const fields = KERNEL_PAYLOAD_FIELDS[kind]
  if (!fields || !Object.keys(payload).every((field) => fields.has(field))) {
    throw new Error("Invalid session event payload fields")
  }
  if (
    "seq" in payload
    && (typeof payload.seq !== "number"
      || !Number.isSafeInteger(payload.seq)
      || payload.seq < 0)
  ) {
    throw new Error("Invalid session event payload seq")
  }
  if ("metadata" in payload && !record(payload.metadata)) {
    throw new Error("Invalid session event payload metadata")
  }
  const textFields = kind === "assistant_message"
    ? ["text", "source"]
    : kind === "tool_call"
      ? ["call_id", "tool", "tool_name", "state"]
      : ["tool", "status", "call_id"]
  for (const field of textFields) {
    if (field in payload && typeof payload[field] !== "string") {
      throw new Error(`Invalid session event payload ${field}`)
    }
  }
  if (kind === "tool_call" && "call" in payload && !record(payload.call)) {
    throw new Error("Invalid session event payload call")
  }
  if (kind === "tool_result" && "success" in payload && typeof payload.success !== "boolean") {
    throw new Error("Invalid session event payload success")
  }
}

const validateAttachments = (value: unknown): boolean =>
  Array.isArray(value) && value.every((attachment) =>
    record(attachment)
    && hasExactFields(attachment, new Set(["digest", "size_bytes", "media_type"]))
    && sha256(attachment.digest)
    && typeof attachment.size_bytes === "number"
    && Number.isSafeInteger(attachment.size_bytes)
    && attachment.size_bytes >= 0
    && typeof attachment.media_type === "string"
    && attachment.media_type.length > 0)

const validateLifecyclePayload = (
  kind: string,
  payload: Record<string, unknown>,
): void => {
  const fields = LIFECYCLE_PAYLOAD_FIELDS[kind]
  if (!fields || !hasExactFields(payload, fields)) {
    throw new Error("Invalid session event lifecycle payload fields")
  }
  let valid = false
  switch (kind) {
    case "session.started":
      valid = sha256(payload.effective_lock_hash) && sha256(payload.task_hash)
      break
    case "input.accepted":
      valid = sha256(payload.content_hash) && validateAttachments(payload.attachments)
      break
    case "approval.requested":
      valid = typeof payload.request_id === "string" && payload.request_id.length > 0
        && typeof payload.operation === "string" && payload.operation.length > 0
      break
    case "approval.resolved":
      valid = typeof payload.request_id === "string" && payload.request_id.length > 0
        && typeof payload.decision === "string"
        && ["allow", "deny", "once", "always", "reject"].includes(payload.decision)
      break
    case "session.reconfigured":
      valid = sha256(payload.effective_lock_hash) && typeof payload.reason === "string"
      break
    case "session.paused":
      valid = typeof payload.reason === "string"
      break
    case "session.resumed":
      valid = true
      break
    case "session.completed":
      valid = payload.outcome === "completed" && typeof payload.summary === "string"
      break
    case "session.failed":
      valid = payload.outcome === "failed"
        && typeof payload.error === "string" && payload.error.length > 0
        && typeof payload.detail === "string" && payload.detail.length > 0
      break
    case "session.canceled":
      valid = payload.outcome === "canceled" && typeof payload.reason === "string"
      break
  }
  if (!valid) throw new Error("Invalid session event lifecycle payload")
}

const validateEventPayload = (
  kind: string,
  schemaVersion: string,
  payload: Record<string, unknown>,
): { kind: EventKind; schemaVersion: EventPayloadSchema } => {
  if (!eventKind(kind)) throw new Error("Invalid session event kind")
  const expectedSchema = EVENT_PAYLOAD_SCHEMAS[kind]
  if (schemaVersion !== expectedSchema) {
    throw new Error("Invalid session event payload_schema_version")
  }
  if (kind in LIFECYCLE_PAYLOAD_FIELDS) validateLifecyclePayload(kind, payload)
  else validateKernelPayload(kind, payload)
  return { kind, schemaVersion: expectedSchema }
}

const decodeSessionEvent = (
  raw: unknown,
  expectedSessionId: string,
  sseId: string | undefined,
): SessionEvent => {
  if (!record(raw)) throw new Error("Invalid session event envelope")
  if (!hasExactFields(raw, SESSION_EVENT_FIELDS)) throw new Error("Invalid session event fields")
  if (raw.schema_version !== "bb.public_session_event.v1") {
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
  const kind = requiredString(raw.kind, "kind")
  const payloadSchemaVersion = requiredString(
    raw.payload_schema_version,
    "payload_schema_version",
  )
  const validatedPayload = validateEventPayload(kind, payloadSchemaVersion, raw.payload)
  const redactionState = raw.visibility.redaction_state
  if (redactionState !== "none" && redactionState !== "redacted") {
    throw new Error("Invalid session event visibility.redaction_state")
  }

  return {
    schema_version: "bb.public_session_event.v1",
    event_id: eventId,
    seq: sequence,
    timestamp: requiredRfc3339Timestamp(raw.timestamp, "timestamp"),
    work_item_id: nullableString(raw.work_item_id, "work_item_id"),
    parent_work_item_id: nullableString(raw.parent_work_item_id, "parent_work_item_id"),
    attempt_id: nullableString(raw.attempt_id, "attempt_id"),
    session_id: sessionId,
    span_id: nullableString(raw.span_id, "span_id"),
    visibility: {
      model_visible: requiredBoolean(raw.visibility.model_visible, "visibility.model_visible"),
      provider_visible: requiredBoolean(raw.visibility.provider_visible, "visibility.provider_visible"),
      host_visible: requiredBoolean(raw.visibility.host_visible, "visibility.host_visible"),
      redaction_state: redactionState,
    },
    kind: validatedPayload.kind,
    payload: raw.payload,
    payload_schema_version: validatedPayload.schemaVersion,
  }
}

const sessionEventsBinding = PUBLIC_BINDINGS_BY_OPERATION_ID["session.events"]

const streamUrl = (
  sessionId: string,
  config: StreamConfig,
  query: EventStreamQuery,
): URL => {
  const url = new URL(
    bindGeneratedRoute(sessionEventsBinding, { session_id: encodeURIComponent(sessionId) }).replace(/^\/+/, ""),
    config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`,
  )
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  return url
}

const resolveToken = async (config: StreamConfig, signal?: AbortSignal): Promise<string | undefined> => {
  if (signal?.aborted) return undefined
  let token: string | undefined
  try {
    token = typeof config.authToken === "function" ? await config.authToken() : config.authToken
  } catch (error) {
    if (signal?.aborted) return undefined
    throw error
  }
  if (signal?.aborted) return undefined
  if (token) assertProtectedBearerTransport(config.baseUrl)
  return token
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
    const response = await (options.config.fetch ?? globalThis.fetch)(streamUrl(sessionId, options.config, options.query ?? {}), {
      method: sessionEventsBinding.httpMethod,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
      },
      signal: controller.signal,
    })
    if (!response.ok) {
      const contentType = response.headers.get("content-type") ?? ""
      const body = contentType.includes("application/json")
        ? await response.json().catch(() => undefined)
        : await response.text().catch(() => undefined)
      throw new ApiError(
        `Streaming request failed with status ${response.status}`,
        response.status,
        body,
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
  let resumeToken = options.query?.resume_token
  let terminal = false
  const markClosed = (): void => {
    if (closed) return
    closed = true
    clearTimeout(timer)
    timer = undefined
    options.signal?.removeEventListener("abort", close)
  }


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
        query: {
          ...options.query,
          ...(resumeToken === undefined ? {} : { resume_token: resumeToken }),
        },
        lastEventId,
        signal: attemptController.signal,
        onOpen: () => {
          retry = options.initialRetryMs ?? 500
          handlers.onOpen?.()
        },
      })) {
        if (closed) return
        lastEventId = resumeCursor(event)
        resumeToken = event.seq
        terminal =
          event.kind === "session.completed"
          || event.kind === "session.failed"
          || event.kind === "session.canceled"
        if (terminal) markClosed()
        handlers.onEvent(event)
        if (terminal) return
      }
    } catch (error) {
      if ((!closed || terminal) && !attemptController.signal.aborted) {
        handlers.onError?.(error instanceof Error ? error : new Error("Event stream failed"))
      }
    } finally {
      if (controller === attemptController) controller = undefined
      if (terminal || options.query?.follow === false) markClosed()
    }
    if (!terminal && options.query?.follow !== false) scheduleReconnect()
  }

  const close = (): void => {
    markClosed()
    controller?.abort()
    controller = undefined
  }

  options.signal?.addEventListener("abort", close, { once: true })
  if (!options.signal?.aborted) void connect()
  else close()
  return { close }
}
