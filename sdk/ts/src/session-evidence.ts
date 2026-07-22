import {
  REDACTED_VALUE,
  sha256Bytes,
  type CanonicalE4Failure,
  type CancellationReason,
  type CancellationReceipt,
  type EventId,
  type InputId,
  type LoggedSessionEvent,
  type SessionId,
  type SessionSnapshot,
  type SubmitReceipt,
  type TurnId,
} from "./session-runtime.js"

export const SESSION_EVIDENCE_SCHEMA_VERSION = "bb.p30.session_evidence.v1" as const
export const SESSION_EVIDENCE_REDACTION_POLICY_VERSION = "bb.p30.session_evidence.redaction.v1" as const
export const SESSION_EVIDENCE_BOUNDS = Object.freeze({
  maxDepth: 8,
  maxCollectionEntries: 64,
  maxInspectedNodes: 4096,
  maxFindings: 128,
  maxStringBytes: 4096,
})

export type SessionEvidenceClass = "static-fixture" | "runtime-capture" | "product-journey"
export type SessionJourneyId =
  | "P30-SESSION-NEW-TURN-RECONNECT"
  | "P30-SESSION-ATTACH-ACTIVE"
  | "P30-SESSION-TARGETED-CANCEL"

export type SensitiveValueCategory =
  | "credential"
  | "header"
  | "url"
  | "path"
  | "alias"
  | "account-id"
  | "body"
  | "event-payload"
  | "malformed-body"
  | "error-serialization"
  | "cycle"
  | "depth-limit"
  | "entry-limit"
  | "string-limit"

export interface SensitiveValueFinding {
  readonly location: string
  readonly category: SensitiveValueCategory
}

export interface SensitiveValueDetection {
  readonly policyVersion: typeof SESSION_EVIDENCE_REDACTION_POLICY_VERSION
  readonly inspectedNodes: number
  readonly truncated: boolean
  readonly findings: readonly SensitiveValueFinding[]
}

export interface DisplayTextEvidence {
  readonly kind: "user-text" | "assistant-text"
  readonly text: string | typeof REDACTED_VALUE
  readonly redacted: boolean
  readonly categories: readonly SensitiveValueCategory[]
}

export interface CancellationDisplayEvidence {
  readonly kind: "cancellation-status"
  readonly reason: CancellationReason
}

export interface ProtocolErrorDisplayEvidence {
  readonly kind: "protocol-error"
  readonly code: string
}

export interface GapErrorDisplayEvidence {
  readonly kind: "gap-error"
  readonly code: string
}

export interface LocalCloseDisplayEvidence {
  readonly kind: "local-close"
  readonly status: "closed-locally"
}

export type SessionDisplayEvidence =
  | DisplayTextEvidence
  | CancellationDisplayEvidence
  | ProtocolErrorDisplayEvidence
  | GapErrorDisplayEvidence
  | LocalCloseDisplayEvidence

export interface SnapshotEvidence {
  readonly recordKind: "snapshot"
  readonly sessionId: SessionId
  readonly status: SessionSnapshot["status"]
  readonly turnAdmission: SessionSnapshot["turnAdmission"]
  readonly activeTurnId: TurnId | null
  readonly queuedTurnCount: number
  readonly cursor: {
    readonly earliestRetainedSequence: number | null
    readonly earliestRetainedEventId: EventId | null
    readonly headSequence: number
    readonly headEventId: EventId | null
    readonly retainedHistory: SessionSnapshot["retainedHistory"]
    readonly replayContractDigest: string
  }
  readonly terminalTurns: readonly {
    readonly inputId: InputId
    readonly turnId: TurnId
    readonly outcome: SessionSnapshot["terminalTurns"][number]["outcome"]
    readonly originalDisposition: SessionSnapshot["terminalTurns"][number]["originalDisposition"]
  }[]
}

export interface SubmitEvidence {
  readonly recordKind: "submit"
  readonly clientMessageId: string
  readonly inputId: InputId
  readonly turnId: TurnId
  readonly disposition: SubmitReceipt["disposition"]
  readonly originalDisposition: SubmitReceipt["originalDisposition"]
}

export interface CancellationEvidence {
  readonly recordKind: "cancellation"
  readonly cancellationRequestId: string
  readonly cancellationRequestKey: string
  readonly inputId: InputId
  readonly turnId: TurnId
  readonly disposition: CancellationReceipt["disposition"]
  readonly originalDisposition: CancellationReceipt["originalDisposition"]
}

export interface EventEvidence {
  readonly recordKind: "event"
  readonly eventId: EventId
  readonly sequence: number
  readonly sessionId: SessionId
  readonly inputId: InputId | null
  readonly turnId: TurnId | null
  readonly occurredAtMs: number
  readonly eventKind: LoggedSessionEvent["kind"]
  readonly display: SessionDisplayEvidence | null
}

export interface StableCursorEvidence {
  readonly recordKind: "stable-cursor"
  readonly sessionId: SessionId
  readonly eventId: EventId
  readonly sequence: number
  readonly resumeMode: "exclusive"
  readonly gapObserved: boolean
  readonly duplicateApplied: boolean
}

export type FailureDetailsEvidence =
  | { readonly kind: "http"; readonly status: number; readonly code: string | null; readonly turnId: TurnId | null }
  | { readonly kind: "timeout" }
  | { readonly kind: "caller-abort" }
  | { readonly kind: "protocol"; readonly code: string; readonly eventId: EventId | null; readonly sequence: number | null }
  | { readonly kind: "resume-gap"; readonly code: string; readonly lastAppliedEventId: EventId | null; readonly lastAppliedSequence: number }
  | { readonly kind: "session-not-found"; readonly sessionId: SessionId }
  | { readonly kind: "admission-conflict"; readonly sessionId: SessionId; readonly code: string | null }
  | { readonly kind: "idempotency-conflict"; readonly sessionId: SessionId; readonly turnId: TurnId | null }
  | { readonly kind: "cancellation-conflict"; readonly sessionId: SessionId; readonly turnId: TurnId; readonly code: string | null }
  | { readonly kind: "turn-failed"; readonly sessionId: SessionId; readonly inputId: InputId; readonly turnId: TurnId; readonly code: string }
  | { readonly kind: "unknown-error" }

export interface FailureEvidence {
  readonly recordKind: "failure"
  readonly details: FailureDetailsEvidence
  readonly display: ProtocolErrorDisplayEvidence | GapErrorDisplayEvidence | null
}

export interface LocalCloseEvidence {
  readonly recordKind: "local-close"
  readonly sessionId: SessionId
  readonly backendSessionDeletion: "not-requested"
  readonly display: LocalCloseDisplayEvidence
}

