import { createParser, type EventSourceParseCallback, type ParsedEvent, type ReconnectInterval } from "eventsource-parser"

declare const sessionIdBrand: unique symbol
declare const inputIdBrand: unique symbol
declare const turnIdBrand: unique symbol
declare const cancellationRequestIdBrand: unique symbol
declare const cancellationRequestKeyBrand: unique symbol
declare const clientMessageIdBrand: unique symbol
declare const eventIdBrand: unique symbol
declare const replayDigestBrand: unique symbol

export type SessionId = string & { readonly [sessionIdBrand]: true }
export type InputId = string & { readonly [inputIdBrand]: true }
export type TurnId = string & { readonly [turnIdBrand]: true }
export type CancellationRequestId = string & { readonly [cancellationRequestIdBrand]: true }
export type CancellationRequestKey = string & { readonly [cancellationRequestKeyBrand]: true }
export type ClientMessageId = string & { readonly [clientMessageIdBrand]: true }
export type EventId = string & { readonly [eventIdBrand]: true }
export type ReplayContractDigest = string & { readonly [replayDigestBrand]: true }

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
export type TurnStartedEvent = TurnOwnedLoggedEvent<"turn_started", ExactEmptyPayload | CanonicalJsonObject>
export type AssistantTextDeltaEvent = TurnOwnedLoggedEvent<"assistant_text_delta", { readonly text: string }>
export type AssistantTextCompletedEvent = TurnOwnedLoggedEvent<"assistant_text_completed", { readonly text: string | null }>
export type TurnCompletedEvent = TurnOwnedLoggedEvent<"turn_completed", ExactEmptyPayload>
export type TurnFailedEvent = TurnOwnedLoggedEvent<"turn_failed", { readonly error: RedactedTurnError }>
export type TurnCancelledEvent = TurnOwnedLoggedEvent<"turn_cancelled", { readonly reason: CancellationReason }>
export type ConversationCompactionStartedEvent = TurnOwnedLoggedEvent<"conversation_compaction_started", CanonicalJsonObject>
export type ConversationCompactionCompletedEvent = TurnOwnedLoggedEvent<"conversation_compaction_completed", CanonicalJsonObject>
export type AssistantMessageStartedEvent = TurnOwnedLoggedEvent<"assistant_message_started", CanonicalJsonObject>
export type AssistantReasoningDeltaEvent = TurnOwnedLoggedEvent<"assistant_reasoning_delta", { readonly text: string }>
export type AssistantThoughtSummaryDeltaEvent = TurnOwnedLoggedEvent<"assistant_thought_summary_delta", { readonly text: string }>
export type ToolExecutionStartedEvent = TurnOwnedLoggedEvent<"tool_execution_started", CanonicalJsonObject>
export type ToolExecutionStdoutDeltaEvent = TurnOwnedLoggedEvent<"tool_execution_stdout_delta", CanonicalJsonObject>
export type ToolExecutionStderrDeltaEvent = TurnOwnedLoggedEvent<"tool_execution_stderr_delta", CanonicalJsonObject>
export type ToolExecutionCompletedEvent = TurnOwnedLoggedEvent<"tool_execution_completed", CanonicalJsonObject>
export type ToolCalledEvent = TurnOwnedLoggedEvent<"tool_called", CanonicalJsonObject>
export type ToolResultObservedEvent = TurnOwnedLoggedEvent<"tool_result_observed", CanonicalJsonObject>
export type PermissionRequestedEvent = TurnOwnedLoggedEvent<"permission_requested", CanonicalJsonObject>
export type PermissionRespondedEvent = TurnOwnedLoggedEvent<"permission_responded", CanonicalJsonObject>
export type CheckpointListObservedEvent = LoggedEventBase<"checkpoint_list_observed", CanonicalJsonObject>
export type CheckpointRestoredEvent = LoggedEventBase<"checkpoint_restored", CanonicalJsonObject>
export type SkillsCatalogObservedEvent = LoggedEventBase<"skills_catalog_observed", CanonicalJsonObject>
export type SkillsSelectionObservedEvent = LoggedEventBase<"skills_selection_observed", CanonicalJsonObject>
export type CTreeNodeObservedEvent = TurnOwnedLoggedEvent<"ctree_node_observed", CanonicalJsonObject>
export type CTreeSnapshotObservedEvent = LoggedEventBase<"ctree_snapshot_observed", CanonicalJsonObject>
export type TaskEventObservedEvent = TurnOwnedLoggedEvent<"task_event_observed", CanonicalJsonObject>
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
  events(request?: ObserveSessionRequest): AsyncGenerator<LoggedSessionEvent, void, void>
  close(): Promise<void>
}

