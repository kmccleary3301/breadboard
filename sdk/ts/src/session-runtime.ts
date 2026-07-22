
declare const sessionIdBrand: unique symbol
declare const inputIdBrand: unique symbol
declare const turnIdBrand: unique symbol
declare const cancellationRequestIdBrand: unique symbol
declare const cancellationRequestKeyBrand: unique symbol
declare const clientMessageIdBrand: unique symbol
declare const eventIdBrand: unique symbol
declare const replayDigestBrand: unique symbol
declare const toolCallIdBrand: unique symbol
declare const permissionRequestIdBrand: unique symbol
declare const taskIdBrand: unique symbol

export type SessionId = string & { readonly [sessionIdBrand]: true }
export type InputId = string & { readonly [inputIdBrand]: true }
export type TurnId = string & { readonly [turnIdBrand]: true }
export type CancellationRequestId = string & { readonly [cancellationRequestIdBrand]: true }
export type CancellationRequestKey = string & { readonly [cancellationRequestKeyBrand]: true }
export type ClientMessageId = string & { readonly [clientMessageIdBrand]: true }
export type EventId = string & { readonly [eventIdBrand]: true }
export type ReplayContractDigest = string & { readonly [replayDigestBrand]: true }
export type ToolCallId = string & { readonly [toolCallIdBrand]: true }
export type PermissionRequestId = string & { readonly [permissionRequestIdBrand]: true }
export type TaskId = string & { readonly [taskIdBrand]: true }

export const REDACTED_VALUE = "[redacted]" as const
export const SESSION_REPLAY_SCHEMA_VERSION = "bb.cli_bridge.session_replay.v1" as const
export const REPLAY_RETENTION_MAX_EVENTS = 1000 as const
export const REPLAY_RETENTION_MAX_AGE_MS = 86_400_000 as const
export const replayConfigurationDigest =
  "sha256:a107aea87bdc7075d68495d3c0bf2b68e85e38a2b2fef1000bf3f1eaee77f743" as ReplayContractDigest

export type SessionStatus = "starting" | "running" | "completed" | "failed" | "stopped"
export type TurnAdmission = "idle" | "active"
export type RetainedHistory = "complete" | "partial"
export type SubmitDisposition = "started" | "queued" | "deduplicated"
export type OriginalSubmitDisposition = "started" | "queued"
export type CancellationReason = "user_requested" | "timeout" | "superseded"
export type CancellationDisposition = "cancellation_requested" | "queued_cancelled" | "deduplicated"
export type OriginalCancellationDisposition = "cancellation_requested" | "queued_cancelled"
export type TerminalOutcome = "completed" | "failed" | "cancelled"

export interface ReplayRetention {
  readonly maxEvents: typeof REPLAY_RETENTION_MAX_EVENTS
  readonly maxAgeMs: typeof REPLAY_RETENTION_MAX_AGE_MS
  readonly configurationDigest: ReplayContractDigest
}

export interface SessionReplayFacts {
  readonly replayRetention: ReplayRetention
  readonly earliestRetainedSequence: number | null
  readonly earliestRetainedEventId: EventId | null
  readonly headSequence: number
  readonly headEventId: EventId | null
  readonly retainedHistory: RetainedHistory
  readonly sessionReplayContractDigest: ReplayContractDigest
}

export interface TerminalTurnSnapshot {
  readonly inputId: InputId
  readonly turnId: TurnId
  readonly outcome: TerminalOutcome
  readonly originalDisposition: OriginalSubmitDisposition
}

export interface SessionSnapshot extends SessionReplayFacts {
  readonly sessionId: SessionId
  readonly status: SessionStatus
  readonly createdAt: string
  readonly lastActivityAt: string
  readonly model: string | null
  readonly mode: string | null
  readonly turnAdmission: TurnAdmission
  readonly activeTurnId: TurnId | null
  readonly queuedTurnCount: number
  readonly terminalTurns: readonly TerminalTurnSnapshot[]
}

export interface CreateSessionRequest {
  readonly configPath: string
  readonly task?: string
  readonly overrides?: Readonly<{ [key: string]: unknown }>
  readonly metadata?: Readonly<{ [key: string]: unknown }>
  readonly workspace?: string
  readonly maxSteps?: number
  readonly permissionMode?: string
  readonly stream?: boolean
}

export interface AttachSessionRequest {
  readonly sessionId: SessionId | string
}

export interface AttachmentHandleInput {
  readonly kind: "handle"
  readonly id: string
}

export interface AttachmentUploadInput {
  readonly kind: "upload"
  readonly filename: string
  readonly data: Blob
}

export type AttachmentInput = string | AttachmentHandleInput | AttachmentUploadInput

export interface StructuredSubmit {
  readonly text: string
  readonly attachments?: readonly AttachmentInput[]
  readonly clientMessageId?: ClientMessageId | string
}

export type SubmitInput = string | StructuredSubmit

export interface SubmitReceipt {
  readonly clientMessageId: ClientMessageId
  readonly inputId: InputId
  readonly turnId: TurnId
  readonly disposition: SubmitDisposition
  readonly originalDisposition: OriginalSubmitDisposition
}

export interface CancelTurnRequest {
  readonly turnId: TurnId | string
  readonly cancellationRequestKey?: CancellationRequestKey | string
  readonly reason?: CancellationReason
}

export interface CancellationReceipt {
  readonly cancellationRequestId: CancellationRequestId
  readonly cancellationRequestKey: CancellationRequestKey
  readonly inputId: InputId
  readonly turnId: TurnId
  readonly disposition: CancellationDisposition
  readonly originalDisposition: OriginalCancellationDisposition
}
export type PermissionDecision = "allow" | "deny"

export interface RespondPermissionRequest {
  readonly requestId: PermissionRequestId | string
  readonly decision: PermissionDecision
}

export interface PermissionDecisionReceipt {
  readonly requestId: PermissionRequestId
  readonly decision: PermissionDecision
}


export interface ObserveSessionRequest {
  readonly signal?: AbortSignal
}

export class ExactEmptyPayload {
  static readonly value: ExactEmptyPayload = new ExactEmptyPayload()
  static { Object.freeze(ExactEmptyPayload.value) }
  private constructor() {}
  readonly #brand = undefined
}

export interface RedactedTurnError {
  readonly code: string
  readonly message: typeof REDACTED_VALUE
}

export type CanonicalJsonPrimitive = string | number | boolean | null
export type CanonicalJsonValue = CanonicalJsonPrimitive | readonly CanonicalJsonValue[] | CanonicalJsonObject
export type CanonicalJsonObject = { readonly [key: string]: CanonicalJsonValue }

interface LoggedEventBase<TKind extends string, TPayload> {
  readonly kind: TKind
  readonly eventId: EventId
  readonly sequence: number
  readonly sessionId: SessionId
  readonly inputId: InputId | null
  readonly turnId: TurnId | null
  readonly occurredAtMs: number
  readonly payload: TPayload
}
type TurnOwnedLoggedEvent<TKind extends string, TPayload> =
  LoggedEventBase<TKind, TPayload> & { readonly inputId: InputId; readonly turnId: TurnId }


export type InputObservedEvent = TurnOwnedLoggedEvent<"input_observed", { readonly text: string }>
export type TurnStartedEvent = TurnOwnedLoggedEvent<"turn_started", ExactEmptyPayload>
export type AssistantTextDeltaEvent = TurnOwnedLoggedEvent<"assistant_text_delta", { readonly text: string }>
export type AssistantTextCompletedEvent = TurnOwnedLoggedEvent<"assistant_text_completed", { readonly text: string | null }>
export type TurnCompletedEvent = TurnOwnedLoggedEvent<"turn_completed", ExactEmptyPayload>
export type TurnFailedEvent = TurnOwnedLoggedEvent<"turn_failed", { readonly error: RedactedTurnError }>
export type TurnCancelledEvent = TurnOwnedLoggedEvent<"turn_cancelled", { readonly reason: CancellationReason }>
export interface AssistantMessageStartedPayload {
  readonly messageId: string | null
  readonly toolCallCount: number
}

export interface ToolCalledPayload {
  readonly callId: ToolCallId
  readonly tool: string
  readonly arguments: CanonicalJsonValue | null
  readonly action: string | null
  readonly diffPreview: CanonicalJsonValue | null
  readonly progress: CanonicalJsonValue | null
}

export interface ToolResultObservedPayload {
  readonly callId: ToolCallId
  readonly tool: string | null
  readonly status: string
  readonly error: boolean
  readonly result: CanonicalJsonValue | null
  readonly artifactRef: CanonicalJsonValue | null
}

export interface PermissionRequestedPayload {
  readonly requestId: PermissionRequestId
  readonly tool: string
  readonly kind: string
  readonly summary: string | null
  readonly defaultScope: string | null
  readonly rewindable: boolean
}

export interface PermissionRespondedPayload {
  readonly requestId: PermissionRequestId
  readonly decision: string
}

export interface TaskEventObservedPayload {
  readonly taskId: TaskId
  readonly kind: string
  readonly status: string | null
  readonly description: string | null
  readonly parentTaskId: TaskId | null
  readonly childSessionId: SessionId | null
  readonly parentSessionId: SessionId | null
  readonly laneId: string | null
  readonly laneLabel: string | null
}