export type SessionEvidenceRecord =
  | SnapshotEvidence
  | SubmitEvidence
  | CancellationEvidence
  | EventEvidence
  | StableCursorEvidence
  | FailureEvidence
  | LocalCloseEvidence

export type SessionEvidenceProvenance =
  | {
      readonly kind: "static-fixture"
      readonly sourceTicket: "bb-89n.15"
    }
  | {
      readonly kind: "runtime-capture"
      readonly sourceTicket: "bb-89n.14"
      readonly captureSchemaVersion: "bb.p30.bb89n14.gate_evidence.v1"
      readonly captureSha256: string
      readonly backendCommit: string
      readonly clientCommit: string
      readonly configurationSha256: string
    }
  | {
      readonly kind: "product-journey"
      readonly sourceTicket: "bb-89n.16"
      readonly candidateCommit: string
      readonly candidateTree: string
      readonly backendCommit: string
      readonly clientCommit: string
      readonly configurationSha256: string
    }

export interface SessionEvidenceBundle {
  readonly schemaVersion: typeof SESSION_EVIDENCE_SCHEMA_VERSION
  readonly redactionPolicyVersion: typeof SESSION_EVIDENCE_REDACTION_POLICY_VERSION
  readonly evidenceClass: SessionEvidenceClass
  readonly journeyId: SessionJourneyId
  readonly provenance: SessionEvidenceProvenance
  readonly records: readonly SessionEvidenceRecord[]
}

export interface TrustedProductJourneyProvenance {
  readonly candidateCommit: string
  readonly candidateTree: string
  readonly backendCommit: string
  readonly clientCommit: string
  readonly configurationBytes: Uint8Array
}