export interface CanonicalE4Client {
  create(request: CreateSessionRequest): Promise<OpenedSession>
  attach(request: AttachSessionRequest): Promise<OpenedSession>
}


type RawObject = { readonly [key: string]: unknown }

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
      payload: isRawObject(payload) && Object.keys(payload).length === 0 ? decodeExactEmptyPayload(payload) : jsonPayload("turn_started"),
    }
    case "conversation.compaction.start": return { ...turnBase(), kind: "conversation_compaction_started", payload: jsonPayload("conversation_compaction_started") }
    case "conversation.compaction.end": return { ...turnBase(), kind: "conversation_compaction_completed", payload: jsonPayload("conversation_compaction_completed") }
    case "assistant.message.start": return { ...turnBase(), kind: "assistant_message_started", payload: jsonPayload("assistant_message_started") }
    case "assistant.message.delta":
    case "assistant_delta":
      return { ...turnBase(), kind: "assistant_text_delta", payload: parseTextPayload(payload, "assistant_text_delta") }
    case "assistant.message.end":
    case "assistant_message":
      return { ...turnBase(), kind: "assistant_text_completed", payload: parseOptionalTextPayload(payload, "assistant_text_completed") }
    case "assistant.reasoning.delta": return { ...turnBase(), kind: "assistant_reasoning_delta", payload: parseTextPayload(payload, "assistant_reasoning_delta") }
    case "assistant.thought_summary.delta": return { ...turnBase(), kind: "assistant_thought_summary_delta", payload: parseTextPayload(payload, "assistant_thought_summary_delta") }
    case "tool.exec.start": return { ...turnBase(), kind: "tool_execution_started", payload: jsonPayload("tool_execution_started") }
    case "tool.exec.stdout.delta": return { ...turnBase(), kind: "tool_execution_stdout_delta", payload: jsonPayload("tool_execution_stdout_delta") }
    case "tool.exec.stderr.delta": return { ...turnBase(), kind: "tool_execution_stderr_delta", payload: jsonPayload("tool_execution_stderr_delta") }
    case "tool.exec.end": return { ...turnBase(), kind: "tool_execution_completed", payload: jsonPayload("tool_execution_completed") }
    case "tool_call": return { ...turnBase(), kind: "tool_called", payload: jsonPayload("tool_called") }
    case "tool.result":
    case "tool_result":
      return { ...turnBase(), kind: "tool_result_observed", payload: jsonPayload("tool_result_observed") }
    case "permission_request": return { ...turnBase(), kind: "permission_requested", payload: jsonPayload("permission_requested") }
    case "permission_response": return { ...turnBase(), kind: "permission_responded", payload: jsonPayload("permission_responded") }
    case "checkpoint_list": return { ...base, kind: "checkpoint_list_observed", payload: jsonPayload("checkpoint_list_observed") }
    case "checkpoint_restored": return { ...base, kind: "checkpoint_restored", payload: jsonPayload("checkpoint_restored") }
    case "skills_catalog": return { ...base, kind: "skills_catalog_observed", payload: jsonPayload("skills_catalog_observed") }
    case "skills_selection": return { ...base, kind: "skills_selection_observed", payload: jsonPayload("skills_selection_observed") }
    case "ctree_node": return { ...turnBase(), kind: "ctree_node_observed", payload: jsonPayload("ctree_node_observed") }
    case "ctree_snapshot": return { ...base, kind: "ctree_snapshot_observed", payload: jsonPayload("ctree_snapshot_observed") }
    case "task_event": return { ...turnBase(), kind: "task_event_observed", payload: jsonPayload("task_event_observed") }
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
  readonly detail: RawObject | null
  readonly turnId: TurnId | null
}