export type ConversationCompactionStartedEvent = TurnOwnedLoggedEvent<"conversation_compaction_started", CanonicalJsonObject>
export type ConversationCompactionCompletedEvent = TurnOwnedLoggedEvent<"conversation_compaction_completed", CanonicalJsonObject>
export type AssistantMessageStartedEvent = TurnOwnedLoggedEvent<"assistant_message_started", AssistantMessageStartedPayload>
export type AssistantReasoningDeltaEvent = TurnOwnedLoggedEvent<"assistant_reasoning_delta", { readonly text: string }>
export type AssistantThoughtSummaryDeltaEvent = TurnOwnedLoggedEvent<"assistant_thought_summary_delta", { readonly text: string }>
export type ToolExecutionStartedEvent = TurnOwnedLoggedEvent<"tool_execution_started", CanonicalJsonObject>
export type ToolExecutionStdoutDeltaEvent = TurnOwnedLoggedEvent<"tool_execution_stdout_delta", CanonicalJsonObject>
export type ToolExecutionStderrDeltaEvent = TurnOwnedLoggedEvent<"tool_execution_stderr_delta", CanonicalJsonObject>
export type ToolExecutionCompletedEvent = TurnOwnedLoggedEvent<"tool_execution_completed", CanonicalJsonObject>
export type ToolCalledEvent = TurnOwnedLoggedEvent<"tool_called", ToolCalledPayload>
export type ToolResultObservedEvent = TurnOwnedLoggedEvent<"tool_result_observed", ToolResultObservedPayload>
export type TodoUpdatedEvent = LoggedEventBase<"todo_updated", CanonicalJsonObject>
export type PermissionRequestedEvent = TurnOwnedLoggedEvent<"permission_requested", PermissionRequestedPayload>
export type PermissionRespondedEvent = TurnOwnedLoggedEvent<"permission_responded", PermissionRespondedPayload>
export type CheckpointListObservedEvent = LoggedEventBase<"checkpoint_list_observed", CanonicalJsonObject>
export type CheckpointRestoredEvent = LoggedEventBase<"checkpoint_restored", CanonicalJsonObject>
export type SkillsCatalogObservedEvent = LoggedEventBase<"skills_catalog_observed", CanonicalJsonObject>
export type SkillsSelectionObservedEvent = LoggedEventBase<"skills_selection_observed", CanonicalJsonObject>
export type CTreeNodeObservedEvent = TurnOwnedLoggedEvent<"ctree_node_observed", CanonicalJsonObject>
export type CTreeSnapshotObservedEvent = LoggedEventBase<"ctree_snapshot_observed", CanonicalJsonObject>
export type TaskEventObservedEvent = TurnOwnedLoggedEvent<"task_event_observed", TaskEventObservedPayload>
export type WarningObservedEvent = TurnOwnedLoggedEvent<"warning_observed", CanonicalJsonObject>
export type RewardUpdatedEvent = TurnOwnedLoggedEvent<"reward_updated", CanonicalJsonObject>
export type LimitsUpdatedEvent = TurnOwnedLoggedEvent<"limits_updated", CanonicalJsonObject>
export type CompletionObservedEvent = TurnOwnedLoggedEvent<"completion_observed", CanonicalJsonObject>
export type LogLinkedEvent = TurnOwnedLoggedEvent<"log_linked", CanonicalJsonObject>
export type RuntimeErrorObservedEvent =
  | (TurnOwnedLoggedEvent<"runtime_error_observed", { readonly error: RedactedTurnError }> & { readonly scope: "turn" })
  | (LoggedEventBase<"runtime_error_observed", { readonly error: RedactedTurnError }> & { readonly inputId: null; readonly turnId: null; readonly scope: "session" })
export type RunFinishedEvent = TurnOwnedLoggedEvent<"run_finished", CanonicalJsonObject>

export type LoggedSessionEvent =
  | InputObservedEvent
  | TurnStartedEvent
  | AssistantTextDeltaEvent
  | AssistantTextCompletedEvent
  | TurnCompletedEvent
  | TurnFailedEvent
  | TurnCancelledEvent
  | ConversationCompactionStartedEvent
  | ConversationCompactionCompletedEvent
  | AssistantMessageStartedEvent
  | AssistantReasoningDeltaEvent
  | AssistantThoughtSummaryDeltaEvent
  | ToolExecutionStartedEvent
  | ToolExecutionStdoutDeltaEvent
  | ToolExecutionStderrDeltaEvent
  | ToolExecutionCompletedEvent
  | ToolCalledEvent
  | ToolResultObservedEvent
  | TodoUpdatedEvent
  | PermissionRequestedEvent
  | PermissionRespondedEvent
  | CheckpointListObservedEvent
  | CheckpointRestoredEvent
  | SkillsCatalogObservedEvent
  | SkillsSelectionObservedEvent
  | CTreeNodeObservedEvent
  | CTreeSnapshotObservedEvent
  | TaskEventObservedEvent
  | WarningObservedEvent
  | RewardUpdatedEvent
  | LimitsUpdatedEvent
  | CompletionObservedEvent
  | LogLinkedEvent
  | RuntimeErrorObservedEvent
  | RunFinishedEvent

export type CanonicalE4Failure =
  | { readonly kind: "http"; readonly status: number; readonly code: string | null; readonly body: typeof REDACTED_VALUE; readonly turnId?: TurnId }
  | { readonly kind: "timeout" }
  | { readonly kind: "caller-abort" }
  | { readonly kind: "protocol"; readonly code: string; readonly eventId?: EventId; readonly sequence?: number }
  | { readonly kind: "resume-gap"; readonly code: string; readonly lastAppliedEventId: EventId | null; readonly lastAppliedSequence: number }
  | { readonly kind: "session-not-found"; readonly sessionId: SessionId }
  | { readonly kind: "admission-conflict"; readonly sessionId: SessionId; readonly code: string | null }
  | { readonly kind: "idempotency-conflict"; readonly sessionId: SessionId; readonly turnId: TurnId | null }
  | { readonly kind: "cancellation-conflict"; readonly sessionId: SessionId; readonly turnId: TurnId; readonly code: string | null }
  | { readonly kind: "turn-failed"; readonly sessionId: SessionId; readonly inputId: InputId; readonly turnId: TurnId; readonly error: RedactedTurnError }
export const turnFailureFromEvent = (event: TurnFailedEvent): CanonicalE4Failure => ({
  kind: "turn-failed",
  sessionId: event.sessionId,
  inputId: event.inputId,
  turnId: event.turnId,
  error: event.payload.error,
})


const failureMessage = (failure: CanonicalE4Failure): string => {
  switch (failure.kind) {
    case "http": return `HTTP request failed (${failure.status})`
    case "timeout": return "Request timed out"
    case "caller-abort": return "Request aborted by caller"
    case "protocol": return `Session protocol error (${failure.code})`
    case "resume-gap": return `Session replay gap (${failure.code})`
    case "session-not-found": return "Session not found"
    case "admission-conflict": return "Session admission conflict"
    case "idempotency-conflict": return "Session input idempotency conflict"
    case "cancellation-conflict": return "Session cancellation conflict"
    case "turn-failed": return "Session turn failed"
  }
}

export class CanonicalE4ClientError extends Error {
  readonly failure: CanonicalE4Failure
  constructor(failure: CanonicalE4Failure) {
    super(failureMessage(failure))
    this.name = "CanonicalE4ClientError"
    this.failure = failure
  }
}

export interface CanonicalE4ClientConfig {
  readonly baseUrl: string
  readonly authToken?: string
  readonly requestTimeoutMs?: number
  readonly fetch?: typeof fetch
}

export interface OpenedSession {
  readonly sessionId: SessionId
  snapshot(): Promise<SessionSnapshot>
  submit(input: SubmitInput): Promise<SubmitReceipt>
  cancel(request: CancelTurnRequest): Promise<CancellationReceipt>
  respondPermission(request: RespondPermissionRequest): Promise<PermissionDecisionReceipt>
  events(request?: ObserveSessionRequest): AsyncGenerator<LoggedSessionEvent, void, void>
  close(): Promise<void>
}

export interface CanonicalE4Client {
  create(request: CreateSessionRequest): Promise<OpenedSession>
  attach(request: AttachSessionRequest): Promise<OpenedSession>
}

export type OpenedSessionRuntime = OpenedSession
export type CancelReceipt = CancellationReceipt
export type ObserveEvents = ObserveSessionRequest
export type SubmitTextTurn = SubmitInput


type RawObject = { readonly [key: string]: unknown }

const MAX_JSON_RESPONSE_BYTES = 1024 * 1024
const MAX_ERROR_RESPONSE_BYTES = 64 * 1024
const MAX_SSE_CHUNK_BYTES = 1024 * 1024
const MAX_SSE_FRAME_BYTES = 512 * 1024
const MAX_SSE_LINE_BYTES = MAX_SSE_FRAME_BYTES
const MAX_SSE_DATA_LINE_COUNT = 4096
const MAX_SSE_EVENT_DATA_BYTES = 256 * 1024
const MAX_SSE_EVENT_ID_BYTES = 1024
const MAX_SSE_PENDING_EVENT_BYTES = 256 * 1024
const MAX_SSE_PENDING_EVENT_COUNT = 2048

const utf8ByteLength = (value: string): number => {
  let bytes = 0
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code <= 0x7f) bytes += 1
    else if (code <= 0x7ff) bytes += 2
    else if (code >= 0xd800 && code <= 0xdbff && index + 1 < value.length && value.charCodeAt(index + 1) >= 0xdc00 && value.charCodeAt(index + 1) <= 0xdfff) {
      bytes += 4
      index += 1
    } else bytes += 3
  }
  return bytes
}

const isRawObject = (value: unknown): value is RawObject =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const own = (value: RawObject, key: string): unknown => Object.prototype.hasOwnProperty.call(value, key) ? value[key] : undefined

const requiredString = (value: unknown, field: string): string => {
  if (typeof value !== "string" || value.length === 0) throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}` })
  return value
}

const optionalString = (value: unknown, field: string): string | null => {
  if (value === null || value === undefined) return null
  return requiredString(value, field)
}

const requiredInteger = (value: unknown, field: string, minimum = 0): number => {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}` })
  }
  return value
}