const utf8ByteLength = (value: string): number => new TextEncoder().encode(value).byteLength
const exceedsStringBound = (value: string): boolean =>
  value.length > SESSION_EVIDENCE_BOUNDS.maxStringBytes || utf8ByteLength(value) > SESSION_EVIDENCE_BOUNDS.maxStringBytes
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const CREDENTIAL_KEY = /(?:^|[_-])(api[_-]?key|access[_-]?token|refresh[_-]?token|token|auth(?:orization)?|bearer|cookie|password|passwd|secret|credential|private[_-]?key)(?:$|[_-])/i
const HEADER_KEY = /^(?:headers?|authorization|proxy-authorization|cookie|set-cookie|x-api-key)$/i
const ACCOUNT_ID_KEY = /(?:^|[_-])(?:account[_-]?id|organization[_-]?id|org[_-]?id)(?:$|[_-])/i
const ALIAS_KEY = /(?:^|[_-])alias(?:$|[_-])/i
const BODY_KEY = /^(?:body|raw_body|rawBody|request_body|requestBody|response_body|responseBody)$/
const EVENT_PAYLOAD_KEY = /^(?:payload|event_payload|eventPayload|raw_event|rawEvent)$/
const URL_VALUE = /(?:https?|wss?|file):\/\//i
const SCHEMELESS_URL_VALUE = /\b(?:localhost(?::\d{1,5})?|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d{1,5})?)(?:\/\S*)?/i
const PATH_VALUE = /(?:^|[\s"'=(])(?:~\/\S+|[A-Za-z]:[\\/]\S+|\\\\\S+|\/(?!\/)[^\s/]\S*)/
const CREDENTIAL_VALUE = /(?:\bBearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk|ghp|gho|github_pat|xox[abprs])[-_][A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})/i
const HEADER_VALUE = /\b(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*\S+/i
const ACCOUNT_ID_VALUE = /\b(?:ChatGPT[-_ ]Account[-_ ]Id|account[_ -]?id|organization[_ -]?id|org[_ -]?id)\s*[:=]\s*\S+/i
const ALIAS_VALUE = /\b(?:account[_ -]?alias|alias)\s*[:=]\s*\S+/i
const BODY_VALUE = /\b(?:response[_ -]?body|request[_ -]?body|raw[_ -]?body)\s*[:=]/i
const EVENT_PAYLOAD_VALUE = /\b(?:event[_ -]?payload|raw[_ -]?event)\s*[:=]/i
const ERROR_VALUE = /(?:^|\s)(?:Error|TypeError|RangeError|ReferenceError|SyntaxError|AggregateError):\s/
const HEX_TOKEN_VALUE = /(?:^|[^A-Za-z0-9])[A-Fa-f0-9]{48,}(?:$|[^A-Za-z0-9])/

const categoryForKey = (key: string): SensitiveValueCategory | null => {
  if (HEADER_KEY.test(key)) return "header"
  if (ACCOUNT_ID_KEY.test(key)) return "account-id"
  if (CREDENTIAL_KEY.test(key)) return "credential"
  if (ALIAS_KEY.test(key)) return "alias"
  if (BODY_KEY.test(key)) return "body"
  if (EVENT_PAYLOAD_KEY.test(key)) return "event-payload"
  return null
}

export const detectSensitiveValues = (value: unknown): SensitiveValueDetection => {
  const findings: SensitiveValueFinding[] = []
  const active = new WeakSet<object>()
  let inspectedNodes = 0
  let truncated = false

  const add = (location: string, category: SensitiveValueCategory): void => {
    if (findings.length >= SESSION_EVIDENCE_BOUNDS.maxFindings) {
      truncated = true
      return
    }
    findings.push({ location, category })
  }

  const visit = (candidate: unknown, depth: number, location: string): void => {
    if (inspectedNodes >= SESSION_EVIDENCE_BOUNDS.maxInspectedNodes) {
      truncated = true
      return
    }
    inspectedNodes += 1
    if (depth > SESSION_EVIDENCE_BOUNDS.maxDepth) {
      truncated = true
      add(location, "depth-limit")
      return
    }
    if (typeof candidate === "string") {
      if (exceedsStringBound(candidate)) {
        truncated = true
        add(location, "string-limit")
        return
      }
      if (URL_VALUE.test(candidate) || SCHEMELESS_URL_VALUE.test(candidate)) add(location, "url")
      if (PATH_VALUE.test(candidate)) add(location, "path")
      if (CREDENTIAL_VALUE.test(candidate) || HEX_TOKEN_VALUE.test(candidate)) add(location, "credential")
      if (HEADER_VALUE.test(candidate)) add(location, "header")
      if (ACCOUNT_ID_VALUE.test(candidate)) add(location, "account-id")
      if (ALIAS_VALUE.test(candidate)) add(location, "alias")
      if (BODY_VALUE.test(candidate)) add(location, "body")
      if (EVENT_PAYLOAD_VALUE.test(candidate)) add(location, "event-payload")
      if (ERROR_VALUE.test(candidate)) add(location, "error-serialization")
      return
    }
    if (typeof candidate !== "object" || candidate === null) return
    if (candidate instanceof Error) {
      add(location, "error-serialization")
      return
    }
    if (!Array.isArray(candidate)) {
      const prototype = Object.getPrototypeOf(candidate)
      if (prototype !== Object.prototype && prototype !== null) {
        truncated = true
        add(location, "error-serialization")
        return
      }
    }
    if (active.has(candidate)) {
      add(location, "cycle")
      return
    }
    active.add(candidate)
    const entries: (readonly [string, unknown])[] = []
    let entryLimitExceeded = false
    if (Array.isArray(candidate)) {
      entryLimitExceeded = candidate.length > SESSION_EVIDENCE_BOUNDS.maxCollectionEntries
      const length = Math.min(candidate.length, SESSION_EVIDENCE_BOUNDS.maxCollectionEntries)
      for (let index = 0; index < length; index += 1) entries.push([String(index), candidate[index]])
    } else {
      const objectCandidate = candidate as Record<string, unknown>
      for (const key in objectCandidate) {
        if (!Object.prototype.hasOwnProperty.call(objectCandidate, key)) continue
        if (entries.length >= SESSION_EVIDENCE_BOUNDS.maxCollectionEntries) {
          entryLimitExceeded = true
          break
        }
        entries.push([key, objectCandidate[key]])
      }
      entries.sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    }
    if (entryLimitExceeded) {
      truncated = true
      add(location, "entry-limit")
    }
    for (let index = 0; index < entries.length; index += 1) {
      const [key, entry] = entries[index]!
      const childLocation = `${location}/${index}`
      if (!Array.isArray(candidate)) {
        const keyCategory = categoryForKey(key)
        if (keyCategory !== null) {
          add(childLocation, keyCategory)
          if (keyCategory === "body" && typeof entry === "string") {
            if (exceedsStringBound(entry)) {
              truncated = true
              add(childLocation, "string-limit")
            } else {
              try {
                JSON.parse(entry)
              } catch {
                add(childLocation, "malformed-body")
              }
            }
          }
          continue
        }
      }
      visit(entry, depth + 1, childLocation)
    }
    active.delete(candidate)
  }

  visit(value, 0, "$")
  return Object.freeze({
    policyVersion: SESSION_EVIDENCE_REDACTION_POLICY_VERSION,
    inspectedNodes,
    truncated,
    findings: Object.freeze(findings.map((finding) => Object.freeze(finding))),
  })
}

const uniqueCategories = (detection: SensitiveValueDetection): readonly SensitiveValueCategory[] =>
  Object.freeze([...new Set(detection.findings.map((finding) => finding.category))].sort())

function validatedRecord<T extends SessionEvidenceRecord>(record: T): T {
  assertRecord(record)
  return record
}

export const projectDisplayText = (
  kind: DisplayTextEvidence["kind"],
  text: string,
): DisplayTextEvidence => {
  const detection = detectSensitiveValues(text)
  const redacted = text === REDACTED_VALUE || detection.findings.length > 0 || detection.truncated
  return Object.freeze({
    kind,
    text: redacted ? REDACTED_VALUE : text,
    redacted,
    categories: uniqueCategories(detection),
  })
}

export const projectSnapshotEvidence = (snapshot: SessionSnapshot): SnapshotEvidence => validatedRecord({
  recordKind: "snapshot",
  sessionId: snapshot.sessionId,
  status: snapshot.status,
  turnAdmission: snapshot.turnAdmission,
  activeTurnId: snapshot.activeTurnId,
  queuedTurnCount: snapshot.queuedTurnCount,
  cursor: {
    earliestRetainedSequence: snapshot.earliestRetainedSequence,
    earliestRetainedEventId: snapshot.earliestRetainedEventId,
    headSequence: snapshot.headSequence,
    headEventId: snapshot.headEventId,
    retainedHistory: snapshot.retainedHistory,
    replayContractDigest: String(snapshot.sessionReplayContractDigest),
  },
  terminalTurns: snapshot.terminalTurns.map((turn) => ({
    inputId: turn.inputId,
    turnId: turn.turnId,
    outcome: turn.outcome,
    originalDisposition: turn.originalDisposition,
  })),
})

export const projectSubmitEvidence = (receipt: SubmitReceipt): SubmitEvidence => validatedRecord({
  recordKind: "submit",
  clientMessageId: String(receipt.clientMessageId),
  inputId: receipt.inputId,
  turnId: receipt.turnId,
  disposition: receipt.disposition,
  originalDisposition: receipt.originalDisposition,
})

export const projectCancellationEvidence = (receipt: CancellationReceipt): CancellationEvidence => validatedRecord({
  recordKind: "cancellation",
  cancellationRequestId: String(receipt.cancellationRequestId),
  cancellationRequestKey: String(receipt.cancellationRequestKey),
  inputId: receipt.inputId,
  turnId: receipt.turnId,
  disposition: receipt.disposition,
  originalDisposition: receipt.originalDisposition,
})

export const projectEventEvidence = (event: LoggedSessionEvent): EventEvidence => {
  let display: SessionDisplayEvidence | null = null
  if (event.kind === "input_observed") display = projectDisplayText("user-text", event.payload.text)
  else if (event.kind === "assistant_text_delta" || event.kind === "assistant_text_completed") {
    if (event.payload.text !== null) display = projectDisplayText("assistant-text", event.payload.text)
  } else if (event.kind === "turn_cancelled") display = { kind: "cancellation-status", reason: event.payload.reason }
  return validatedRecord({
    recordKind: "event",
    eventId: event.eventId,
    sequence: event.sequence,
    sessionId: event.sessionId,
    inputId: event.inputId,
    turnId: event.turnId,
    occurredAtMs: event.occurredAtMs,
    eventKind: event.kind,
    display,
  })
}

export const createStableCursorEvidence = (
  sessionId: SessionId,
  eventId: EventId,
  sequence: number,
  result: { readonly gapObserved: boolean; readonly duplicateApplied: boolean },
): StableCursorEvidence => validatedRecord({
  recordKind: "stable-cursor",
  sessionId,
  eventId,
  sequence,
  resumeMode: "exclusive",
  gapObserved: result.gapObserved,
  duplicateApplied: result.duplicateApplied,
})

const safeFailureCode = (code: string | null): string | null => {
  if (code === null) return null
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(code)) return "redacted_error_code"
  const detection = detectSensitiveValues(code)
  return detection.findings.length === 0 && !detection.truncated ? code : "redacted_error_code"
}

export const projectFailureEvidence = (failure: CanonicalE4Failure): FailureEvidence => {
  switch (failure.kind) {
    case "http":
      return validatedRecord({ recordKind: "failure", details: { kind: "http", status: failure.status, code: safeFailureCode(failure.code), turnId: failure.turnId ?? null }, display: null })
    case "timeout":
    case "caller-abort":
      return validatedRecord({ recordKind: "failure", details: { kind: failure.kind }, display: null })
    case "protocol": {
      const code = safeFailureCode(failure.code) ?? "redacted_error_code"
      return validatedRecord({
        recordKind: "failure",
        details: { kind: "protocol", code, eventId: failure.eventId ?? null, sequence: failure.sequence ?? null },
        display: { kind: "protocol-error", code },
      })
    }
    case "resume-gap": {
      const code = safeFailureCode(failure.code) ?? "redacted_error_code"
      return validatedRecord({
        recordKind: "failure",
        details: { kind: "resume-gap", code, lastAppliedEventId: failure.lastAppliedEventId, lastAppliedSequence: failure.lastAppliedSequence },
        display: { kind: "gap-error", code },
      })
    }
    case "session-not-found":
      return validatedRecord({ recordKind: "failure", details: { kind: failure.kind, sessionId: failure.sessionId }, display: null })
    case "admission-conflict":
      return validatedRecord({ recordKind: "failure", details: { kind: failure.kind, sessionId: failure.sessionId, code: safeFailureCode(failure.code) }, display: null })
    case "idempotency-conflict":
      return validatedRecord({ recordKind: "failure", details: { kind: failure.kind, sessionId: failure.sessionId, turnId: failure.turnId }, display: null })
    case "cancellation-conflict":
      return validatedRecord({ recordKind: "failure", details: { kind: failure.kind, sessionId: failure.sessionId, turnId: failure.turnId, code: safeFailureCode(failure.code) }, display: null })
    case "turn-failed":
      return validatedRecord({
        recordKind: "failure",
        details: { kind: failure.kind, sessionId: failure.sessionId, inputId: failure.inputId, turnId: failure.turnId, code: safeFailureCode(failure.error.code) ?? "redacted_error_code" },
        display: null,
      })
  }
}

export const projectUnknownErrorEvidence = (_error: unknown): FailureEvidence => validatedRecord({
  recordKind: "failure",
  details: { kind: "unknown-error" },
  display: null,
})

export const createLocalCloseEvidence = (sessionId: SessionId): LocalCloseEvidence => validatedRecord({
  recordKind: "local-close",
  sessionId,
  backendSessionDeletion: "not-requested",
  display: { kind: "local-close", status: "closed-locally" },
})

const DIGEST = /^sha256:[a-f0-9]{64}$/
const COMMIT = /^[a-f0-9]{40}$/
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/
const SAFE_CODE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const LOGGED_SESSION_EVENT_KINDS: readonly LoggedSessionEvent["kind"][] = [
  "input_observed",
  "turn_started",
  "assistant_text_delta",
  "assistant_text_completed",
  "turn_completed",
  "turn_failed",
  "turn_cancelled",
  "conversation_compaction_started",
  "conversation_compaction_completed",
  "assistant_message_started",
  "assistant_reasoning_delta",
  "assistant_thought_summary_delta",
  "tool_execution_started",
  "tool_execution_stdout_delta",
  "tool_execution_stderr_delta",
  "tool_execution_completed",
  "tool_called",
  "tool_result_observed",
  "todo_updated",
  "permission_requested",
  "permission_responded",
  "checkpoint_list_observed",
  "checkpoint_restored",
  "skills_catalog_observed",
  "skills_selection_observed",
  "ctree_node_observed",
  "ctree_snapshot_observed",
  "task_event_observed",
  "warning_observed",
  "reward_updated",
  "limits_updated",
  "completion_observed",
  "log_linked",
  "runtime_error_observed",
  "run_finished",
]

const TURN_OWNED_EVENT_KINDS: readonly LoggedSessionEvent["kind"][] = [
  "input_observed",
  "turn_started",
  "assistant_text_delta",
  "assistant_text_completed",
  "turn_completed",
  "turn_failed",
  "turn_cancelled",
  "conversation_compaction_started",
  "conversation_compaction_completed",
  "assistant_message_started",
  "assistant_reasoning_delta",
  "assistant_thought_summary_delta",
  "tool_execution_started",
  "tool_execution_stdout_delta",
  "tool_execution_stderr_delta",
  "tool_execution_completed",
  "tool_called",
  "tool_result_observed",
  "permission_requested",
  "permission_responded",
  "ctree_node_observed",
  "task_event_observed",
  "warning_observed",
  "reward_updated",
  "limits_updated",
  "completion_observed",
  "log_linked",
  "run_finished",
]

const exactObject = (value: unknown, allowedKeys: readonly string[], requiredKeys: readonly string[], label: string): Record<string, unknown> => {
  if (!isObject(value)) throw new TypeError(`${label} must be an object`)
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) throw new TypeError(`${label} must be a plain object`)
  let keyCount = 0
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue
    keyCount += 1
    if (keyCount > allowedKeys.length || !allowedKeys.includes(key)) throw new TypeError(`${label} contains an unapproved field`)
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (descriptor === undefined || !("value" in descriptor)) throw new TypeError(`${label} contains an accessor field`)
  }
  for (const key of requiredKeys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (descriptor === undefined || descriptor.enumerable !== true || !("value" in descriptor)) throw new TypeError(`${label} is missing a required data field`)
  }
  return value
}