const parseSafeErrorEnvelope = async (response: Response): Promise<SafeErrorEnvelope> => {
  const value = await response.json().catch(() => null) as unknown
  if (!isRawObject(value)) return { code: null, detail: null, turnId: null }
  const rawCode = own(value, "error")
  const code = typeof rawCode === "string" && /^[a-z0-9_.-]{1,128}$/.test(rawCode) ? rawCode : null
  const detail = isRawObject(own(value, "detail")) ? own(value, "detail") as RawObject : null
  const rawTurnId = detail === null ? null : own(detail, "turn_id")
  const turnId = typeof rawTurnId === "string" && rawTurnId.length > 0 ? rawTurnId as TurnId : null
  return { code, detail, turnId }
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
  const url = new URL(path, context.baseUrl.endsWith("/") ? context.baseUrl : `${context.baseUrl}/`)
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
      const safe = await parseSafeErrorEnvelope(response)
      if (response.status === 404) {
        const sessionId = path.split("/")[3] ?? "unknown"
        throw new CanonicalE4ClientError({ kind: "session-not-found", sessionId: sessionId as SessionId })
      }
      throw new CanonicalE4ClientError({ kind: "http", status: response.status, code: safe.code, body: REDACTED_VALUE, ...(safe.turnId === null ? {} : { turnId: safe.turnId }) })
    }
    return await response.json().catch(() => {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_json_response" })
    }) as unknown
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
      const safe = await parseSafeErrorEnvelope(response)
      if (response.status === 404) throw new CanonicalE4ClientError({ kind: "session-not-found", sessionId })
      throw new CanonicalE4ClientError({ kind: "http", status: response.status, code: safe.code, body: REDACTED_VALUE })
    }
    const value = await response.json().catch(() => {
      throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_attachment_upload_response" })
    }) as unknown
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
    let resolveFinished!: () => void
    const finished = new Promise<void>((resolve) => { resolveFinished = resolve })
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
        const safe = await parseSafeErrorEnvelope(response)
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

      const reader = response.body.getReader()
      const decoder = new TextDecoder("utf-8", { fatal: true })
      const pending: RawSseItem[] = []
      let pendingIndex = 0
      let sseTail = ""
      let sawOpen = false
      let openHeadSequence = 0
      const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
        if (event.type === "event" && "data" in event) pending.push({ data: event.data, eventId: event.id ?? null })
      }) as EventSourceParseCallback)
      const feedSse = (text: string): void => {
        parser.feed(text)
        const normalizedTail = `${sseTail}${text}`.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
        const boundary = normalizedTail.lastIndexOf("\n\n")
        sseTail = boundary === -1 ? normalizedTail : normalizedTail.slice(boundary + 2)
      }
      try {
        while (true) {
          const result = await reader.read()
          let decoded: string
          try {
            decoded = result.done ? decoder.decode() : decoder.decode(result.value, { stream: true })
          } catch {
            throw new CanonicalE4ClientError({ kind: "protocol", code: "invalid_sse_utf8" })
          }
          if (decoded.length > 0) feedSse(decoded)
          while (pendingIndex < pending.length) {
            const item = pending[pendingIndex]
            pendingIndex += 1
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
                const payload = own(raw, "payload")
                const code = isRawObject(payload) && typeof own(payload, "code") === "string" ? own(payload, "code") as string : "subscriber_overflow"
                throw new CanonicalE4ClientError({ kind: "resume-gap", code, lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
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
          if (result.done) {
            if (sseTail.length > 0) throw new CanonicalE4ClientError({ kind: "protocol", code: "truncated_sse_frame" })
            break
          }
        }
        if (!sawOpen && !this.closed) throw new CanonicalE4ClientError({ kind: "protocol", code: "missing_stream_open" })
        if (sawOpen && !this.closed && this.lastAppliedSequence < openHeadSequence) {
          throw new CanonicalE4ClientError({ kind: "resume-gap", code: "stream_truncated_before_open_head", lastAppliedEventId: this.lastAppliedEventId, lastAppliedSequence: this.lastAppliedSequence })
        }
      } finally {
        reader.releaseLock()
        await response.body.cancel().catch(() => undefined)
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
      resolveFinished()
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