const requiredEnum = <T extends string>(value: unknown, field: string, allowed: readonly T[]): T => {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}` })
  }
  return value as T
}

export function decodeExactEmptyPayload(value: unknown): ExactEmptyPayload {
  if (!isRawObject(value) || Object.getOwnPropertyNames(value).length !== 0) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_exact_empty_payload" })
  }
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_exact_empty_payload_prototype" })
  }
  return ExactEmptyPayload.value
}

export function normalizeSubmitInput(input: string): { readonly text: string }
export function normalizeSubmitInput<T extends StructuredSubmit>(input: T): T
export function normalizeSubmitInput(input: SubmitInput): StructuredSubmit
export function normalizeSubmitInput(input: SubmitInput): StructuredSubmit {
  return typeof input === "string" ? { text: input } : input
}

const toJsonValue = (value: unknown, seen: Set<object>): CanonicalJsonValue => {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "cyclic_value" })
    seen.add(value)
    const result = value.map((item) => toJsonValue(item, seen))
    seen.delete(value)
    return result
  }
  if (typeof value !== "object") throw new CanonicalE4ClientError({ kind: "protocol", code: "non_json_value" })
  if (seen.has(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "cyclic_value" })
  seen.add(value)
  const result: { [key: string]: CanonicalJsonValue } = {}
  for (const key of Object.keys(value).sort()) {
    const item = (value as RawObject)[key]
    if (item === undefined) throw new CanonicalE4ClientError({ kind: "protocol", code: "undefined_json_value" })
    result[key] = toJsonValue(item, seen)
  }
  seen.delete(value)
  return result
}

export const deterministicSerialize = (value: unknown): Uint8Array =>
  new TextEncoder().encode(JSON.stringify(toJsonValue(value, new Set())))

const bytesToHex = (bytes: ArrayBuffer): string =>
  Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("")

export const sha256Bytes = async (bytes: Uint8Array): Promise<string> =>
  `sha256:${bytesToHex(await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes)))}`

export const serializeLoggedSessionEvent = (event: LoggedSessionEvent): Uint8Array => deterministicSerialize({
  eventId: event.eventId,
  inputId: event.inputId,
  kind: event.kind,
  occurredAtMs: event.occurredAtMs,
  payload: event.payload,
  sequence: event.sequence,
  sessionId: event.sessionId,
  turnId: event.turnId,
})

export const digestLoggedSessionEvent = async (event: LoggedSessionEvent): Promise<string> =>
  sha256Bytes(serializeLoggedSessionEvent(event))

const parseReplayRetention = (value: unknown): ReplayRetention => {
  if (!isRawObject(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_replay_retention" })
  const maxEvents = requiredInteger(own(value, "maxEvents"), "replay_max_events", 1)
  const maxAgeMs = requiredInteger(own(value, "maxAgeMs"), "replay_max_age_ms", 1)
  const configurationDigest = requiredString(own(value, "configurationDigest"), "replay_configuration_digest") as ReplayContractDigest
  if (maxEvents !== REPLAY_RETENTION_MAX_EVENTS || maxAgeMs !== REPLAY_RETENTION_MAX_AGE_MS || configurationDigest !== replayConfigurationDigest) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "replay_contract_mismatch" })
  }
  return { maxEvents: REPLAY_RETENTION_MAX_EVENTS, maxAgeMs: REPLAY_RETENTION_MAX_AGE_MS, configurationDigest }
}

const parseReplayFacts = (value: RawObject): SessionReplayFacts => {
  const facts: SessionReplayFacts = {
    replayRetention: parseReplayRetention(own(value, "replayRetention")),
    earliestRetainedSequence: own(value, "earliestRetainedSequence") === null ? null : requiredInteger(own(value, "earliestRetainedSequence"), "earliest_retained_sequence", 1),
    earliestRetainedEventId: optionalString(own(value, "earliestRetainedEventId"), "earliest_retained_event_id") as EventId | null,
    headSequence: requiredInteger(own(value, "headSequence"), "head_sequence"),
    headEventId: optionalString(own(value, "headEventId"), "head_event_id") as EventId | null,
    retainedHistory: requiredEnum(own(value, "retainedHistory"), "retained_history", ["complete", "partial"] as const),
    sessionReplayContractDigest: requiredString(own(value, "sessionReplayContractDigest"), "session_replay_contract_digest") as ReplayContractDigest,
  }
  if ((facts.earliestRetainedSequence === null) !== (facts.earliestRetainedEventId === null)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "replay_earliest_pair_mismatch" })
  }
  if (facts.headSequence === 0 && (facts.headEventId !== null || facts.earliestRetainedSequence !== null || facts.retainedHistory !== "complete")) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_empty_replay_facts" })
  }
  if (facts.earliestRetainedSequence !== null && (facts.headEventId === null || facts.earliestRetainedSequence > facts.headSequence)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_replay_range" })
  }
  if (facts.retainedHistory === "complete" && facts.headSequence > 0 && (facts.earliestRetainedSequence !== 1 || facts.headEventId === null)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "incomplete_replay_marked_complete" })
  }
  return facts
}

export const computeSessionReplayDigest = async (facts: Omit<SessionReplayFacts, "sessionReplayContractDigest">): Promise<ReplayContractDigest> =>
  sha256Bytes(deterministicSerialize({ schemaVersion: SESSION_REPLAY_SCHEMA_VERSION, ...facts })) as Promise<ReplayContractDigest>

export const validateSessionReplayFacts = async (facts: SessionReplayFacts): Promise<void> => {
  const expected = await computeSessionReplayDigest({
    replayRetention: facts.replayRetention,
    earliestRetainedSequence: facts.earliestRetainedSequence,
    earliestRetainedEventId: facts.earliestRetainedEventId,
    headSequence: facts.headSequence,
    headEventId: facts.headEventId,
    retainedHistory: facts.retainedHistory,
  })
  if (expected !== facts.sessionReplayContractDigest) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "session_replay_digest_mismatch" })
  }
}

export const assertAdvertisedReplayConfigurationDigest = (advertised: string): void => {
  if (advertised !== replayConfigurationDigest) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "advertised_replay_configuration_mismatch" })
  }
}

const parseTerminalTurns = (value: unknown): readonly TerminalTurnSnapshot[] => {
  if (!Array.isArray(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_terminal_turns" })
  return value.map((item) => {
    if (!isRawObject(item)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_terminal_turn" })
    return {
      inputId: requiredString(own(item, "input_id"), "terminal_input_id") as InputId,
      turnId: requiredString(own(item, "turn_id"), "terminal_turn_id") as TurnId,
      outcome: requiredEnum(own(item, "outcome"), "terminal_outcome", ["completed", "failed", "cancelled"] as const),
      originalDisposition: requiredEnum(own(item, "original_disposition"), "terminal_original_disposition", ["started", "queued"] as const),
    }
  })
}

const decodeSnapshot = async (value: unknown): Promise<SessionSnapshot> => {
  if (!isRawObject(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_session_snapshot" })
  const facts = parseReplayFacts(value)
  await validateSessionReplayFacts(facts)
  const snapshot: SessionSnapshot = {
    ...facts,
    sessionId: requiredString(own(value, "session_id"), "session_id") as SessionId,
    status: requiredEnum(own(value, "status"), "session_status", ["starting", "running", "completed", "failed", "stopped"] as const),
    createdAt: requiredString(own(value, "created_at"), "created_at"),
    lastActivityAt: requiredString(own(value, "last_activity_at"), "last_activity_at"),
    model: optionalString(own(value, "model"), "model"),
    mode: optionalString(own(value, "mode"), "mode"),
    turnAdmission: requiredEnum(own(value, "turn_admission"), "turn_admission", ["idle", "active"] as const),
    activeTurnId: optionalString(own(value, "active_turn_id"), "active_turn_id") as TurnId | null,
    queuedTurnCount: requiredInteger(own(value, "queued_turn_count"), "queued_turn_count"),
    terminalTurns: parseTerminalTurns(own(value, "terminalTurns")),
  }
  if (snapshot.turnAdmission === "idle" && (snapshot.activeTurnId !== null || snapshot.queuedTurnCount !== 0)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "idle_admission_invariant" })
  }
  if (snapshot.turnAdmission === "active" && snapshot.activeTurnId === null && snapshot.queuedTurnCount === 0) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "active_admission_invariant" })
  }
  return snapshot
}

const parseJsonObjectPayload = (payload: unknown, field: string): CanonicalJsonObject => {
  if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}_payload` })
  return toJsonValue(payload, new Set()) as CanonicalJsonObject
}

const payloadText = (payload: RawObject): unknown => {
  const direct = own(payload, "text")
  if (typeof direct === "string") return direct
  const delta = own(payload, "delta")
  if (typeof delta === "string") return delta
  const message = own(payload, "message")
  return isRawObject(message) ? own(message, "content") : undefined
}