function assertString(value: unknown, label: string, pattern?: RegExp): asserts value is string {
  if (typeof value !== "string" || exceedsStringBound(value) || (pattern !== undefined && !pattern.test(value))) {
    throw new TypeError(`${label} is not an approved string`)
  }
}
function assertIdentity(value: unknown, label: string): asserts value is string {
  assertString(value, label, IDENTITY)
  const detection = detectSensitiveValues(value)
  if (detection.findings.length > 0 || detection.truncated) throw new TypeError(`${label} contains a sensitive value`)
}
function assertNullableIdentity(value: unknown, label: string): void {
  if (value !== null) assertIdentity(value, label)
}
function assertInteger(value: unknown, label: string): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new TypeError(`${label} must be a non-negative safe integer`)
}
function assertBoolean(value: unknown, label: string): asserts value is boolean {
  if (typeof value !== "boolean") throw new TypeError(`${label} must be a boolean`)
}
function assertEnum<T extends string>(value: unknown, allowed: readonly T[], label: string): asserts value is T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new TypeError(`${label} is not an approved value`)
}
function assertSafeCode(value: unknown, label: string): asserts value is string {
  assertString(value, label, SAFE_CODE)
  const detection = detectSensitiveValues(value)
  if (detection.findings.length > 0 || detection.truncated) throw new TypeError(`${label} contains a sensitive value`)
}


const assertDisplay = (value: unknown): void => {
  const display = exactObject(value, ["kind", "text", "redacted", "categories", "reason", "code", "status"], ["kind"], "display")
  assertEnum(display.kind, ["user-text", "assistant-text", "cancellation-status", "protocol-error", "gap-error", "local-close"] as const, "display.kind")
  if (display.kind === "user-text" || display.kind === "assistant-text") {
    exactObject(display, ["kind", "text", "redacted", "categories"], ["kind", "text", "redacted", "categories"], "display text")
    assertString(display.text, "display.text")
    assertBoolean(display.redacted, "display.redacted")
    if (!Array.isArray(display.categories) || display.categories.length > SESSION_EVIDENCE_BOUNDS.maxFindings) throw new TypeError("display.categories is invalid")
    for (const category of display.categories) assertEnum(category, ["credential", "header", "url", "path", "alias", "account-id", "body", "event-payload", "malformed-body", "error-serialization", "cycle", "depth-limit", "entry-limit", "string-limit"] as const, "display category")
    const normalizedCategories = [...new Set(display.categories)].sort()
    if (JSON.stringify(display.categories) !== JSON.stringify(normalizedCategories)) throw new TypeError("display categories must be unique and sorted")
    if (display.redacted !== (display.text === REDACTED_VALUE)) throw new TypeError("display redaction state is inconsistent")
    if (!display.redacted) {
      const detection = detectSensitiveValues(display.text)
      if (display.categories.length !== 0 || detection.findings.length > 0 || detection.truncated) throw new TypeError("unredacted display text is not safe")
    }
  } else if (display.kind === "cancellation-status") {
    exactObject(display, ["kind", "reason"], ["kind", "reason"], "cancellation display")
    assertEnum(display.reason, ["user_requested", "timeout", "superseded"] as const, "cancellation reason")
  } else if (display.kind === "protocol-error" || display.kind === "gap-error") {
    exactObject(display, ["kind", "code"], ["kind", "code"], "error display")
    assertSafeCode(display.code, "display.code")
  } else {
    exactObject(display, ["kind", "status"], ["kind", "status"], "local close display")
    assertEnum(display.status, ["closed-locally"] as const, "local close status")
  }
}

const assertFailureDetails = (value: unknown): void => {
  const details = exactObject(value, ["kind", "status", "code", "turnId", "eventId", "sequence", "lastAppliedEventId", "lastAppliedSequence", "sessionId", "inputId"], ["kind"], "failure details")
  assertEnum(details.kind, ["http", "timeout", "caller-abort", "protocol", "resume-gap", "session-not-found", "admission-conflict", "idempotency-conflict", "cancellation-conflict", "turn-failed", "unknown-error"] as const, "failure kind")
  const allowedByKind: Record<string, readonly string[]> = {
    http: ["kind", "status", "code", "turnId"], timeout: ["kind"], "caller-abort": ["kind"],
    protocol: ["kind", "code", "eventId", "sequence"], "resume-gap": ["kind", "code", "lastAppliedEventId", "lastAppliedSequence"],
    "session-not-found": ["kind", "sessionId"], "admission-conflict": ["kind", "sessionId", "code"],
    "idempotency-conflict": ["kind", "sessionId", "turnId"], "cancellation-conflict": ["kind", "sessionId", "turnId", "code"],
    "turn-failed": ["kind", "sessionId", "inputId", "turnId", "code"], "unknown-error": ["kind"],
  }
  exactObject(details, allowedByKind[details.kind]!, allowedByKind[details.kind]!, "failure details variant")
  if (details.kind === "http") {
    assertInteger(details.status, "failure.status")
    if ((details.status as number) < 100 || (details.status as number) > 599) throw new TypeError("failure.status must be an HTTP status")
    if (details.code !== null) assertSafeCode(details.code, "failure.code")
    assertNullableIdentity(details.turnId, "failure.turnId")
  } else if (details.kind === "protocol") {
    assertSafeCode(details.code, "failure.code")
    assertNullableIdentity(details.eventId, "failure.eventId")
    if (details.sequence !== null) assertInteger(details.sequence, "failure.sequence")
  } else if (details.kind === "resume-gap") {
    assertSafeCode(details.code, "failure.code")
    assertNullableIdentity(details.lastAppliedEventId, "failure.lastAppliedEventId")
    assertInteger(details.lastAppliedSequence, "failure.lastAppliedSequence")
  } else if (details.kind === "session-not-found") {
    assertIdentity(details.sessionId, "failure.sessionId")
  } else if (details.kind === "admission-conflict") {
    assertIdentity(details.sessionId, "failure.sessionId")
    if (details.code !== null) assertSafeCode(details.code, "failure.code")
  } else if (details.kind === "idempotency-conflict") {
    assertIdentity(details.sessionId, "failure.sessionId")
    assertNullableIdentity(details.turnId, "failure.turnId")
  } else if (details.kind === "cancellation-conflict") {
    assertIdentity(details.sessionId, "failure.sessionId")
    assertIdentity(details.turnId, "failure.turnId")
    if (details.code !== null) assertSafeCode(details.code, "failure.code")
  } else if (details.kind === "turn-failed") {
    assertIdentity(details.sessionId, "failure.sessionId")
    assertIdentity(details.inputId, "failure.inputId")
    assertIdentity(details.turnId, "failure.turnId")
    assertSafeCode(details.code, "failure.code")
  }
}