const parseTextPayload = (payload: unknown, field: string): { readonly text: string } => {
  if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}_payload` })
  return { text: requiredString(payloadText(payload), `${field}_text`) }
}

const parseOptionalTextPayload = (payload: unknown, field: string): { readonly text: string | null } => {
  if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: `invalid_${field}_payload` })
  const text = payloadText(payload)
  return { text: text === undefined ? null : requiredString(text, `${field}_text`) }
}

const parseCancellationReason = (payload: unknown): { readonly reason: CancellationReason } => {
  if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_turn_cancelled_payload" })
  return { reason: requiredEnum(own(payload, "reason"), "cancellation_reason", ["user_requested", "timeout", "superseded"] as const) }
}

const parseTurnFailure = (payload: unknown): { readonly error: RedactedTurnError } => {
  if (!isRawObject(payload) || !isRawObject(own(payload, "error"))) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_turn_failed_payload" })
  }
  const rawError = own(payload, "error") as RawObject
  const code = requiredString(own(rawError, "code"), "turn_error_code")
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(code)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_turn_error_code" })
  return { error: { code, message: REDACTED_VALUE } }
}
const parseRuntimeFailure = (payload: unknown): { readonly error: RedactedTurnError } => {
  if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_runtime_error_payload" })
  const nested = own(payload, "error")
  const rawCode = isRawObject(nested) ? own(nested, "code") : own(payload, "code")
  const code = requiredString(rawCode, "runtime_error_code")
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(code)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_runtime_error_code" })
  return { error: { code, message: REDACTED_VALUE } }
}

const optionalPayloadString = (payload: RawObject, key: string, field: string): string | null =>
  optionalString(own(payload, key), field)

const parseAssistantMessageStarted = (payload: unknown): AssistantMessageStartedPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_assistant_message_started_payload" })
  }
  const message = own(payload, "message")
  const rawMessage = isRawObject(message) ? message : null
  const toolCalls = rawMessage === null ? own(payload, "tool_calls") : own(rawMessage, "tool_calls")
  const messageId =
    optionalPayloadString(payload, "message_id", "assistant_message_id")
    ?? optionalPayloadString(payload, "item_id", "assistant_message_id")
    ?? optionalPayloadString(payload, "id", "assistant_message_id")
  return { messageId, toolCallCount: Array.isArray(toolCalls) ? toolCalls.length : 0 }
}

const isToolCallOnlyAssistantMessage = (payload: unknown): boolean => {
  if (!isRawObject(payload)) return false
  const message = own(payload, "message")
  if (!isRawObject(message)) return false
  const toolCalls = own(message, "tool_calls")
  const text = payloadText(payload)
  return Array.isArray(toolCalls) && toolCalls.length > 0 && (text === undefined || text === null || text === "")
}

const parseToolCalled = (payload: unknown): ToolCalledPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_tool_called_payload" })
  }
  const nestedCall = own(payload, "call")
  const call = isRawObject(nestedCall) ? nestedCall : payload
  const nestedFunction = own(call, "function")
  const fn = isRawObject(nestedFunction) ? nestedFunction : null
  const callId = requiredString(
    own(payload, "call_id") ?? own(call, "id") ?? own(call, "call_id") ?? own(call, "tool_call_id"),
    "tool_called_call_id",
  ) as ToolCallId
  const tool = requiredString(
    own(payload, "tool") ?? own(call, "name") ?? (fn === null ? undefined : own(fn, "name")),
    "tool_called_tool",
  )
  const rawArguments =
    own(payload, "arguments")
    ?? own(call, "arguments")
    ?? (fn === null ? undefined : own(fn, "arguments"))
  const action = optionalPayloadString(payload, "action", "tool_called_action")
  return {
    callId,
    tool,
    arguments: rawArguments === undefined ? null : toJsonValue(rawArguments, new Set()),
    action,
    diffPreview: own(payload, "diff_preview") === undefined
      ? null
      : toJsonValue(own(payload, "diff_preview"), new Set()),
    progress: own(payload, "progress") === undefined
      ? null
      : toJsonValue(own(payload, "progress"), new Set()),
  }
}

const parseToolResultObserved = (payload: unknown): ToolResultObservedPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_tool_result_observed_payload" })
  }
  const nestedMessage = own(payload, "message")
  const message = isRawObject(nestedMessage) ? nestedMessage : null
  const callId = requiredString(
    own(payload, "call_id")
    ?? (message === null ? undefined : own(message, "tool_call_id"))
    ?? (message === null ? undefined : own(message, "call_id")),
    "tool_result_call_id",
  ) as ToolCallId
  const errorValue = own(payload, "error")
  if (typeof errorValue !== "boolean") {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_tool_result_error" })
  }
  const rawStatus = own(payload, "status")
  const status = typeof rawStatus === "string" && rawStatus.length > 0 ? rawStatus : errorValue ? "error" : "ok"
  const rawResult =
    own(payload, "result")
    ?? own(payload, "content")
    ?? (message === null ? undefined : own(message, "content"))
  return {
    callId,
    tool: optionalPayloadString(payload, "tool", "tool_result_tool"),
    status,
    error: errorValue,
    result: rawResult === undefined ? null : toJsonValue(rawResult, new Set()),
    artifactRef: own(payload, "artifact_ref") === undefined
      ? null
      : toJsonValue(own(payload, "artifact_ref"), new Set()),
  }
}

const parsePermissionRequested = (payload: unknown): PermissionRequestedPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_permission_requested_payload" })
  }
  const rewindable = own(payload, "rewindable")
  if (typeof rewindable !== "boolean") {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_permission_requested_rewindable" })
  }
  return {
    requestId: requiredString(own(payload, "request_id"), "permission_request_id") as PermissionRequestId,
    tool: requiredString(own(payload, "tool"), "permission_request_tool"),
    kind: requiredString(own(payload, "kind"), "permission_request_kind"),
    summary: optionalPayloadString(payload, "summary", "permission_request_summary"),
    defaultScope: optionalPayloadString(payload, "default_scope", "permission_request_default_scope"),
    rewindable,
  }
}

const parsePermissionResponded = (payload: unknown): PermissionRespondedPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_permission_responded_payload" })
  }
  return {
    requestId: requiredString(own(payload, "request_id"), "permission_response_request_id") as PermissionRequestId,
    decision: requiredString(own(payload, "decision"), "permission_response_decision"),
  }
}

const parseTaskEventObserved = (payload: unknown): TaskEventObservedPayload => {
  if (!isRawObject(payload)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_task_event_observed_payload" })
  }
  const taskId = requiredString(own(payload, "task_id"), "task_event_task_id") as TaskId
  const parentTaskId = optionalPayloadString(payload, "parent_task_id", "task_event_parent_task_id")
  const childSessionId = optionalPayloadString(payload, "child_session_id", "task_event_child_session_id")
  const parentSessionId = optionalPayloadString(payload, "parent_session_id", "task_event_parent_session_id")
  return {
    taskId,
    kind: requiredString(own(payload, "kind"), "task_event_kind"),
    status: optionalPayloadString(payload, "status", "task_event_status"),
    description: optionalPayloadString(payload, "description", "task_event_description"),
    parentTaskId: parentTaskId as TaskId | null,
    childSessionId: childSessionId as SessionId | null,
    parentSessionId: parentSessionId as SessionId | null,
    laneId: optionalPayloadString(payload, "lane_id", "task_event_lane_id"),
    laneLabel: optionalPayloadString(payload, "lane_label", "task_event_lane_label"),
  }
}


export const decodeLoggedSessionEvent = (value: unknown): LoggedSessionEvent => {
  if (!isRawObject(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_event_envelope" })
  if (own(value, "stable_cursor") !== true) throw new CanonicalE4ClientError({ kind: "protocol", code: "event_not_stable_cursor" })
  const eventId = requiredString(own(value, "id"), "event_id") as EventId
  const sequence = requiredInteger(own(value, "seq"), "event_sequence", 1)
  const sessionId = requiredString(own(value, "session_id"), "event_session_id") as SessionId
  const inputId = optionalString(own(value, "input_id"), "event_input_id") as InputId | null
  const turnId = optionalString(own(value, "turn_id"), "event_turn_id") as TurnId | null
  if ((inputId === null) !== (turnId === null)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "incomplete_event_correlation", eventId, sequence })
  }
  const occurredAtMs = requiredInteger(own(value, "timestamp_ms"), "event_timestamp_ms")
  const type = requiredString(own(value, "type"), "event_type")
  const payload = own(value, "payload")
  const base = { eventId, sequence, sessionId, inputId, turnId, occurredAtMs }
  const turnBase = () => {
    if (inputId === null || turnId === null) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_turn_correlation", eventId, sequence })
    }
    return { eventId, sequence, sessionId, inputId, turnId, occurredAtMs }
  }
  const jsonPayload = (field: string) => parseJsonObjectPayload(payload, field)
  switch (type) {
    case "user_message": return { ...turnBase(), kind: "input_observed", payload: parseTextPayload(payload, "input_observed") }
    case "turn_start": return {
      ...turnBase(),
      kind: "turn_started",
      payload: decodeExactEmptyPayload(payload),
    }
    case "conversation.compaction.start": return { ...turnBase(), kind: "conversation_compaction_started", payload: jsonPayload("conversation_compaction_started") }
    case "conversation.compaction.end": return { ...turnBase(), kind: "conversation_compaction_completed", payload: jsonPayload("conversation_compaction_completed") }
    case "assistant.message.start": return { ...turnBase(), kind: "assistant_message_started", payload: parseAssistantMessageStarted(payload) }
    case "assistant.message.delta":
    case "assistant_delta":
      return { ...turnBase(), kind: "assistant_text_delta", payload: parseTextPayload(payload, "assistant_text_delta") }
    case "assistant.message.end":
      return { ...turnBase(), kind: "assistant_text_completed", payload: parseOptionalTextPayload(payload, "assistant_text_completed") }
    case "assistant_message":
      return isToolCallOnlyAssistantMessage(payload)
        ? { ...turnBase(), kind: "assistant_message_started", payload: parseAssistantMessageStarted(payload) }
        : { ...turnBase(), kind: "assistant_text_completed", payload: parseOptionalTextPayload(payload, "assistant_text_completed") }
    case "assistant.reasoning.delta": return { ...turnBase(), kind: "assistant_reasoning_delta", payload: parseTextPayload(payload, "assistant_reasoning_delta") }
    case "assistant.thought_summary.delta": return { ...turnBase(), kind: "assistant_thought_summary_delta", payload: parseTextPayload(payload, "assistant_thought_summary_delta") }
    case "tool.exec.start": return { ...turnBase(), kind: "tool_execution_started", payload: jsonPayload("tool_execution_started") }
    case "tool.exec.stdout.delta": return { ...turnBase(), kind: "tool_execution_stdout_delta", payload: jsonPayload("tool_execution_stdout_delta") }
    case "tool.exec.stderr.delta": return { ...turnBase(), kind: "tool_execution_stderr_delta", payload: jsonPayload("tool_execution_stderr_delta") }
    case "tool.exec.end": return { ...turnBase(), kind: "tool_execution_completed", payload: jsonPayload("tool_execution_completed") }
    case "tool_call": return { ...turnBase(), kind: "tool_called", payload: parseToolCalled(payload) }
    case "todo_event": return { ...base, kind: "todo_updated", payload: jsonPayload("todo_updated") }
    case "tool.result":
    case "tool_result": {
      if (isRawObject(payload) && isRawObject(own(payload, "todo"))) {
        return { ...base, kind: "todo_updated", payload: jsonPayload("todo_updated") }
      }
      return { ...turnBase(), kind: "tool_result_observed", payload: parseToolResultObserved(payload) }
    }
    case "permission_request": return { ...turnBase(), kind: "permission_requested", payload: parsePermissionRequested(payload) }
    case "permission_response": return { ...turnBase(), kind: "permission_responded", payload: parsePermissionResponded(payload) }
    case "checkpoint_list": return { ...base, kind: "checkpoint_list_observed", payload: jsonPayload("checkpoint_list_observed") }
    case "checkpoint_restored": return { ...base, kind: "checkpoint_restored", payload: jsonPayload("checkpoint_restored") }
    case "skills_catalog": return { ...base, kind: "skills_catalog_observed", payload: jsonPayload("skills_catalog_observed") }
    case "skills_selection": return { ...base, kind: "skills_selection_observed", payload: jsonPayload("skills_selection_observed") }
    case "ctree_node": return { ...turnBase(), kind: "ctree_node_observed", payload: jsonPayload("ctree_node_observed") }
    case "ctree_snapshot": return { ...base, kind: "ctree_snapshot_observed", payload: jsonPayload("ctree_snapshot_observed") }
    case "task_event": return { ...turnBase(), kind: "task_event_observed", payload: parseTaskEventObserved(payload) }
    case "warning": return { ...turnBase(), kind: "warning_observed", payload: jsonPayload("warning_observed") }
    case "reward_update": return { ...turnBase(), kind: "reward_updated", payload: jsonPayload("reward_updated") }
    case "limits_update": return { ...turnBase(), kind: "limits_updated", payload: jsonPayload("limits_updated") }
    case "completion": return { ...turnBase(), kind: "completion_observed", payload: jsonPayload("completion_observed") }
    case "log_link": return { ...turnBase(), kind: "log_linked", payload: jsonPayload("log_linked") }
    case "error": return inputId === null
      ? { ...base, inputId: null, turnId: null, scope: "session", kind: "runtime_error_observed", payload: parseRuntimeFailure(payload) }
      : { ...turnBase(), scope: "turn", kind: "runtime_error_observed", payload: parseRuntimeFailure(payload) }
    case "run_finished": return { ...turnBase(), kind: "run_finished", payload: jsonPayload("run_finished") }
    case "turn_completed": return { ...turnBase(), kind: "turn_completed", payload: decodeExactEmptyPayload(payload) }
    case "turn_failed": return { ...turnBase(), kind: "turn_failed", payload: parseTurnFailure(payload) }
    case "turn_cancelled": return { ...turnBase(), kind: "turn_cancelled", payload: parseCancellationReason(payload) }
    default: throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_event_family", eventId, sequence })
  }
}

interface SafeErrorEnvelope {
  readonly code: string | null
  readonly turnId: TurnId | null
}

class ResponseBodyLimitError extends Error {}
class ResponseBodyProgressError extends Error {}

interface ResponseReaderState {
  readonly reader: ReadableStreamDefaultReader<Uint8Array>
  pending: Promise<ReadableStreamReadResult<Uint8Array>> | null
  released: boolean
}

const INPUT_ERROR_CODES = ["input_idempotency_conflict"] as const
const CANCELLATION_ERROR_CODES = ["turn_not_found", "turn_already_terminal", "cancellation_already_requested"] as const
const STREAM_ERROR_CODES = ["resume_window_exceeded"] as const

const createDeferred = <T>(): {
  readonly promise: Promise<T>
  readonly resolve: (value: T | PromiseLike<T>) => void
  readonly reject: (reason?: unknown) => void
} => {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const abortError = (): Error => {
  const error = new Error("aborted")
  error.name = "AbortError"
  return error
}

const cancelResponseBody = (body: ReadableStream<Uint8Array> | null): void => {
  if (body === null) return
  try {
    void body.cancel().catch(() => undefined)
  } catch {
    // A hostile Response implementation must not delay or replace the closed failure.
  }
}

const cancelReader = (state: ResponseReaderState): void => {
  try {
    void state.reader.cancel().catch(() => undefined)
  } catch {
    // Cancellation is best-effort; the read race owns liveness.
  }
}

const releaseReaderWhenIdle = (state: ResponseReaderState): void => {
  if (state.released) return
  const pending = state.pending
  if (pending !== null) {
    void pending.then(
      () => releaseReaderWhenIdle(state),
      () => releaseReaderWhenIdle(state),
    ).catch(() => undefined)
    return
  }
  state.released = true
  try {
    state.reader.releaseLock()
  } catch {
    // A reader whose implementation violates settlement ordering remains safely locked.
  }
}

const readReaderAbortably = (
  state: ResponseReaderState,
  signal: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> => {
  if (signal.aborted) {
    cancelReader(state)
    return Promise.reject(abortError())
  }
  const rawRead = state.reader.read()
  state.pending = rawRead
  const clearPending = () => {
    if (state.pending === rawRead) state.pending = null
  }
  void rawRead.then(clearPending, clearPending)
  const { promise, resolve, reject } = createDeferred<ReadableStreamReadResult<Uint8Array>>()
  let settled = false
  const onAbort = () => {
    if (settled) return
    settled = true
    cancelReader(state)
    reject(abortError())
  }
  const onValue = (value: ReadableStreamReadResult<Uint8Array>) => {
    if (settled) return
    settled = true
    signal.removeEventListener("abort", onAbort)
    resolve(value)
  }
  const onError = (error: unknown) => {
    if (settled) return
    settled = true
    signal.removeEventListener("abort", onAbort)
    reject(error)
  }
  signal.addEventListener("abort", onAbort, { once: true })
  if (signal.aborted) onAbort()
  void rawRead.then(onValue, onError)
  return promise
}

const contentLengthExceeds = (response: Response, limit: number): boolean => {
  const contentLength = response.headers.get("content-length")
  if (contentLength === null || !/^\d+$/.test(contentLength)) return false
  const normalized = contentLength.replace(/^0+/, "") || "0"
  const maximum = String(limit)
  return normalized.length > maximum.length || (normalized.length === maximum.length && normalized > maximum)
}

const readBoundedResponseBytes = async (
  response: Response,
  limit: number,
  controller: AbortController,
): Promise<Uint8Array> => {
  if (contentLengthExceeds(response, limit)) {
    controller.abort()
    cancelResponseBody(response.body)
    throw new ResponseBodyLimitError()
  }
  if (response.body === null) return new Uint8Array()
  const state: ResponseReaderState = { reader: response.body.getReader(), pending: null, released: false }
  let bytes = new Uint8Array()
  let totalBytes = 0
  try {
    while (true) {
      const result = await readReaderAbortably(state, controller.signal)
      if (result.done) break
      const chunkBytes = result.value.byteLength
      if (chunkBytes === 0) {
        controller.abort()
        cancelReader(state)
        throw new ResponseBodyProgressError()
      }
      const requiredBytes = totalBytes + chunkBytes
      if (requiredBytes > limit) {
        controller.abort()
        cancelReader(state)
        throw new ResponseBodyLimitError()
      }
      if (requiredBytes > bytes.byteLength) {
        let capacity = bytes.byteLength === 0 ? Math.min(1024, limit) : bytes.byteLength
        while (capacity < requiredBytes) capacity = Math.min(limit, capacity * 2)
        const grown = new Uint8Array(capacity)
        grown.set(bytes.subarray(0, totalBytes))
        bytes = grown
      }
      bytes.set(result.value, totalBytes)
      totalBytes = requiredBytes
    }
  } finally {
    releaseReaderWhenIdle(state)
  }
  return bytes.subarray(0, totalBytes)
}

const readBoundedResponseJson = async (
  response: Response,
  limit: number,
  controller: AbortController,
): Promise<unknown> => JSON.parse(new TextDecoder().decode(await readBoundedResponseBytes(response, limit, controller))) as unknown

const parseSafeErrorEnvelope = async (
  response: Response,
  controller: AbortController,
  allowedCodes: readonly string[],
  correlationCodes: readonly string[] = [],
): Promise<SafeErrorEnvelope> => {
  let value: unknown
  try {
    value = await readBoundedResponseJson(response, MAX_ERROR_RESPONSE_BYTES, controller)
  } catch (error) {
    if (error instanceof ResponseBodyLimitError || error instanceof ResponseBodyProgressError || error instanceof SyntaxError) return { code: null, turnId: null }
    throw error
  }
  if (!isRawObject(value)) return { code: null, turnId: null }
  const detail = isRawObject(own(value, "detail")) ? own(value, "detail") as RawObject : null
  const rawCode = typeof own(value, "error") === "string" ? own(value, "error") : detail === null ? null : own(detail, "code")
  const code = typeof rawCode === "string" && allowedCodes.includes(rawCode) ? rawCode : null
  const rawTurnId = code !== null && correlationCodes.includes(code) && detail !== null ? own(detail, "turn_id") : null
  const turnId = typeof rawTurnId === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(rawTurnId)
    ? rawTurnId as TurnId
    : null
  return { code, turnId }
}

const pathForSession = (sessionId: SessionId | string, suffix = ""): string =>
  `/v1/sessions/${encodeURIComponent(String(sessionId))}${suffix}`

const isAbortError = (error: unknown): boolean =>
  typeof error === "object" && error !== null && "name" in error && (error as { readonly name?: unknown }).name === "AbortError"

interface RequestContext {
  readonly fetch: typeof fetch
  readonly baseUrl: string
  readonly authToken?: string
  readonly timeoutMs: number
}

const buildUrl = (context: RequestContext, path: string, query?: readonly (readonly [string, string])[]): URL => {
  const url = new URL(`${context.baseUrl.replace(/\/$/, "")}${path}`)
  for (const [key, value] of query ?? []) url.searchParams.set(key, value)
  return url
}

const requestJson = async (
  context: RequestContext,
  path: string,
  method: "GET" | "POST",
  body: unknown,
  callerSignal?: AbortSignal,
): Promise<unknown> => {
  const controller = new AbortController()
  let timedOut = false
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, context.timeoutMs)
  const onCallerAbort = () => controller.abort()
  callerSignal?.addEventListener("abort", onCallerAbort, { once: true })
  try {
    const response = await context.fetch(buildUrl(context, path), {
      method,
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(context.authToken ? { Authorization: `Bearer ${context.authToken}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      redirect: "error",
    })
    if (!response.ok) {
      const allowedCodes = response.status === 409 && method === "POST" && path.endsWith("/input")
        ? INPUT_ERROR_CODES
        : response.status === 409 && method === "POST" && path.endsWith("/cancel")
          ? CANCELLATION_ERROR_CODES
          : []
      const safe = await parseSafeErrorEnvelope(response, controller, allowedCodes, INPUT_ERROR_CODES)
      if (response.status === 404) {
        const sessionId = path.split("/")[3] ?? "unknown"
        throw new CanonicalE4ClientError({ kind: "session-not-found", sessionId: sessionId as SessionId })
      }
      throw new CanonicalE4ClientError({ kind: "http", status: response.status, code: safe.code, body: REDACTED_VALUE, ...(safe.turnId === null ? {} : { turnId: safe.turnId }) })
    }
    try {
      return await readBoundedResponseJson(response, MAX_JSON_RESPONSE_BYTES, controller)
    } catch (error) {
      if (error instanceof ResponseBodyLimitError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "response_body_too_large" })
      }
      if (error instanceof ResponseBodyProgressError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "response_body_no_progress" })
      }
      if (error instanceof SyntaxError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_json_response" })
      }
      throw error
    }
  } catch (error) {
    if (error instanceof CanonicalE4ClientError) throw error
    if (isAbortError(error)) {
      if (callerSignal?.aborted) throw new CanonicalE4ClientError({ kind: "caller-abort" })
      if (timedOut) throw new CanonicalE4ClientError({ kind: "timeout" })
    }
    throw new CanonicalE4ClientError({ kind: "http", status: 0, code: null, body: REDACTED_VALUE })
  } finally {
    clearTimeout(timeout)
    callerSignal?.removeEventListener("abort", onCallerAbort)
  }
}
const attachmentKeysMatch = (value: RawObject, expected: readonly string[]): boolean => {
  const actual = Object.keys(value).sort()
  const canonical = [...expected].sort()
  return actual.length === canonical.length && actual.every((key, index) => key === canonical[index])
}