function assertRecord(value: unknown): void {
  const base = exactObject(value, ["recordKind", "sessionId", "status", "turnAdmission", "activeTurnId", "queuedTurnCount", "cursor", "terminalTurns", "clientMessageId", "inputId", "turnId", "disposition", "originalDisposition", "cancellationRequestId", "cancellationRequestKey", "eventId", "sequence", "occurredAtMs", "eventKind", "display", "resumeMode", "gapObserved", "duplicateApplied", "details", "backendSessionDeletion"], ["recordKind"], "evidence record")
  assertEnum(base.recordKind, ["snapshot", "submit", "cancellation", "event", "stable-cursor", "failure", "local-close"] as const, "record kind")
  if (base.recordKind === "snapshot") {
    const row = exactObject(base, ["recordKind", "sessionId", "status", "turnAdmission", "activeTurnId", "queuedTurnCount", "cursor", "terminalTurns"], ["recordKind", "sessionId", "status", "turnAdmission", "activeTurnId", "queuedTurnCount", "cursor", "terminalTurns"], "snapshot record")
    assertIdentity(row.sessionId, "snapshot.sessionId")
    assertEnum(row.status, ["starting", "running", "completed", "failed", "stopped"] as const, "snapshot.status")
    assertEnum(row.turnAdmission, ["idle", "active"] as const, "snapshot.turnAdmission")
    assertNullableIdentity(row.activeTurnId, "snapshot.activeTurnId")
    assertInteger(row.queuedTurnCount, "snapshot.queuedTurnCount")
    const cursor = exactObject(row.cursor, ["earliestRetainedSequence", "earliestRetainedEventId", "headSequence", "headEventId", "retainedHistory", "replayContractDigest"], ["earliestRetainedSequence", "earliestRetainedEventId", "headSequence", "headEventId", "retainedHistory", "replayContractDigest"], "snapshot cursor")
    if (cursor.earliestRetainedSequence !== null) assertInteger(cursor.earliestRetainedSequence, "cursor.earliestRetainedSequence")
    assertNullableIdentity(cursor.earliestRetainedEventId, "cursor.earliestRetainedEventId")
    assertInteger(cursor.headSequence, "cursor.headSequence")
    assertNullableIdentity(cursor.headEventId, "cursor.headEventId")
    assertEnum(cursor.retainedHistory, ["complete", "partial"] as const, "cursor.retainedHistory")
    assertString(cursor.replayContractDigest, "cursor.replayContractDigest", DIGEST)
    if (!Array.isArray(row.terminalTurns) || row.terminalTurns.length > SESSION_EVIDENCE_BOUNDS.maxCollectionEntries) throw new TypeError("snapshot.terminalTurns is invalid")
    for (const terminal of row.terminalTurns) {
      const turn = exactObject(terminal, ["inputId", "turnId", "outcome", "originalDisposition"], ["inputId", "turnId", "outcome", "originalDisposition"], "terminal turn")
      assertIdentity(turn.inputId, "terminal.inputId"); assertIdentity(turn.turnId, "terminal.turnId")
      assertEnum(turn.outcome, ["completed", "failed", "cancelled"] as const, "terminal.outcome")
      assertEnum(turn.originalDisposition, ["started", "queued"] as const, "terminal.originalDisposition")
    }
  } else if (base.recordKind === "submit") {
    const row = exactObject(base, ["recordKind", "clientMessageId", "inputId", "turnId", "disposition", "originalDisposition"], ["recordKind", "clientMessageId", "inputId", "turnId", "disposition", "originalDisposition"], "submit record")
    assertIdentity(row.clientMessageId, "submit.clientMessageId"); assertIdentity(row.inputId, "submit.inputId"); assertIdentity(row.turnId, "submit.turnId")
    assertEnum(row.disposition, ["started", "queued", "deduplicated"] as const, "submit.disposition")
    assertEnum(row.originalDisposition, ["started", "queued"] as const, "submit.originalDisposition")
  } else if (base.recordKind === "cancellation") {
    const row = exactObject(base, ["recordKind", "cancellationRequestId", "cancellationRequestKey", "inputId", "turnId", "disposition", "originalDisposition"], ["recordKind", "cancellationRequestId", "cancellationRequestKey", "inputId", "turnId", "disposition", "originalDisposition"], "cancellation record")
    for (const key of ["cancellationRequestId", "cancellationRequestKey", "inputId", "turnId"]) assertIdentity(row[key], `cancellation.${key}`)
    assertEnum(row.disposition, ["cancellation_requested", "queued_cancelled", "deduplicated"] as const, "cancellation.disposition")
    assertEnum(row.originalDisposition, ["cancellation_requested", "queued_cancelled"] as const, "cancellation.originalDisposition")
  } else if (base.recordKind === "event") {
    const row = exactObject(base, ["recordKind", "eventId", "sequence", "sessionId", "inputId", "turnId", "occurredAtMs", "eventKind", "display"], ["recordKind", "eventId", "sequence", "sessionId", "inputId", "turnId", "occurredAtMs", "eventKind", "display"], "event record")
    assertIdentity(row.eventId, "event.eventId"); assertIdentity(row.sessionId, "event.sessionId"); assertNullableIdentity(row.inputId, "event.inputId"); assertNullableIdentity(row.turnId, "event.turnId")
    assertInteger(row.sequence, "event.sequence"); assertInteger(row.occurredAtMs, "event.occurredAtMs"); assertEnum(row.eventKind, LOGGED_SESSION_EVENT_KINDS, "event.eventKind")
    if (TURN_OWNED_EVENT_KINDS.includes(row.eventKind as LoggedSessionEvent["kind"])) {
      assertIdentity(row.inputId, "event.inputId")
      assertIdentity(row.turnId, "event.turnId")
    }
    if (row.display !== null) assertDisplay(row.display)
    const displayKind = isObject(row.display) ? row.display.kind : null
    if (row.eventKind === "input_observed" && displayKind !== "user-text") throw new TypeError("input event requires user-text display")
    if (row.eventKind === "assistant_text_delta" && displayKind !== "assistant-text") throw new TypeError("assistant delta requires assistant-text display")
    if (row.eventKind === "assistant_text_completed" && displayKind !== null && displayKind !== "assistant-text") throw new TypeError("assistant completion display must be assistant-text")
    if (row.eventKind === "turn_cancelled" && displayKind !== "cancellation-status") throw new TypeError("cancelled event requires cancellation-status display")
    if (row.eventKind !== "input_observed" && row.eventKind !== "assistant_text_delta" && row.eventKind !== "assistant_text_completed" && row.eventKind !== "turn_cancelled" && row.display !== null) throw new TypeError("event kind does not admit display")
  } else if (base.recordKind === "stable-cursor") {
    const row = exactObject(base, ["recordKind", "sessionId", "eventId", "sequence", "resumeMode", "gapObserved", "duplicateApplied"], ["recordKind", "sessionId", "eventId", "sequence", "resumeMode", "gapObserved", "duplicateApplied"], "cursor record")
    assertIdentity(row.sessionId, "cursor.sessionId"); assertIdentity(row.eventId, "cursor.eventId"); assertInteger(row.sequence, "cursor.sequence")
    assertEnum(row.resumeMode, ["exclusive"] as const, "cursor.resumeMode"); assertBoolean(row.gapObserved, "cursor.gapObserved"); assertBoolean(row.duplicateApplied, "cursor.duplicateApplied")
  } else if (base.recordKind === "failure") {
    const row = exactObject(base, ["recordKind", "details", "display"], ["recordKind", "details", "display"], "failure record")
    assertFailureDetails(row.details)
    if (row.display !== null) assertDisplay(row.display)
    const detailsKind = isObject(row.details) ? row.details.kind : null
    const detailsCode = isObject(row.details) ? row.details.code : null
    const displayKind = isObject(row.display) ? row.display.kind : null
    const displayCode = isObject(row.display) ? row.display.code : null
    if (detailsKind === "protocol" && (displayKind !== "protocol-error" || displayCode !== detailsCode)) throw new TypeError("protocol failure display is inconsistent")
    if (detailsKind === "resume-gap" && (displayKind !== "gap-error" || displayCode !== detailsCode)) throw new TypeError("resume-gap failure display is inconsistent")
    if (detailsKind !== "protocol" && detailsKind !== "resume-gap" && row.display !== null) throw new TypeError("failure kind does not admit display")
  } else {
    const row = exactObject(base, ["recordKind", "sessionId", "backendSessionDeletion", "display"], ["recordKind", "sessionId", "backendSessionDeletion", "display"], "local close record")
    assertIdentity(row.sessionId, "close.sessionId"); assertEnum(row.backendSessionDeletion, ["not-requested"] as const, "close.backendSessionDeletion"); assertDisplay(row.display)
    if (!isObject(row.display) || row.display.kind !== "local-close") throw new TypeError("local close display is inconsistent")
  }
}