const uploadAttachments = async (
  context: RequestContext,
  sessionId: SessionId,
  uploads: readonly AttachmentUploadInput[],
): Promise<readonly string[]> => {
  const form = new FormData()
  for (const upload of uploads) form.append("files", upload.data, upload.filename)
  const controller = new AbortController()
  let timedOut = false
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, context.timeoutMs)
  try {
    const response = await context.fetch(buildUrl(context, pathForSession(sessionId, "/attachments")), {
      method: "POST",
      headers: {
        Accept: "application/json",
        ...(context.authToken ? { Authorization: `Bearer ${context.authToken}` } : {}),
      },
      body: form,
      signal: controller.signal,
      redirect: "error",
    })
    if (!response.ok) {
      const safe = await parseSafeErrorEnvelope(response, controller, [])
      if (response.status === 404) throw new CanonicalE4ClientError({ kind: "session-not-found", sessionId })
      throw new CanonicalE4ClientError({ kind: "http", status: response.status, code: safe.code, body: REDACTED_VALUE })
    }
    let value: unknown
    try {
      value = await readBoundedResponseJson(response, MAX_JSON_RESPONSE_BYTES, controller)
    } catch (error) {
      if (error instanceof ResponseBodyLimitError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "response_body_too_large" })
      }
      if (error instanceof ResponseBodyProgressError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "response_body_no_progress" })
      }
      if (error instanceof SyntaxError) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_attachment_upload_response" })
      }
      throw error
    }
    if (!isRawObject(value) || !Array.isArray(own(value, "attachments"))) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_attachment_upload_response" })
    }
    const handles = own(value, "attachments") as readonly unknown[]
    if (handles.length !== uploads.length) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "attachment_upload_count_mismatch" })
    }
    return handles.map((handle) => {
      if (!isRawObject(handle)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_attachment_handle" })
      return requiredString(own(handle, "id"), "attachment_handle_id")
    })
  } catch (error) {
    if (error instanceof CanonicalE4ClientError) throw error
    if (isAbortError(error) && timedOut) throw new CanonicalE4ClientError({ kind: "timeout" })
    throw new CanonicalE4ClientError({ kind: "http", status: 0, code: null, body: REDACTED_VALUE })
  } finally {
    clearTimeout(timeout)
  }
}