const assertProvenance = (value: unknown, evidenceClass: SessionEvidenceClass): void => {
  const provenance = exactObject(value, ["kind", "sourceTicket", "captureSchemaVersion", "captureSha256", "backendCommit", "clientCommit", "configurationSha256", "candidateCommit", "candidateTree"], ["kind", "sourceTicket"], "provenance")
  assertEnum(provenance.kind, ["static-fixture", "runtime-capture", "product-journey"] as const, "provenance.kind")
  if (provenance.kind !== evidenceClass) throw new TypeError("evidence class and provenance kind differ")
  if (provenance.kind === "static-fixture") {
    exactObject(provenance, ["kind", "sourceTicket"], ["kind", "sourceTicket"], "static provenance")
    assertEnum(provenance.sourceTicket, ["bb-89n.15"] as const, "static source ticket")
  } else if (provenance.kind === "runtime-capture") {
    exactObject(provenance, ["kind", "sourceTicket", "captureSchemaVersion", "captureSha256", "backendCommit", "clientCommit", "configurationSha256"], ["kind", "sourceTicket", "captureSchemaVersion", "captureSha256", "backendCommit", "clientCommit", "configurationSha256"], "runtime provenance")
    assertEnum(provenance.sourceTicket, ["bb-89n.14"] as const, "runtime source ticket")
    assertEnum(provenance.captureSchemaVersion, ["bb.p30.bb89n14.gate_evidence.v1"] as const, "capture schema")
    assertString(provenance.captureSha256, "capture digest", DIGEST); assertString(provenance.configurationSha256, "configuration digest", DIGEST)
    assertString(provenance.backendCommit, "backend commit", COMMIT); assertString(provenance.clientCommit, "client commit", COMMIT)
  } else {
    exactObject(provenance, ["kind", "sourceTicket", "candidateCommit", "candidateTree", "backendCommit", "clientCommit", "configurationSha256"], ["kind", "sourceTicket", "candidateCommit", "candidateTree", "backendCommit", "clientCommit", "configurationSha256"], "product provenance")
    assertEnum(provenance.sourceTicket, ["bb-89n.16"] as const, "product source ticket")
    for (const key of ["candidateCommit", "backendCommit", "clientCommit"]) assertString(provenance[key], `provenance.${key}`, COMMIT)
    assertString(provenance.candidateTree, "candidate tree", COMMIT); assertString(provenance.configurationSha256, "configuration digest", DIGEST)
  }
}

const assertJourneyContract = (bundle: SessionEvidenceBundle): void => {
  const records = bundle.records
  const events = records.filter((record): record is EventEvidence => record.recordKind === "event")
  const snapshots = records.filter((record): record is SnapshotEvidence => record.recordKind === "snapshot")
  const submits = records.filter((record): record is SubmitEvidence => record.recordKind === "submit")
  const cancellations = records.filter((record): record is CancellationEvidence => record.recordKind === "cancellation")
  const cursors = records.filter((record): record is StableCursorEvidence => record.recordKind === "stable-cursor")
  const directSessionIds = new Set<string>()
  for (const record of records) {
    if (record.recordKind === "snapshot" || record.recordKind === "event" || record.recordKind === "stable-cursor" || record.recordKind === "local-close") {
      directSessionIds.add(record.sessionId)
    }
  }
  if (directSessionIds.size !== 1) throw new TypeError("journey evidence must bind exactly one session")

  if (bundle.journeyId === "P30-SESSION-NEW-TURN-RECONNECT") {
    if (submits.length !== 1 || cursors.length === 0) throw new TypeError("new-turn reconnect evidence requires one submit and a stable cursor")
    const submit = submits[0]!
    const inputEvents = events.filter((event) => event.eventKind === "input_observed")
    const assistantEvents = events.filter((event) => event.eventKind === "assistant_text_completed")
    const completedEvents = events.filter((event) => event.eventKind === "turn_completed")
    if (inputEvents.length === 0 || assistantEvents.length === 0 || completedEvents.length === 0) {
      throw new TypeError("new-turn reconnect evidence is missing a required transition")
    }
    for (const event of [...inputEvents, ...assistantEvents, ...completedEvents]) {
      if (event.inputId !== submit.inputId || event.turnId !== submit.turnId) throw new TypeError("new-turn reconnect correlation differs from submit")
    }
    const inputSequence = Math.min(...inputEvents.map((event) => event.sequence))
    const assistantSequence = Math.min(...assistantEvents.map((event) => event.sequence))
    const completedSequence = Math.min(...completedEvents.map((event) => event.sequence))
    if (!(inputSequence <= assistantSequence && assistantSequence <= completedSequence)) throw new TypeError("new-turn reconnect transitions are out of order")
    if (!cursors.some((cursor) => events.some((event) => event.sessionId === cursor.sessionId && event.eventId === cursor.eventId && event.sequence === cursor.sequence))) {
      throw new TypeError("stable cursor is not bound to an observed event")
    }
  } else if (bundle.journeyId === "P30-SESSION-ATTACH-ACTIVE") {
    const activeSnapshots = snapshots.filter((snapshot) => snapshot.turnAdmission === "active" && snapshot.activeTurnId !== null)
    if (activeSnapshots.length === 0 || cursors.length === 0) throw new TypeError("attach-active evidence requires an active snapshot and stable cursor")
    if (!activeSnapshots.some((snapshot) =>
      events.some((event) => event.sessionId === snapshot.sessionId && event.turnId === snapshot.activeTurnId)
    )) throw new TypeError("attach-active evidence does not observe the active turn")
  } else {
    if (cancellations.length !== 1) throw new TypeError("targeted-cancel evidence requires exactly one cancellation")
    const cancellation = cancellations[0]!
    if (!events.some((event) =>
      event.eventKind === "turn_cancelled" &&
      event.inputId === cancellation.inputId &&
      event.turnId === cancellation.turnId
    )) throw new TypeError("targeted-cancel evidence is missing its correlated cancellation event")
    if (!snapshots.some((snapshot) =>
      snapshot.terminalTurns.some((turn) =>
        turn.inputId === cancellation.inputId &&
        turn.turnId === cancellation.turnId &&
        turn.outcome === "cancelled"
      )
    )) throw new TypeError("targeted-cancel evidence is missing the cancelled terminal state")
    if (!snapshots.some((snapshot) =>
      snapshot.turnAdmission === "active" &&
      snapshot.activeTurnId !== null &&
      snapshot.activeTurnId !== cancellation.turnId
    )) throw new TypeError("targeted-cancel evidence does not preserve the other active turn")
  }
}

export function validateSessionEvidenceBundle(value: unknown): asserts value is SessionEvidenceBundle {
  const bundle = exactObject(value, ["schemaVersion", "redactionPolicyVersion", "evidenceClass", "journeyId", "provenance", "records"], ["schemaVersion", "redactionPolicyVersion", "evidenceClass", "journeyId", "provenance", "records"], "session evidence bundle")
  assertEnum(bundle.schemaVersion, [SESSION_EVIDENCE_SCHEMA_VERSION] as const, "schema version")
  assertEnum(bundle.redactionPolicyVersion, [SESSION_EVIDENCE_REDACTION_POLICY_VERSION] as const, "redaction policy version")
  assertEnum(bundle.evidenceClass, ["static-fixture", "runtime-capture", "product-journey"] as const, "evidence class")
  assertEnum(bundle.journeyId, ["P30-SESSION-NEW-TURN-RECONNECT", "P30-SESSION-ATTACH-ACTIVE", "P30-SESSION-TARGETED-CANCEL"] as const, "journey id")
  assertProvenance(bundle.provenance, bundle.evidenceClass)
  if (!Array.isArray(bundle.records) || bundle.records.length > SESSION_EVIDENCE_BOUNDS.maxCollectionEntries) throw new TypeError("evidence records are invalid")
  for (const record of bundle.records) assertRecord(record)
  if (bundle.records.length === 0) throw new TypeError("evidence records must not be empty")
  if (bundle.evidenceClass !== "static-fixture") assertJourneyContract(bundle as unknown as SessionEvidenceBundle)
}

const canonicalize = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (!isObject(value)) return value
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]))
}

const snapshotAndValidateBundle = (bundle: SessionEvidenceBundle): SessionEvidenceBundle => {
  const snapshot: unknown = structuredClone(bundle)
  validateSessionEvidenceBundle(snapshot)
  return snapshot
}

const serializeValidatedSnapshot = (snapshot: SessionEvidenceBundle): Uint8Array =>
  new TextEncoder().encode(JSON.stringify(canonicalize(snapshot)))

export const serializeSessionEvidenceBundle = (bundle: SessionEvidenceBundle): Uint8Array => {
  const snapshot = snapshotAndValidateBundle(bundle)
  if (snapshot.provenance.kind !== "static-fixture") throw new TypeError("non-static evidence requires verified provenance serialization")
  return serializeValidatedSnapshot(snapshot)
}

export const digestSessionEvidenceBundle = async (bundle: SessionEvidenceBundle): Promise<string> =>
  sha256Bytes(serializeSessionEvidenceBundle(bundle))

const snapshotVerifiedRuntimeCapture = async (
  bundle: SessionEvidenceBundle,
  sourceBytes: Uint8Array,
): Promise<SessionEvidenceBundle> => {
  const snapshot = snapshotAndValidateBundle(bundle)
  if (snapshot.provenance.kind !== "runtime-capture") throw new TypeError("evidence is not a runtime capture")
  if (!(sourceBytes instanceof Uint8Array)) throw new TypeError("runtime capture source bytes must be a Uint8Array")
  const actual = await sha256Bytes(sourceBytes)
  if (actual !== snapshot.provenance.captureSha256) throw new TypeError("runtime capture source digest differs")
  return snapshot
}

export const verifyRuntimeCaptureEvidenceSource = async (
  bundle: SessionEvidenceBundle,
  sourceBytes: Uint8Array,
): Promise<void> => {
  await snapshotVerifiedRuntimeCapture(bundle, sourceBytes)
}

export const serializeVerifiedRuntimeCaptureEvidenceBundle = async (
  bundle: SessionEvidenceBundle,
  sourceBytes: Uint8Array,
): Promise<Uint8Array> =>
  serializeValidatedSnapshot(await snapshotVerifiedRuntimeCapture(bundle, sourceBytes))

export const digestVerifiedRuntimeCaptureEvidenceBundle = async (
  bundle: SessionEvidenceBundle,
  sourceBytes: Uint8Array,
): Promise<string> =>
  sha256Bytes(await serializeVerifiedRuntimeCaptureEvidenceBundle(bundle, sourceBytes))

const snapshotVerifiedProductJourney = async (
  bundle: SessionEvidenceBundle,
  trusted: TrustedProductJourneyProvenance,
): Promise<SessionEvidenceBundle> => {
  const snapshot = snapshotAndValidateBundle(bundle)
  if (snapshot.provenance.kind !== "product-journey") throw new TypeError("evidence is not a product journey")
  assertString(trusted.candidateCommit, "trusted candidate commit", COMMIT)
  assertString(trusted.candidateTree, "trusted candidate tree", COMMIT)
  assertString(trusted.backendCommit, "trusted backend commit", COMMIT)
  assertString(trusted.clientCommit, "trusted client commit", COMMIT)
  if (!(trusted.configurationBytes instanceof Uint8Array)) throw new TypeError("trusted configuration bytes must be a Uint8Array")
  if (
    snapshot.provenance.candidateCommit !== trusted.candidateCommit ||
    snapshot.provenance.candidateTree !== trusted.candidateTree ||
    snapshot.provenance.backendCommit !== trusted.backendCommit ||
    snapshot.provenance.clientCommit !== trusted.clientCommit
  ) throw new TypeError("product journey repository provenance differs")
  const configurationSha256 = await sha256Bytes(trusted.configurationBytes)
  if (snapshot.provenance.configurationSha256 !== configurationSha256) throw new TypeError("product journey configuration digest differs")
  return snapshot
}

export const serializeVerifiedProductJourneyEvidenceBundle = async (
  bundle: SessionEvidenceBundle,
  trusted: TrustedProductJourneyProvenance,
): Promise<Uint8Array> => {
  const snapshot = await snapshotVerifiedProductJourney(bundle, trusted)
  return serializeValidatedSnapshot(snapshot)
}

export const digestVerifiedProductJourneyEvidenceBundle = async (
  bundle: SessionEvidenceBundle,
  trusted: TrustedProductJourneyProvenance,
): Promise<string> =>
  sha256Bytes(await serializeVerifiedProductJourneyEvidenceBundle(bundle, trusted))