type ValidatedAttachmentInput = AttachmentHandleInput | AttachmentUploadInput

const validateAttachmentInputs = (inputs: readonly AttachmentInput[]): readonly ValidatedAttachmentInput[] => {
  const validated: ValidatedAttachmentInput[] = []
  for (const candidate of inputs as readonly unknown[]) {
    if (typeof candidate === "string") {
      validated.push({ kind: "handle", id: requiredString(candidate.trim(), "attachment_handle_id") })
      continue
    }
    if (!isRawObject(candidate)) throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_attachment_input" })
    const kind = own(candidate, "kind")
    if (kind === "handle" && attachmentKeysMatch(candidate, ["kind", "id"])) {
      validated.push({ kind: "handle", id: requiredString(own(candidate, "id"), "attachment_handle_id") })
      continue
    }
    if (kind === "upload" && attachmentKeysMatch(candidate, ["kind", "filename", "data"])) {
      const filename = requiredString(own(candidate, "filename"), "attachment_filename")
      const data = own(candidate, "data")
      if (typeof Blob === "undefined" || !(data instanceof Blob)) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_attachment_upload_data" })
      }
      validated.push({ kind: "upload", filename, data })
      continue
    }
    throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_attachment_input" })
  }
  return validated
}

const logicalSubmissionDigest = async (
  text: string,
  attachments: readonly ValidatedAttachmentInput[],
): Promise<string> => {
  const logicalAttachments: CanonicalJsonObject[] = []
  for (const attachment of attachments) {
    if (attachment.kind === "handle") {
      logicalAttachments.push({ kind: "handle", id: attachment.id })
      continue
    }
    const contentDigest = await sha256Bytes(new Uint8Array(await attachment.data.arrayBuffer()))
    logicalAttachments.push({
      kind: "upload",
      filename: attachment.filename,
      contentType: attachment.data.type,
      size: attachment.data.size,
      contentDigest,
    })
  }
  return sha256Bytes(deterministicSerialize({ text, attachments: logicalAttachments }))
}

const resolveAttachmentInputs = async (
  context: RequestContext,
  sessionId: SessionId,
  inputs: readonly ValidatedAttachmentInput[],
): Promise<readonly string[]> => {
  const uploads = inputs.filter((input): input is AttachmentUploadInput => input.kind === "upload")
  const uploadedIds = uploads.length === 0 ? [] : await uploadAttachments(context, sessionId, uploads)
  let uploadIndex = 0
  return inputs.map((input) => input.kind === "handle" ? input.id : uploadedIds[uploadIndex++])
}

interface ResolvedSubmissionBody {
  readonly content: string
  readonly client_message_id: ClientMessageId
  readonly attachments?: readonly string[]
}

interface SubmissionPreparation {
  readonly logicalDigest: string
  readonly body: Promise<ResolvedSubmissionBody>
}


const decodeSubmitReceipt = (value: unknown): SubmitReceipt => {
  if (!isRawObject(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_submit_receipt" })
  if (own(value, "status") !== "accepted") throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_submit_status" })
  return {
    clientMessageId: requiredString(own(value, "client_message_id"), "client_message_id") as ClientMessageId,
    inputId: requiredString(own(value, "input_id"), "input_id") as InputId,
    turnId: requiredString(own(value, "turn_id"), "turn_id") as TurnId,
    disposition: requiredEnum(own(value, "disposition"), "submit_disposition", ["started", "queued", "deduplicated"] as const),
    originalDisposition: requiredEnum(own(value, "original_disposition"), "submit_original_disposition", ["started", "queued"] as const),
  }
}

const decodeCancellationReceipt = (value: unknown): CancellationReceipt => {
  if (!isRawObject(value)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_cancellation_receipt" })
  if (own(value, "status") !== "accepted") throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_cancellation_status" })
  return {
    cancellationRequestId: requiredString(own(value, "cancellation_request_id"), "cancellation_request_id") as CancellationRequestId,
    cancellationRequestKey: requiredString(own(value, "cancellation_request_key"), "cancellation_request_key") as CancellationRequestKey,
    inputId: requiredString(own(value, "input_id"), "input_id") as InputId,
    turnId: requiredString(own(value, "turn_id"), "turn_id") as TurnId,
    disposition: requiredEnum(own(value, "disposition"), "cancellation_disposition", ["cancellation_requested", "queued_cancelled", "deduplicated"] as const),
    originalDisposition: requiredEnum(own(value, "original_disposition"), "cancellation_original_disposition", ["cancellation_requested", "queued_cancelled"] as const),
  }
}
const decodePermissionDecisionReceipt = (
  value: unknown,
  requestId: PermissionRequestId,
  decision: PermissionDecision,
): PermissionDecisionReceipt => {
  if (!isRawObject(value) || own(value, "status") !== "accepted") {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_permission_decision_receipt" })
  }
  const detail = own(value, "detail")
  if (!isRawObject(detail)) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_permission_decision_detail" })
  }
  const observedRequestId = requiredString(own(detail, "request_id"), "permission_decision_request_id") as PermissionRequestId
  if (observedRequestId !== requestId) {
    throw new CanonicalE4ClientError({ kind: "protocol", code: "permission_decision_identity_mismatch" })
  }
  return { requestId, decision }
}


const createRequestBody = (request: CreateSessionRequest): RawObject => ({
  config_path: request.configPath,
  task: request.task ?? "",
  ...(request.overrides === undefined ? {} : { overrides: request.overrides }),
  ...(request.metadata === undefined ? {} : { metadata: request.metadata }),
  ...(request.workspace === undefined ? {} : { workspace: request.workspace }),
  ...(request.maxSteps === undefined ? {} : { max_steps: request.maxSteps }),
  ...(request.permissionMode === undefined ? {} : { permission_mode: request.permissionMode }),
  ...(request.stream === undefined ? {} : { stream: request.stream }),
})

interface RawSseItem {
  readonly data: string
  readonly eventId: string | null
  readonly byteLength: number
}

class BoundedSseDecoder {
  private lineBuffer = new Uint8Array()
  private lineBytes = 0
  private readonly lineEncoder = new TextEncoder()
  private readonly lineDecoder = new TextDecoder()
  private frameBytes = 0
  private dataParts: string[] = []
  private dataBytes = 0
  private lastEventId: string | null = null
  private pendingCr = false

  constructor(
    private readonly onEvent: (data: string, eventId: string | null) => void,
    private readonly fail: (code: string) => never,
  ) {}

  feed(text: string): void {
    let offset = 0
    if (this.pendingCr) {
      this.pendingCr = false
      this.finishLine()
      if (text.startsWith("\n")) offset = 1
    }
    let segmentStart = offset
    while (offset < text.length) {
      const code = text.charCodeAt(offset)
      if (code !== 0x0a && code !== 0x0d) {
        offset += 1
        continue
      }
      this.appendLineSegment(text.slice(segmentStart, offset))
      if (code === 0x0d && offset + 1 === text.length) {
        this.pendingCr = true
        offset += 1
        segmentStart = offset
        break
      }
      if (code === 0x0d && text.charCodeAt(offset + 1) === 0x0a) offset += 1
      this.finishLine()
      offset += 1
      segmentStart = offset
    }
    this.appendLineSegment(text.slice(segmentStart))
  }

  finish(): void {
    if (this.pendingCr) {
      this.pendingCr = false
      this.finishLine()
    }
    if (this.frameBytes !== 0 || this.lineBytes !== 0 || this.dataParts.length !== 0) {
      this.fail("truncated_sse_frame")
    }
  }

  private appendLineSegment(segment: string): void {
    if (segment.length === 0) return
    const bytes = utf8ByteLength(segment)
    if (this.frameBytes + bytes > MAX_SSE_FRAME_BYTES) this.fail("sse_frame_too_large")
    if (this.lineBytes + bytes > MAX_SSE_LINE_BYTES) this.fail("sse_line_too_large")
    const requiredBytes = this.lineBytes + bytes
    if (requiredBytes > this.lineBuffer.byteLength) {
      let capacity = Math.max(64, this.lineBuffer.byteLength)
      while (capacity < requiredBytes) capacity = Math.min(MAX_SSE_LINE_BYTES, capacity * 2)
      const grown = new Uint8Array(capacity)
      grown.set(this.lineBuffer.subarray(0, this.lineBytes))
      this.lineBuffer = grown
    }
    const encoded = this.lineEncoder.encodeInto(segment, this.lineBuffer.subarray(this.lineBytes, requiredBytes))
    if (encoded.read !== segment.length || encoded.written !== bytes) this.fail("invalid_sse_utf8")
    this.lineBytes = requiredBytes
    this.frameBytes += bytes
  }

  private finishLine(): void {
    if (this.frameBytes + 1 > MAX_SSE_FRAME_BYTES) this.fail("sse_frame_too_large")
    this.frameBytes += 1
    const line = this.lineDecoder.decode(this.lineBuffer.subarray(0, this.lineBytes))
    this.lineBytes = 0
    if (line.length === 0) {
      this.dispatchEvent()
      this.frameBytes = 0
      return
    }
    if (line.startsWith(":")) return
    const colon = line.indexOf(":")
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? "" : line.slice(colon + 1)
    if (value.startsWith(" ")) value = value.slice(1)
    if (field === "data") {
      const separatorBytes = this.dataParts.length === 0 ? 0 : 1
      const valueBytes = utf8ByteLength(value)
      if (this.dataBytes + separatorBytes + valueBytes > MAX_SSE_EVENT_DATA_BYTES) this.fail("sse_event_data_too_large")
      if (this.dataParts.length >= MAX_SSE_DATA_LINE_COUNT) this.fail("sse_data_line_count_exceeded")
      this.dataBytes += separatorBytes + valueBytes
      this.dataParts.push(value)
      return
    }
    if (field === "id" && !value.includes("\0")) {
      if (utf8ByteLength(value) > MAX_SSE_EVENT_ID_BYTES) this.fail("sse_event_id_too_large")
      this.lastEventId = value
    }
  }

  private dispatchEvent(): void {
    if (this.dataParts.length === 0) return
    const data = this.dataParts.join("\n")
    this.dataParts = []
    this.dataBytes = 0
    this.onEvent(data, this.lastEventId)
  }
}

class RuntimeSession implements OpenedSession {
  readonly sessionId: SessionId
  private closed = false
  private lastAppliedEventId: EventId | null = null
  private lastAppliedSequence = 0
  private readonly retainedDigests = new Map<EventId, string>()
  private readonly activeStreams = new Map<AbortController, Promise<void>>()
  private readonly terminalTurns = new Map<TurnId, "turn_completed" | "turn_failed" | "turn_cancelled">()
  private closePromise: Promise<void> | null = null
  private readonly streamGenerators = new Set<AsyncGenerator<LoggedSessionEvent, void, void>>()
  private readonly submissionPreparations = new Map<ClientMessageId, SubmissionPreparation>()

  constructor(private readonly context: RequestContext, sessionId: SessionId) {
    this.sessionId = sessionId
  }

  async snapshot(): Promise<SessionSnapshot> {
    this.assertOpen()
    const observed = await decodeSnapshot(await requestJson(this.context, pathForSession(this.sessionId), "GET", undefined))
    if (observed.sessionId !== this.sessionId) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "cross_session_snapshot" })
    }
    return observed
  }

  async submit(input: SubmitInput): Promise<SubmitReceipt> {
    this.assertOpen()
    const normalized = normalizeSubmitInput(input)
    const unsupportedField = Object.keys(normalized).find((key) => !["text", "attachments", "clientMessageId"].includes(key))
    if (unsupportedField !== undefined) throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_submit_field" })
    if (!normalized.text.trim()) throw new CanonicalE4ClientError({ kind: "protocol", code: "empty_submit_text" })
    const clientMessageId = requiredString(normalized.clientMessageId ?? crypto.randomUUID(), "client_message_id") as ClientMessageId
    const validatedAttachments = validateAttachmentInputs(normalized.attachments ?? [])
    const logicalDigest = await logicalSubmissionDigest(normalized.text, validatedAttachments)
    let preparation = this.submissionPreparations.get(clientMessageId)
    if (preparation !== undefined && preparation.logicalDigest !== logicalDigest) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "client_message_id_body_mismatch" })
    }
    if (preparation === undefined) {
      const body = (async (): Promise<ResolvedSubmissionBody> => {
        const attachments = await resolveAttachmentInputs(this.context, this.sessionId, validatedAttachments)
        return {
          content: normalized.text,
          client_message_id: clientMessageId,
          ...(normalized.attachments === undefined ? {} : { attachments }),
        }
      })()
      preparation = { logicalDigest, body }
      this.submissionPreparations.set(clientMessageId, preparation)
    }
    let body: ResolvedSubmissionBody
    try {
      body = await preparation.body
    } catch (error) {
      if (this.submissionPreparations.get(clientMessageId) === preparation) {
        this.submissionPreparations.delete(clientMessageId)
      }
      throw error
    }
    try {
      const receipt = decodeSubmitReceipt(await requestJson(this.context, pathForSession(this.sessionId, "/input"), "POST", body))
      if (receipt.clientMessageId !== clientMessageId) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "submit_receipt_identity_mismatch" })
      }
      return receipt
    } catch (error) {
      if (error instanceof CanonicalE4ClientError && error.failure.kind === "http" && error.failure.status === 409) {
        if (error.failure.code === "input_idempotency_conflict") {
          throw new CanonicalE4ClientError({ kind: "idempotency-conflict", sessionId: this.sessionId, turnId: error.failure.turnId ?? null })
        }
        throw new CanonicalE4ClientError({ kind: "admission-conflict", sessionId: this.sessionId, code: error.failure.code })
      }
      throw error
    }
  }

  async cancel(request: CancelTurnRequest): Promise<CancellationReceipt> {
    this.assertOpen()
    const turnId = requiredString(String(request.turnId), "cancel_turn_id") as TurnId
    const cancellationRequestKey = requiredString(request.cancellationRequestKey ?? crypto.randomUUID(), "cancellation_request_key") as CancellationRequestKey
    try {
      const receipt = decodeCancellationReceipt(await requestJson(
        this.context,
        pathForSession(this.sessionId, `/turns/${encodeURIComponent(turnId)}/cancel`),
        "POST",
        { cancellation_request_key: cancellationRequestKey, reason: request.reason ?? "user_requested" },
      ))
      if (receipt.cancellationRequestKey !== cancellationRequestKey || receipt.turnId !== turnId) {
        throw new CanonicalE4ClientError({ kind: "protocol", code: "cancellation_receipt_identity_mismatch" })
      }
      return receipt
    } catch (error) {
      if (error instanceof CanonicalE4ClientError && error.failure.kind === "http" && error.failure.status === 409) {
        throw new CanonicalE4ClientError({ kind: "cancellation-conflict", sessionId: this.sessionId, turnId, code: error.failure.code })
      }
      throw error
    }
  }
  async respondPermission(request: RespondPermissionRequest): Promise<PermissionDecisionReceipt> {
    this.assertOpen()
    const requestId = requiredString(String(request.requestId), "permission_decision_request_id") as PermissionRequestId
    const decision = requiredEnum(request.decision, "permission_decision", ["allow", "deny"] as const)
    return decodePermissionDecisionReceipt(
      await requestJson(this.context, pathForSession(this.sessionId, "/command"), "POST", {
        command: "respond_permission",
        payload: { request_id: requestId, response: decision },
      }),
      requestId,
      decision,
    )
  }


  events(request: ObserveSessionRequest = {}): AsyncGenerator<LoggedSessionEvent, void, void> {
    this.assertOpen()
    if (this.streamGenerators.size > 0) {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "observation_already_active" })
    }
    const source = this.eventGenerator(request)
    let wrapper!: AsyncGenerator<LoggedSessionEvent, void, void>
    const self = this
    wrapper = (async function* () {
      try {
        yield* source
      } finally {
        self.streamGenerators.delete(wrapper)
      }
    })()
    const originalReturn = wrapper.return.bind(wrapper)
    wrapper.return = async (value?: void) => {
      try {
        return await originalReturn(value)
      } finally {
        self.streamGenerators.delete(wrapper)
      }
    }
    const originalThrow = wrapper.throw.bind(wrapper)
    wrapper.throw = async (error?: unknown) => {
      try {
        return await originalThrow(error)
      } finally {
        self.streamGenerators.delete(wrapper)
      }
    }
    this.streamGenerators.add(wrapper)
    return wrapper
  }

  private async *eventGenerator(request: ObserveSessionRequest): AsyncGenerator<LoggedSessionEvent, void, void> {
    if (request.signal?.aborted) throw new CanonicalE4ClientError({ kind: "caller-abort" })
    this.assertOpen()
    const controller = new AbortController()
    const { promise: finished, resolve: resolveFinished } = createDeferred<void>()
    this.activeStreams.set(controller, finished)
    const onCallerAbort = () => controller.abort()
    request.signal?.addEventListener("abort", onCallerAbort, { once: true })
    try {
      const query: [string, string][] = [["replay", "true"]]
      if (this.lastAppliedEventId !== null) query.push(["from_id", this.lastAppliedEventId])
      const response = await this.context.fetch(buildUrl(this.context, pathForSession(this.sessionId, "/events"), query), {
        method: "GET",
        headers: {
          Accept: "text/event-stream",
          ...(this.context.authToken ? { Authorization: `Bearer ${this.context.authToken}` } : {}),
          ...(this.lastAppliedEventId === null ? {} : { "Last-Event-ID": this.lastAppliedEventId }),
        },
        signal: controller.signal,
        redirect: "error",
      })
      if (!response.ok) {
        const safe = await parseSafeErrorEnvelope(response, controller, response.status === 409 ? STREAM_ERROR_CODES : [])
        if (response.status === 404) throw new CanonicalE4ClientError({ kind: "session-not-found", sessionId: this.sessionId })
        if (response.status === 409 && safe.code === "resume_window_exceeded") {
          throw new CanonicalE4ClientError({
            kind: "resume-gap",
            code: "resume_window_exceeded",
            lastAppliedEventId: this.lastAppliedEventId,
            lastAppliedSequence: this.lastAppliedSequence,
          })
        }
        throw new CanonicalE4ClientError({ kind: "http", status: response.status, code: safe.code, body: REDACTED_VALUE })
      }
      if (response.body === null) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_stream_body" })
      const terminalTransition = (event: LoggedSessionEvent): { readonly turnId: TurnId; readonly kind: "turn_completed" | "turn_failed" | "turn_cancelled" } | null => {
        switch (event.kind) {
          case "turn_completed": return { turnId: event.turnId, kind: event.kind }
          case "turn_failed": return { turnId: event.turnId, kind: event.kind }
          case "turn_cancelled": return { turnId: event.turnId, kind: event.kind }
          default: return null
        }
      }

      const readerState: ResponseReaderState = { reader: response.body.getReader(), pending: null, released: false }
      const textDecoder = new TextDecoder("utf-8", { fatal: true })
      const pending: RawSseItem[] = []
      let pendingIndex = 0
      let pendingBytes = 0
      let sawOpen = false
      let openHeadSequence = 0
      const failSseLimit = (code: string): never => {
        controller.abort()
        cancelReader(readerState)
        throw new CanonicalE4ClientError({ kind: "protocol", code })
      }
      const sseDecoder = new BoundedSseDecoder((data, eventId) => {
        const itemBytes = utf8ByteLength(data) + (eventId === null ? 0 : utf8ByteLength(eventId))
        if (pending.length - pendingIndex >= MAX_SSE_PENDING_EVENT_COUNT) failSseLimit("sse_pending_event_count_exceeded")
        if (pendingBytes + itemBytes > MAX_SSE_PENDING_EVENT_BYTES) failSseLimit("sse_pending_event_bytes_exceeded")
        pendingBytes += itemBytes
        pending.push({ data, eventId, byteLength: itemBytes })
      }, failSseLimit)
      try {
        while (true) {
          const result = await readReaderAbortably(readerState, controller.signal)
          if (!result.done && result.value.byteLength > MAX_SSE_CHUNK_BYTES) failSseLimit("sse_chunk_too_large")
          if (!result.done && result.value.byteLength === 0) failSseLimit("sse_chunk_no_progress")
          let decoded: string
          try {
            decoded = result.done ? textDecoder.decode() : textDecoder.decode(result.value, { stream: true })
          } catch {
            throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_sse_utf8" })
          }
          if (decoded.length > 0) sseDecoder.feed(decoded)
          if (result.done) sseDecoder.finish()
          while (pendingIndex < pending.length) {
            const item = pending[pendingIndex]
            pendingIndex += 1
            pendingBytes -= item.byteLength
            let raw: unknown
            try { raw = JSON.parse(item.data) as unknown } catch { throw new CanonicalE4ClientError({ kind: "protocol", code: "malformed_sse_json" }) }
            if (!isRawObject(raw)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_sse_envelope" })
            const rawType = requiredString(own(raw, "type"), "stream_event_type")
            const stableCursor = own(raw, "stable_cursor")
            if (stableCursor === false) {
              if (own(raw, "id") !== undefined || own(raw, "seq") !== undefined || item.eventId !== null) {
                throw new CanonicalE4ClientError({ kind: "protocol", code: "control_event_has_cursor" })
              }
              if (rawType === "stream.open") {
                if (sawOpen) throw new CanonicalE4ClientError({ kind: "protocol", code: "duplicate_stream_open" })
                const payload = own(raw, "payload")
                if (!isRawObject(payload)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_stream_open" })
                const facts = parseReplayFacts(payload)
                await validateSessionReplayFacts(facts)
                if (facts.retainedHistory !== "complete" && this.lastAppliedEventId === null) {
                  throw new CanonicalE4ClientError({ kind: "resume-gap", code: "partial_retained_history", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
                }
                if (facts.headSequence < this.lastAppliedSequence || (
                  this.lastAppliedEventId !== null
                  && (
                    facts.earliestRetainedSequence === null
                    || facts.headEventId === null
                    || facts.earliestRetainedSequence > this.lastAppliedSequence
                    || (facts.headSequence === this.lastAppliedSequence && facts.headEventId !== this.lastAppliedEventId)
                  )
                )) {
                  throw new CanonicalE4ClientError({ kind: "resume-gap", code: "cursor_outside_advertised_replay", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
                }
                sawOpen = true
                openHeadSequence = facts.headSequence
                continue
              }
              if (rawType === "stream.gap") {
                throw new CanonicalE4ClientError({ kind: "resume-gap", code: "subscriber_overflow", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
              }
              throw new CanonicalE4ClientError({ kind: "protocol", code: "unsupported_non_cursor_event" })
            }
            if (!sawOpen) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_stream_open" })
            const event = decodeLoggedSessionEvent(raw)
            if (event.sessionId !== this.sessionId) throw new CanonicalE4ClientError({ kind: "protocol", code: "cross_session_event", eventId: event.eventId, sequence: event.sequence })
            if (item.eventId !== null && item.eventId !== String(event.sequence)) throw new CanonicalE4ClientError({ kind: "protocol", code: "sse_sequence_id_mismatch", eventId: event.eventId, sequence: event.sequence })
            const digest = await digestLoggedSessionEvent(event)
            const known = this.retainedDigests.get(event.eventId)
            if (known !== undefined) {
              if (known !== digest) throw new CanonicalE4ClientError({ kind: "protocol", code: "event_id_digest_collision", eventId: event.eventId, sequence: event.sequence })
              continue
            }
            if (event.sequence <= this.lastAppliedSequence) {
              throw new CanonicalE4ClientError({ kind: "resume-gap", code: "unretained_replay", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
            }
            if (event.sequence !== this.lastAppliedSequence + 1) {
              throw new CanonicalE4ClientError({ kind: "resume-gap", code: "sequence_discontinuity", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
            }
            const terminal = terminalTransition(event)
            if (terminal !== null && this.terminalTurns.has(terminal.turnId)) {
              throw new CanonicalE4ClientError({ kind: "protocol", code: "duplicate_terminal_transition", eventId: event.eventId, sequence: event.sequence })
            }
            yield event
            this.lastAppliedEventId = event.eventId
            this.lastAppliedSequence = event.sequence
            if (terminal !== null) this.terminalTurns.set(terminal.turnId, terminal.kind)
            this.retainedDigests.set(event.eventId, digest)
            while (this.retainedDigests.size > REPLAY_RETENTION_MAX_EVENTS) {
              const oldest = this.retainedDigests.keys().next().value as EventId | undefined
              if (oldest === undefined) break
              this.retainedDigests.delete(oldest)
            }
          }
          pending.length = 0
          pendingIndex = 0
          pendingBytes = 0
          if (result.done) break
        }
        if (!sawOpen && !this.closed) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_stream_open" })
        if (sawOpen && !this.closed && this.lastAppliedSequence < openHeadSequence) {
          throw new CanonicalE4ClientError({ kind: "resume-gap", code: "stream_truncated_before_open_head", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
        }
      } finally {
        cancelReader(readerState)
        releaseReaderWhenIdle(readerState)
      }
    } catch (error) {
      if (this.closed && isAbortError(error)) return
      if (request.signal?.aborted && isAbortError(error)) throw new CanonicalE4ClientError({ kind: "caller-abort" })
      if (error instanceof CanonicalE4ClientError) throw error
      if (isAbortError(error)) throw new CanonicalE4ClientError({ kind: "caller-abort" })
      throw new CanonicalE4ClientError({ kind: "http", status: 0, code: null, body: REDACTED_VALUE })
    } finally {
      request.signal?.removeEventListener("abort", onCallerAbort)
      this.activeStreams.delete(controller)
      resolveFinished(undefined)
    }
  }

  close(): Promise<void> {
    if (this.closePromise !== null) return this.closePromise
    this.closed = true
    const streams = [...this.activeStreams.entries()]
    const generators = [...this.streamGenerators]
    this.closePromise = (async () => {
      for (const [controller] of streams) controller.abort()
      await Promise.all(generators.map((generator) => generator.return().catch(() => ({ done: true, value: undefined }))))
      await Promise.all(streams.map(([, finished]) => finished))
    })()
    return this.closePromise
  }

  private assertOpen(): void {
    if (this.closed) throw new CanonicalE4ClientError({ kind: "protocol", code: "session_locally_closed" })
  }
}

export const createCanonicalE4Client = (config: CanonicalE4ClientConfig): CanonicalE4Client => {
  const context: RequestContext = {
    fetch: config.fetch ?? globalThis.fetch,
    baseUrl: config.baseUrl,
    authToken: config.authToken,
    timeoutMs: config.requestTimeoutMs ?? 30_000,
  }
  const open = async (sessionId: SessionId): Promise<OpenedSession> => {
    const runtime = new RuntimeSession(context, sessionId)
    await runtime.snapshot()
    return runtime
  }
  return {
    create: async (request) => {
      if (!request.configPath) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_config_path" })
      const response = await requestJson(context, "/v1/sessions", "POST", createRequestBody(request))
      if (!isRawObject(response)) throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_create_response" })
      const sessionId = requiredString(own(response, "session_id"), "session_id") as SessionId
      return open(sessionId)
    },
    attach: async (request) => {
      const sessionId = String(request.sessionId)
      if (!sessionId) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_session_id" })
      return open(sessionId as SessionId)
    },
  }
}
