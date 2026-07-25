#!/usr/bin/env node

import { createHash, randomBytes, randomUUID } from "node:crypto"
import { execFile, spawn } from "node:child_process"
import { createServer as createNetServer } from "node:net"
import { constants as fsConstants, watch } from "node:fs"
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  readFile,
  realpath,
  readlink,
  rm,
  stat,
  writeFile,
} from "node:fs/promises"
import { tmpdir } from "node:os"
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import { isDeepStrictEqual, promisify } from "node:util"

const execFileAsync = promisify(execFile)
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
const SDK_ROOT = resolve(SCRIPT_DIR, "..")
const REPOSITORY_ROOT = resolve(SCRIPT_DIR, "../../..")
const DEFAULT_RUN_TIMEOUT_MS = 300_000
const MIN_RUN_TIMEOUT_MS = 100
const MAX_CONFIG_BYTES = 4 * 1024 * 1024
const MAX_CONFIG_CLOSURE_BYTES = 64 * 1024 * 1024
const MAX_CONFIG_CLOSURE_FILES = 4096
const MAX_EVENTS = 2048
const MAX_EVENT_BYTES = 64 * 1024
const MAX_EVIDENCE_BYTES = 1024 * 1024
const MAX_EVIDENCE_TEXT = 4096
const MAX_ID_TEXT = 256
const MAX_TERMINAL_TURNS = 16
const MAX_CLEANUP_RESERVE_MS = 500
const SYNTHETIC_HOLD_MS = 10_000
const MAX_RUNTIME_CLOSURE_FILES = 100_000
const MAX_RUNTIME_CLOSURE_BYTES = 4 * 1024 * 1024 * 1024
const MAX_RUNTIME_FILE_BYTES = 512 * 1024 * 1024
const THREAT_MODEL = "trusted-local-user-no-hostile-same-uid-process"
const PROVIDER_PERSISTENCE = "disabled"
const PROVIDER_STREAMING = "required"
const PROVIDER_CONVERSATION_STATE = "stateless"
const AUTH_ENVIRONMENT_VARIABLE = "BB89N14_AUTH_TOKEN"
let capturedAuthToken = process.env[AUTH_ENVIRONMENT_VARIABLE]
delete process.env[AUTH_ENVIRONMENT_VARIABLE]
const CHILD_ENVIRONMENT = { ...process.env }
delete CHILD_ENVIRONMENT[AUTH_ENVIRONMENT_VARIABLE]
const MANIFEST_COMMIT = "a578aaadc08bcf8ad3095532fa14cc1d69c0c975"
const CLIENT_BUILD_MANIFEST = Object.freeze({
  commit: MANIFEST_COMMIT,
  committed: Object.freeze({
    "sdk/ts/package-lock.json": "sha256:5d51127e09ed01d4457befdf35129c9d4465be774964c74c340270c3f67cc71a",
    "sdk/ts/src/client.ts": "sha256:5f2c19f53d1a5955756e0be6d0f82f4471c9101bc50c16007d191a6ab3878f9c",
    "sdk/ts/src/index.ts": "sha256:66817164f89a6459a0c81563280b7b4db73b54674778dc79d8b3c440b749b105",
    "sdk/ts/src/lifecycle-client.ts": "sha256:da6f35c721f72026a8f3e24a22a82fdd30fb84d1cce32f8d6bc998496415021b",
    "sdk/ts/src/session-runtime.ts": "sha256:254b9ea3a917d1992acd684c4f6d753172488a6683606fa3cd2953744f993f21",
    "sdk/ts/src/stream.ts": "sha256:9df24989851c314724aa852b204954b22f74457d8bd90b715991bf37fc9895de",
    "sdk/ts/tsconfig.json": "sha256:4ee84c7653016c88591787fb45ebe0faf73629d10973e181ed05922c02005bae",
  }),
  loaded: Object.freeze({
    "dist/client.js": "sha256:371d0a6076f6dfea5a888987f94f230a6f606382fbe8b8648a4143e0de82d4bc",
    "dist/index.js": "sha256:cefaf3d0154b3338fb416848b94e27d2c837cb245a8b892099ab49e0d09dee21",
    "dist/lifecycle-client.js": "sha256:b59cd08e40d40a3b4bc14dfc2ef8d92bc9255ea54d48d44819719dca00f0de8a",
    "dist/session-runtime.js": "sha256:bae837866ad815d672c5eaccbbfa533c0fb701eccdd5ff0a654e0890aae7f2ff",
    "dist/stream.js": "sha256:d4f82e6dd1c79cd7d631fc536998c501927759f5fee9b7ce05a40b61924eb18d",
    "node_modules/eventsource-parser/dist/index.js": "sha256:2b3e0c02c00ed19165cb125f2ad7f502695a73851a2f2a9ba4d3b2a1ef527047",
    "node_modules/eventsource-parser/package.json": "sha256:ef5b0bb8909234bd3f1879c86d71b4b119b6a82bd27858fdbd434cf265d276ed",
  }),
})
const FIXED_GIT_ENVIRONMENT = { ...CHILD_ENVIRONMENT }
for (const name of Object.keys(FIXED_GIT_ENVIRONMENT)) {
  if (name.startsWith("GIT_")) delete FIXED_GIT_ENVIRONMENT[name]
}
FIXED_GIT_ENVIRONMENT.GIT_NO_REPLACE_OBJECTS = "1"
const CLIENT_BUILD_MANIFEST_SHA256 = `sha256:${createHash("sha256").update(JSON.stringify(CLIENT_BUILD_MANIFEST)).digest("hex")}`
const OPENAI_PROVIDER_MODEL = /^openai\/[A-Za-z0-9][A-Za-z0-9._:+-]{0,126}$/
const SYNTHETIC_IDENTITY = /^(?:replay|mock|smoke|cli_mock)(?:$|[\/_:.+-])/i
const SAFE_DIAGNOSTIC_CODES = new Set([
  "caller-abort",
  "resume_window_exceeded",
  "sequence_discontinuity",
  "duplicate_terminal_transition",
  "invalid_exact_empty_payload",
  "stream_truncated_before_open_head",
  "cursor_outside_advertised_replay",
  "backend_git_common_dir_mismatch",
  "backend_python_unapproved",
  "backend_runtime_unapproved",
  "backend_listener_not_clean",
  "partial_retained_history",
])
const SAFE_FAILURE_KINDS = new Set([
  "http",
  "timeout",
  "caller-abort",
  "protocol",
  "resume-gap",
  "session-not-found",
  "admission-conflict",
  "idempotency-conflict",
  "cancellation-conflict",
  "turn-failed",
])
const SESSION_BOOTSTRAP_KINDS = new Set([
  "checkpoint_list_observed",
  "skills_catalog_observed",
  "skills_selection_observed",
  "ctree_snapshot_observed",
])
const TERMINAL_KINDS = new Set(["turn_completed", "turn_failed", "turn_cancelled"])
const MAIN_CAPTURE_KINDS = new Set([
  "input_observed",
  "turn_started",
  "assistant_text_delta",
  "assistant_text_completed",
  "turn_completed",
])
const OPTION_NAMES = new Set([
  "--base-url",
  "--config-path",
  "--backend-root",
  "--backend-python",
  "--workspace",
  "--output",
  "--expected-backend-commit",
  "--expected-client-commit",
  "--expected-provider-model",
  "--timeout-ms",
])
const REQUIRED_OPTIONS = [...OPTION_NAMES].filter((name) => name !== "--timeout-ms")
let CanonicalE4ClientErrorClass = null

export class GateFailure extends Error {
  constructor(code, stage) {
    super(code)
    this.name = "GateFailure"
    this.code = code
    this.stage = stage
  }
}

const fail = (code, stage) => {
  throw new GateFailure(code, stage)
}

const assertGate = (condition, code, stage) => {
  if (!condition) fail(code, stage)
}

const isInside = (parent, child) => {
  const rel = relative(parent, child)
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
}

const sha256 = (bytes) => `sha256:${createHash("sha256").update(bytes).digest("hex")}`
const isSha256 = (value) => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value)
const isCommit = (value) => typeof value === "string" && /^[0-9a-f]{40}$/.test(value)
const isInteger = (value, minimum = 0) => Number.isSafeInteger(value) && value >= minimum

const boundedText = (value, code, stage, maximum = MAX_EVIDENCE_TEXT) => {
  assertGate(typeof value === "string" && value.length > 0 && value.length <= maximum, code, stage)
  return value
}

const nullableBoundedText = (value, code, stage, maximum = MAX_EVIDENCE_TEXT) => (
  value === null ? null : boundedText(value, code, stage, maximum)
)

const exactObject = (value, keys, code, stage) => {
  assertGate(value !== null && typeof value === "object" && !Array.isArray(value), code, stage)
  const prototype = Object.getPrototypeOf(value)
  assertGate(prototype === Object.prototype || prototype === null, code, stage)
  const actual = Reflect.ownKeys(value)
  assertGate(actual.every((key) => typeof key === "string"), code, stage)
  assertGate(actual.length === keys.length && keys.every((key) => actual.includes(key)), code, stage)
  return value
}

const withTimeout = async (promise, code, stage, timeoutMs) => {
  const observed = Promise.resolve(promise)
  void observed.catch(() => undefined)
  assertGate(Number.isFinite(timeoutMs) && timeoutMs > 0, "absolute_deadline_exceeded", stage)
  let timer
  try {
    return await Promise.race([
      observed,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new GateFailure(code, stage)), timeoutMs)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

const withinDeadline = (promise, deadline, code, stage) => (
  withTimeout(promise, code, stage, deadline - Date.now())
)
const operationDeadlineFor = (startedAt, finalDeadline) => {
  const total = finalDeadline - startedAt
  const reserve = Math.min(MAX_CLEANUP_RESERVE_MS, Math.max(25, Math.floor(total / 5)))
  const operationDeadline = finalDeadline - reserve
  assertGate(operationDeadline > startedAt, "invalid_timeout", "cli")
  return operationDeadline
}


const cleanupTimeout = (deadline) => {
  const remaining = deadline - Date.now()
  assertGate(remaining > 0, "absolute_deadline_exceeded", "cleanup")
  return Math.min(MAX_CLEANUP_RESERVE_MS, remaining)
}

const requiredCleanup = async (operations, deadline) => {
  let failed = false
  for (const operation of operations) {
    try {
      await withTimeout(Promise.resolve().then(operation), "cleanup_timeout", "cleanup", cleanupTimeout(deadline))
    } catch {
      failed = true
    }
  }
  assertGate(!failed, "required_cleanup_failed", "cleanup")
}

const removeRequired = async (path, options) => {
  await rm(path, options)
  assertGate(await lstat(path).catch(() => null) === null, "required_cleanup_failed", "cleanup")
}

const closeIterator = async (iterator, controller, deadline) => {
  if (iterator === null) return
  controller?.abort()
  await withTimeout(iterator.return(), "cleanup_timeout", "cleanup", cleanupTimeout(deadline))
}

const eventBudget = () => ({ count: 0, bytes: 0 })

const accountEvent = (budget, event, stage) => {
  budget.count += 1
  assertGate(budget.count <= MAX_EVENTS, "event_count_budget_exceeded", stage)
  let serialized
  try {
    serialized = JSON.stringify(event)
  } catch {
    fail("event_payload_not_serializable", stage)
  }
  const bytes = Buffer.byteLength(serialized, "utf8")
  assertGate(bytes <= MAX_EVENT_BYTES, "event_payload_budget_exceeded", stage)
  budget.bytes += bytes
  assertGate(budget.bytes <= MAX_EVENTS * MAX_EVENT_BYTES, "event_byte_budget_exceeded", stage)
}

const nextEvent = async (iterator, stage, deadline, budget) => {
  let result
  try {
    result = await withinDeadline(iterator.next(), deadline, "event_timeout", stage)
  } catch (error) {
    if (
      Date.now() >= deadline
      && CanonicalE4ClientErrorClass !== null
      && error instanceof CanonicalE4ClientErrorClass
      && ["http", "timeout", "caller-abort"].includes(error.failure?.kind)
    ) fail("event_timeout", stage)
    throw error
  }
  assertGate(!result.done, "event_stream_ended", stage)
  accountEvent(budget, result.value, stage)
  return result.value
}

const correlated = (event, receipt) => (
  String(event.inputId) === String(receipt.inputId) && String(event.turnId) === String(receipt.turnId)
)

const requireCorrelated = (event, receipt, stage) => {
  assertGate(event.inputId !== null && event.turnId !== null, "uncorrelated_event", stage)
  assertGate(correlated(event, receipt), "conflicting_event_correlation", stage)
}

const receiptEvidence = (receipt) => ({
  clientMessageId: boundedText(String(receipt.clientMessageId), "receipt_client_message_id", "evidence", MAX_ID_TEXT),
  inputId: boundedText(String(receipt.inputId), "receipt_input_id", "evidence", MAX_ID_TEXT),
  turnId: boundedText(String(receipt.turnId), "receipt_turn_id", "evidence", MAX_ID_TEXT),
  disposition: receipt.disposition,
  originalDisposition: receipt.originalDisposition,
})

const projectSnapshot = (snapshot, stage) => {
  assertGate(Array.isArray(snapshot.terminalTurns) && snapshot.terminalTurns.length <= MAX_TERMINAL_TURNS, "snapshot_terminal_budget", stage)
  const terminalTurns = snapshot.terminalTurns.map((terminal) => ({
    inputId: boundedText(String(terminal.inputId), "snapshot_terminal_input", stage, MAX_ID_TEXT),
    turnId: boundedText(String(terminal.turnId), "snapshot_terminal_turn", stage, MAX_ID_TEXT),
    outcome: terminal.outcome,
    originalDisposition: terminal.originalDisposition,
  }))
  assertGate(["idle", "active"].includes(snapshot.turnAdmission), "snapshot_admission", stage)
  assertGate(isInteger(snapshot.queuedTurnCount), "snapshot_queue", stage)
  assertGate(isInteger(snapshot.headSequence), "snapshot_head_sequence", stage)
  assertGate(["complete", "partial"].includes(snapshot.retainedHistory), "snapshot_retention", stage)
  assertGate(isSha256(snapshot.sessionReplayContractDigest), "snapshot_replay_digest", stage)
  return {
    sessionId: boundedText(String(snapshot.sessionId), "snapshot_session_id", stage, MAX_ID_TEXT),
    model: nullableBoundedText(snapshot.model, "snapshot_model", stage, MAX_ID_TEXT),
    turnAdmission: snapshot.turnAdmission,
    activeTurnId: nullableBoundedText(snapshot.activeTurnId, "snapshot_active_turn", stage, MAX_ID_TEXT),
    queuedTurnCount: snapshot.queuedTurnCount,
    terminalTurns,
    headSequence: snapshot.headSequence,
    headEventId: nullableBoundedText(snapshot.headEventId, "snapshot_head_event", stage, MAX_ID_TEXT),
    retainedHistory: snapshot.retainedHistory,
    sessionReplayContractDigest: snapshot.sessionReplayContractDigest,
  }
}

const requireIdle = (snapshot, stage) => {
  assertGate(snapshot.turnAdmission === "idle", "non_idle_admission", stage)
  assertGate(snapshot.activeTurnId === null, "unexpected_active_turn", stage)
  assertGate(snapshot.queuedTurnCount === 0, "non_empty_queue", stage)
}

const requireNewSession = (snapshot, stage = "main.pre_submit") => {
  requireIdle(snapshot, stage)
  assertGate(snapshot.terminalTurns.length === 0, "preexisting_terminal", stage)
}

const terminalFor = (snapshot, receipt) => snapshot.terminalTurns.filter(
  (terminal) => String(terminal.inputId) === String(receipt.inputId) && String(terminal.turnId) === String(receipt.turnId),
)

const sequenceEvidence = (event) => ({
  eventId: boundedText(String(event.eventId), "event_id_too_large", "event_projection", MAX_ID_TEXT),
  sequence: event.sequence,
  sessionId: boundedText(String(event.sessionId), "session_id_too_large", "event_projection", MAX_ID_TEXT),
  inputId: boundedText(String(event.inputId), "input_id_too_large", "event_projection", MAX_ID_TEXT),
  turnId: boundedText(String(event.turnId), "turn_id_too_large", "event_projection", MAX_ID_TEXT),
  occurredAtMs: event.occurredAtMs,
  kind: boundedText(event.kind, "event_kind_too_large", "event_projection", 128),
})

const projectedPayload = (event) => {
  switch (event.kind) {
    case "input_observed":
    case "assistant_text_delta":
      exactObject(event.payload, ["text"], "unexpected_event_payload_keys", "event_projection")
      return { text: boundedText(event.payload.text, "event_text_too_large", "event_projection") }
    case "assistant_text_completed":
      exactObject(event.payload, ["text"], "unexpected_event_payload_keys", "event_projection")
      return {
        text: event.payload.text === null
          ? null
          : boundedText(event.payload.text, "event_text_too_large", "event_projection"),
      }
    case "turn_started": {
      const keys = Reflect.ownKeys(event.payload)
      assertGate(
        keys.length === 0 || (keys.length === 1 && keys[0] === "mode"),
        "unexpected_event_payload_keys",
        "event_projection",
      )
      return keys.length === 0
        ? {}
        : { mode: boundedText(event.payload.mode, "event_mode_too_large", "event_projection", 128) }
    }
    case "turn_completed":
      assertGate(Reflect.ownKeys(event.payload).length === 0, "unexpected_event_payload_keys", "event_projection")
      return {}
    default:
      fail("unsupported_evidence_event_kind", "event_projection")
  }
}

const eventEvidence = (event) => ({
  ...sequenceEvidence(event),
  payload: projectedPayload(event),
})

const assertContiguousUnique = (events, stage) => {
  const eventIds = new Set()
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    assertGate(!eventIds.has(event.eventId), "duplicate_event_application", stage)
    eventIds.add(event.eventId)
    assertGate(isInteger(event.sequence, 1), "invalid_event_sequence", stage)
    if (index > 0) assertGate(event.sequence === events[index - 1].sequence + 1, "event_sequence_gap", stage)
  }
}

const assertExpectedProviderModel = (model, expected, stage) => {
  assertGate(typeof expected === "string" && expected.length > 0 && expected.length <= MAX_ID_TEXT, "invalid_expected_provider_model", stage)
  assertGate(!SYNTHETIC_IDENTITY.test(expected.toLowerCase()), "synthetic_model_not_gate_eligible", stage)
  assertGate(OPENAI_PROVIDER_MODEL.test(expected), "provider_model_route_unapproved", stage)
  assertGate(model === expected, "provider_model_mismatch", stage)
}

const advancePastYield = async (iterator, controller, deadline, stage) => {
  const advancement = iterator.next()
  await Promise.resolve()
  controller.abort()
  try {
    const result = await withinDeadline(advancement, deadline, "cursor_commit_timeout", stage)
    assertGate(result.done, "event_beyond_captured_head", stage)
  } catch (error) {
    if (
      (CanonicalE4ClientErrorClass === null || error instanceof CanonicalE4ClientErrorClass)
      && error?.failure?.kind === "caller-abort"
    ) return
    throw error
  }
}

async function observeMainProof(session, nonce, requestText, deadline, cleanupDeadline) {
  const budget = eventBudget()
  const initialController = new AbortController()
  let initialStream = session.events({ signal: initialController.signal })
  const firstNext = nextEvent(initialStream, "main.initial_stream", deadline, budget)
  void firstNext.catch(() => undefined)
  let receipt
  try {
    receipt = await withinDeadline(
      session.submit({ text: requestText, clientMessageId: `bb89n14-main-${randomUUID()}` }),
      deadline,
      "submit_timeout",
      "main.submit",
    )
  } catch (error) {
    initialController.abort()
    await firstNext.catch(() => undefined)
    await closeIterator(initialStream, initialController, cleanupDeadline)
    throw error
  }
  assertGate(receipt.disposition === "started", "main_receipt_not_started", "main.submit")
  assertGate(receipt.originalDisposition === "started", "main_original_disposition_not_started", "main.submit")

  const applied = []
  const envelopes = []
  let pending = await firstNext
  let boundary = null
  let uncommittedLookahead = null
  try {
    while (boundary === null) {
      const pendingIsCorrelated = correlated(pending, receipt)
      if (pendingIsCorrelated) {
        requireCorrelated(pending, receipt, "main.pre_disconnect")
        assertGate(!TERMINAL_KINDS.has(pending.kind), "terminal_before_disconnect", "main.pre_disconnect")
      } else {
        assertGate(
          pending.inputId === null && pending.turnId === null && SESSION_BOOTSTRAP_KINDS.has(pending.kind),
          "uncorrelated_event",
          "main.pre_disconnect",
        )
      }
      const current = await nextEvent(initialStream, "main.pre_disconnect", deadline, budget)
      if (pendingIsCorrelated) {
        applied.push(sequenceEvidence(pending))
        if (MAIN_CAPTURE_KINDS.has(pending.kind)) envelopes.push(eventEvidence(pending))
      }
      if (pendingIsCorrelated && correlated(current, receipt) && !TERMINAL_KINDS.has(current.kind)) {
        requireCorrelated(current, receipt, "main.disconnect_lookahead")
        boundary = sequenceEvidence(pending)
        uncommittedLookahead = sequenceEvidence(current)
        break
      }
      pending = current
    }
  } finally {
    await closeIterator(initialStream, initialController, cleanupDeadline)
    initialStream = null
  }

  assertGate(boundary !== null && uncommittedLookahead !== null, "missing_disconnect_boundary", "main.disconnect")
  assertGate(uncommittedLookahead.sequence === boundary.sequence + 1, "disconnect_boundary_gap", "main.disconnect")

  const resumeController = new AbortController()
  const resumed = session.events({ signal: resumeController.signal })
  let firstResumed = null
  let terminal = null
  let capturedHead = null
  try {
    while (terminal === null) {
      const event = await nextEvent(resumed, "main.resume", deadline, budget)
      requireCorrelated(event, receipt, "main.resume")
      const sequence = sequenceEvidence(event)
      if (firstResumed === null) firstResumed = sequence
      applied.push(sequence)
      if (MAIN_CAPTURE_KINDS.has(event.kind)) envelopes.push(eventEvidence(event))
      if (TERMINAL_KINDS.has(event.kind)) {
        assertGate(event.kind === "turn_completed", "non_completed_terminal", "main.resume")
        terminal = sequence
        const headSnapshot = await withinDeadline(session.snapshot(), deadline, "snapshot_timeout", "main.captured_head")
        capturedHead = {
          sequence: headSnapshot.headSequence,
          eventId: nullableBoundedText(headSnapshot.headEventId, "snapshot_head_event", "main.captured_head", MAX_ID_TEXT),
        }
        assertGate(capturedHead.sequence >= terminal.sequence, "captured_head_before_terminal", "main.captured_head")
        assertGate(capturedHead.sequence - terminal.sequence <= MAX_EVENTS, "captured_head_budget_exceeded", "main.captured_head")
      }
    }

    let last = terminal
    while (last.sequence < capturedHead.sequence) {
      const event = await nextEvent(resumed, "main.drain", deadline, budget)
      requireCorrelated(event, receipt, "main.drain")
      assertGate(!TERMINAL_KINDS.has(event.kind), "second_terminal_observed", "main.drain")
      last = sequenceEvidence(event)
      applied.push(last)
      if (MAIN_CAPTURE_KINDS.has(event.kind)) envelopes.push(eventEvidence(event))
    }
    assertGate(last.sequence === capturedHead.sequence, "captured_head_not_drained", "main.drain")
    assertGate(last.eventId === capturedHead.eventId, "captured_head_identity_mismatch", "main.drain")
    await advancePastYield(resumed, resumeController, deadline, "main.cursor_commit")
  } finally {
    await closeIterator(resumed, resumeController, cleanupDeadline)
  }

  assertGate(firstResumed !== null, "missing_resumed_event", "main.resume")
  assertGate(firstResumed.eventId === uncommittedLookahead.eventId, "non_exclusive_resume_event", "main.resume")
  assertGate(firstResumed.sequence === boundary.sequence + 1, "non_exclusive_resume_sequence", "main.resume")
  assertContiguousUnique(applied, "main.resume")

  const inputEvents = envelopes.filter((event) => event.kind === "input_observed")
  const startEvents = envelopes.filter((event) => event.kind === "turn_started")
  const completedAssistant = envelopes.filter(
    (event) => event.kind === "assistant_text_completed" && typeof event.payload.text === "string",
  )
  const assistantDeltas = envelopes.filter((event) => event.kind === "assistant_text_delta")
  const completedTerminals = envelopes.filter((event) => event.kind === "turn_completed")
  assertGate(inputEvents.length === 1, "input_envelope_count", "main.evidence")
  assertGate(inputEvents[0].payload.text === requestText, "input_envelope_text_mismatch", "main.evidence")
  assertGate(startEvents.length === 1, "turn_start_envelope_count", "main.evidence")
  assertGate(completedAssistant.length === 1, "assistant_completed_envelope_count", "main.evidence")
  const assistantText = completedAssistant[0].payload.text
  assertGate(assistantText === nonce, "assistant_nonce_mismatch", "main.evidence")
  if (assistantDeltas.length > 0) {
    const accumulatedDeltas = assistantDeltas.map((event) => event.payload.text).join("")
    assertGate(accumulatedDeltas.length <= MAX_EVIDENCE_TEXT, "assistant_delta_budget_exceeded", "main.evidence")
    assertGate(accumulatedDeltas === nonce, "assistant_delta_accumulation_mismatch", "main.evidence")
  }
  assertGate(completedTerminals.length === 1, "completed_terminal_count", "main.evidence")

  return {
    receipt,
    assistantText,
    events: envelopes,
    sequenceTrace: applied,
    streamedTerminal: terminal,
    capturedHead,
    disconnect: {
      stableEventId: boundary.eventId,
      stableSequence: boundary.sequence,
      uncommittedLookaheadEventId: uncommittedLookahead.eventId,
      uncommittedLookaheadSequence: uncommittedLookahead.sequence,
    },
    reconnect: {
      firstEventId: firstResumed.eventId,
      firstSequence: firstResumed.sequence,
      exclusive: true,
      duplicateApplied: false,
      gapObserved: false,
      cursorCommittedThroughHead: true,
    },
  }
}

const replayFixture = (text, delayMs) => [
  JSON.stringify({ type: "assistant_message", payload: { text }, delay_ms: delayMs }),
  JSON.stringify({ type: "completion", payload: { completed: true } }),
  JSON.stringify({ type: "run_finished", payload: { completed: true } }),
].join("\n") + "\n"

export async function runSyntheticControl({
  client,
  configPath,
  configurationDigest = null,
  verifyConfiguration = async () => {},
  forbiddenEvidenceStrings = null,
  workspace,
  repositoryRoot = REPOSITORY_ROOT,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
  cleanupDeadline = deadline,
}) {
  const fixtureRoot = await withinDeadline(mkdtemp(join(tmpdir(), "bb89n14-control-fixture-")), deadline, "fixture_setup_timeout", "control.setup")
  let controlWorkspace = null
  let session = null
  let attached = null
  let stream = null
  let controller = null
  try {
    await chmod(fixtureRoot, 0o700)
    const repository = await realpath(repositoryRoot)
    const fixture = await realpath(fixtureRoot)
    assertGate(!isInside(repository, fixture), "synthetic_fixture_inside_repository", "control.setup")
    forbiddenEvidenceStrings?.add(fixtureRoot)
    forbiddenEvidenceStrings?.add(fixture)
    controlWorkspace = join(workspace, `bb89n14-control-${randomUUID()}`)
    await withinDeadline(mkdir(controlWorkspace, { recursive: false, mode: 0o700 }), deadline, "workspace_setup_timeout", "control.setup")
    forbiddenEvidenceStrings?.add(controlWorkspace)
    const fixturePaths = [1, 2, 3].map((number) => join(fixtureRoot, `turn-${number}.jsonl`))
    await withinDeadline(Promise.all([
      writeFile(fixturePaths[0], replayFixture("synthetic-control-1", SYNTHETIC_HOLD_MS), { mode: 0o600, flag: "wx" }),
      writeFile(fixturePaths[1], replayFixture("synthetic-control-2", 25), { mode: 0o600, flag: "wx" }),
      writeFile(fixturePaths[2], replayFixture("synthetic-control-3", 25), { mode: 0o600, flag: "wx" }),
    ]), deadline, "fixture_write_timeout", "control.setup")

    await verifyConfiguration()
    session = await withinDeadline(client.create({
      configPath,
      workspace: controlWorkspace,
      stream: true,
      metadata: {
        gate: "bb-89n.14",
        proof: "provider-free-synthetic-control",
        model: "replay",
        ...(configurationDigest === null ? {} : { configuration_sha256: configurationDigest }),
      },
    }), deadline, "create_timeout", "control.create")
    await verifyConfiguration()
    const initial = await withinDeadline(session.snapshot(), deadline, "snapshot_timeout", "control.initial_snapshot")
    requireNewSession(initial, "control.initial_snapshot")

    controller = new AbortController()
    stream = session.events({ signal: controller.signal })
    const budget = eventBudget()
    const firstNext = nextEvent(stream, "control.first_stream", deadline, budget)
    void firstNext.catch(() => undefined)
    const first = await withinDeadline(session.submit({
      text: `replay:${fixturePaths[0]}`,
      clientMessageId: `bb89n14-control-1-${randomUUID()}`,
    }), deadline, "submit_timeout", "control.first_submit")
    assertGate(first.disposition === "started" && first.originalDisposition === "started", "control_first_not_started", "control.first_submit")

    const events = []
    let event = await firstNext
    while (!(correlated(event, first) && event.kind === "turn_started")) {
      if (correlated(event, first)) {
        requireCorrelated(event, first, "control.first_active")
        events.push(sequenceEvidence(event))
      } else {
        assertGate(
          events.length === 0 && event.inputId === null && event.turnId === null && SESSION_BOOTSTRAP_KINDS.has(event.kind),
          "control_uncorrelated_event",
          "control.first_active",
        )
      }
      event = await nextEvent(stream, "control.first_active", deadline, budget)
    }
    events.push(sequenceEvidence(event))

    const beforeAttach = await withinDeadline(session.snapshot(), deadline, "snapshot_timeout", "control.attach")
    assertGate(beforeAttach.turnAdmission === "active", "control_first_not_active", "control.attach")
    assertGate(String(beforeAttach.activeTurnId) === String(first.turnId), "control_active_identity_mismatch", "control.attach")
    assertGate(beforeAttach.queuedTurnCount === 0, "control_pre_attach_queue_nonempty", "control.attach")
    attached = await withinDeadline(client.attach({ sessionId: session.sessionId }), deadline, "attach_timeout", "control.attach")
    const afterAttach = await withinDeadline(attached.snapshot(), deadline, "snapshot_timeout", "control.attach")
    const beforeEvidence = projectSnapshot(beforeAttach, "control.attach")
    const afterEvidence = projectSnapshot(afterAttach, "control.attach")
    assertGate(isDeepStrictEqual(beforeEvidence, afterEvidence), "attach_mutated_snapshot", "control.attach")

    const second = await withinDeadline(attached.submit({
      text: `replay:${fixturePaths[1]}`,
      clientMessageId: `bb89n14-control-2-${randomUUID()}`,
    }), deadline, "submit_timeout", "control.queue")
    const third = await withinDeadline(attached.submit({
      text: `replay:${fixturePaths[2]}`,
      clientMessageId: `bb89n14-control-3-${randomUUID()}`,
    }), deadline, "submit_timeout", "control.queue")
    assertGate(second.disposition === "queued" && second.originalDisposition === "queued", "control_second_not_queued", "control.queue")
    assertGate(third.disposition === "queued" && third.originalDisposition === "queued", "control_third_not_queued", "control.queue")

    const receipts = [first, second, third]
    const terminalTurns = new Set()
    let capturedHead = null
    while (!terminalTurns.has(String(third.turnId))) {
      event = await nextEvent(stream, "control.fifo", deadline, budget)
      const owner = receipts.find((receipt) => correlated(event, receipt))
      assertGate(owner !== undefined, "control_uncorrelated_event", "control.fifo")
      events.push(sequenceEvidence(event))
      if (TERMINAL_KINDS.has(event.kind)) {
        assertGate(event.kind === "turn_completed", "control_non_completed_terminal", "control.fifo")
        assertGate(!terminalTurns.has(String(event.turnId)), "control_duplicate_terminal", "control.fifo")
        terminalTurns.add(String(event.turnId))
      }
    }
    const headSnapshot = await withinDeadline(session.snapshot(), deadline, "snapshot_timeout", "control.captured_head")
    capturedHead = { sequence: headSnapshot.headSequence, eventId: headSnapshot.headEventId }
    let last = events.at(-1)
    while (last.sequence < capturedHead.sequence) {
      event = await nextEvent(stream, "control.drain", deadline, budget)
      const owner = receipts.find((receipt) => correlated(event, receipt))
      assertGate(owner !== undefined, "control_uncorrelated_event", "control.drain")
      assertGate(!TERMINAL_KINDS.has(event.kind), "control_duplicate_terminal", "control.drain")
      last = sequenceEvidence(event)
      events.push(last)
    }
    assertGate(last.sequence === capturedHead.sequence && last.eventId === capturedHead.eventId, "control_head_not_drained", "control.drain")
    await advancePastYield(stream, controller, deadline, "control.cursor_commit")
    assertContiguousUnique(events, "control.fifo")

    const sequenceOf = (kind, receipt) => {
      const matches = events.filter((candidate) => candidate.kind === kind && candidate.turnId === String(receipt.turnId))
      assertGate(matches.length === 1, "control_order_event_count", "control.fifo")
      return matches[0].sequence
    }
    const terminalFirst = sequenceOf("turn_completed", first)
    const startSecond = sequenceOf("turn_started", second)
    const terminalSecond = sequenceOf("turn_completed", second)
    const startThird = sequenceOf("turn_started", third)
    const terminalThird = sequenceOf("turn_completed", third)
    assertGate(
      terminalFirst < startSecond
        && startSecond < terminalSecond
        && terminalSecond < startThird
        && startThird < terminalThird,
      "control_fifo_order_violation",
      "control.fifo",
    )

    const finalSnapshot = await withinDeadline(session.snapshot(), deadline, "snapshot_timeout", "control.final_snapshot")
    requireIdle(finalSnapshot, "control.final_snapshot")
    assertGate(finalSnapshot.headSequence === capturedHead.sequence && finalSnapshot.headEventId === capturedHead.eventId, "control_final_head_mismatch", "control.final_snapshot")
    for (const receipt of receipts) {
      const terminals = terminalFor(finalSnapshot, receipt)
      assertGate(terminals.length === 1 && terminals[0].outcome === "completed", "control_terminal_snapshot_mismatch", "control.final_snapshot")
      assertGate(terminals[0].originalDisposition === receipt.originalDisposition, "control_terminal_disposition_mismatch", "control.final_snapshot")
    }

    await verifyConfiguration()
    return {
      classification: "provider-free synthetic control",
      sessionId: String(session.sessionId),
      fixtures: { temporary: true, outsideRepository: true, controlledDelayMs: SYNTHETIC_HOLD_MS },
      initialSnapshot: projectSnapshot(initial, "control.evidence"),
      firstReceipt: receiptEvidence(first),
      attachSnapshotBefore: beforeEvidence,
      attachSnapshotAfter: afterEvidence,
      attachUnchanged: true,
      secondReceipt: receiptEvidence(second),
      thirdReceipt: receiptEvidence(third),
      fifoSequences: { terminalFirst, startSecond, terminalSecond, startThird, terminalThird },
      sequenceTrace: events,
      capturedHead,
      finalSnapshot: projectSnapshot(finalSnapshot, "control.evidence"),
    }
  } finally {
    await requiredCleanup([
      () => closeIterator(stream, controller, cleanupDeadline),
      ...(attached === null ? [] : [() => attached.close()]),
      ...(session === null ? [] : [() => session.close()]),
      ...(controlWorkspace === null ? [] : [() => removeRequired(controlWorkspace, { recursive: true, force: true })]),
      () => removeRequired(fixtureRoot, { recursive: true, force: true }),
    ], cleanupDeadline)
  }
}

const readLimitedBody = async (response, maximum, deadline, stage, controller) => {
  assertGate(response.body !== null, "missing_response_body", stage)
  const reader = response.body.getReader()
  const chunks = []
  let total = 0
  try {
    while (true) {
      const item = await withinDeadline(reader.read(), deadline, "response_timeout", stage)
      if (item.done) break
      total += item.value.byteLength
      assertGate(total <= maximum, "response_body_budget_exceeded", stage)
      chunks.push(item.value)
    }
  } finally {
    controller.abort()
    void reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
  return Buffer.concat(chunks, total).toString("utf8")
}

const readBackendProvenance = async ({ baseUrl, authToken, expectedCommit, deadline }) => {
  const controller = new AbortController()
  let response
  try {
    response = await withinDeadline(fetch(new URL("/v1/status", baseUrl), {
      method: "GET",
      headers: {
        Accept: "application/json",
        ...(authToken === undefined ? {} : { Authorization: `Bearer ${authToken}` }),
      },
      redirect: "error",
      signal: controller.signal,
    }), deadline, "backend_identity_timeout", "provenance.backend")
  } catch (error) {
    controller.abort()
    if (error instanceof GateFailure) throw error
    fail("backend_identity_unavailable", "provenance.backend")
  }
  let body
  try {
    assertGate(response.ok, "backend_identity_http_failure", "provenance.backend")
    body = JSON.parse(await readLimitedBody(response, 64 * 1024, deadline, "provenance.backend", controller))
  } catch (error) {
    controller.abort()
    if (error instanceof GateFailure) throw error
    fail("backend_identity_invalid_json", "provenance.backend")
  } finally {
    controller.abort()
  }
  const revision = body && typeof body === "object" ? body.served_revision : null
  const commit = revision && typeof revision === "object" ? revision.commit : null
  assertGate(commit === expectedCommit, "backend_commit_mismatch", "provenance.backend")
  assertGate(revision.dirty === false, "backend_revision_not_clean", "provenance.backend")
  return {
    commit,
    dirty: false,
    protocolVersion: body.protocol_version === null || body.protocol_version === undefined
      ? null
      : boundedText(body.protocol_version, "backend_protocol_version", "provenance.backend", 128),
    engineVersion: body.engine_version === null || body.engine_version === undefined
      ? null
      : boundedText(body.engine_version, "backend_engine_version", "provenance.backend", 128),
  }
}

const verifyFileDigest = async (path, expected, stage, deadline) => {
  let handle = null
  try {
    handle = await withinDeadline(
      open(path, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)),
      deadline,
      "client_artifact_timeout",
      stage,
    )
    const before = await withinDeadline(handle.stat({ bigint: true }), deadline, "client_artifact_timeout", stage)
    assertGate(before.isFile() && before.size <= BigInt(16 * 1024 * 1024), "client_artifact_unavailable", stage)
    const bytes = await withinDeadline(handle.readFile(), deadline, "client_artifact_timeout", stage)
    const after = await withinDeadline(handle.stat({ bigint: true }), deadline, "client_artifact_timeout", stage)
    assertGate(
      before.dev === after.dev
        && before.ino === after.ino
        && before.size === after.size
        && before.mtimeNs === after.mtimeNs
        && before.ctimeNs === after.ctimeNs,
      "client_artifact_changed",
      stage,
    )
    assertGate(sha256(bytes) === expected, "client_artifact_mismatch", stage)
  } catch (error) {
    if (error instanceof GateFailure) throw error
    fail("client_artifact_unavailable", stage)
  } finally {
    await handle?.close().catch(() => undefined)
  }
}

const verifiedGit = async (arguments_, deadline, options = {}) => {
  const remaining = deadline - Date.now()
  assertGate(remaining > 0, "absolute_deadline_exceeded", "provenance.client")
  return execFileAsync("/usr/bin/git", arguments_, {
    cwd: REPOSITORY_ROOT,
    env: FIXED_GIT_ENVIRONMENT,
    timeout: remaining,
    killSignal: "SIGKILL",
    maxBuffer: 16 * 1024 * 1024,
    ...options,
  })
}
const validateTrustedDirectory = async (requestedDirectory, deadline) => {
  const directory = resolve(requestedDirectory)
  const canonical = await withinDeadline(
    realpath(directory),
    deadline,
    "config_snapshot_timeout",
    "config.snapshot",
  ).catch(() => null)
  assertGate(canonical === directory, "config_trusted_root_invalid", "config.snapshot")
  const ancestors = []
  for (let current = directory; ; current = dirname(current)) {
    ancestors.push(current)
    if (dirname(current) === current) break
  }
  for (const path of ancestors.reverse()) {
    const info = await withinDeadline(
      lstat(path),
      deadline,
      "config_snapshot_timeout",
      "config.snapshot",
    ).catch(() => null)
    assertGate(info?.isDirectory() && !info.isSymbolicLink(), "config_trusted_root_invalid", "config.snapshot")
  }
  return directory
}

const resolveTrustedExternalRoot = async (deadline) => {
  let stdout
  try {
    ;({ stdout } = await verifiedGit(
      ["rev-parse", "--path-format=absolute", "--git-common-dir"],
      deadline,
      { encoding: "utf8", maxBuffer: 4096 },
    ))
  } catch {
    fail("config_trusted_root_invalid", "config.snapshot")
  }
  assertGate(
    typeof stdout === "string"
      && stdout.endsWith("\n")
      && !stdout.slice(0, -1).includes("\n")
      && !stdout.includes("\0"),
    "config_trusted_root_invalid",
    "config.snapshot",
  )
  const gitCommonDirectory = resolve(stdout.slice(0, -1))
  assertGate(isAbsolute(gitCommonDirectory) && basename(gitCommonDirectory) === ".git", "config_trusted_root_invalid", "config.snapshot")
  const primaryCheckout = dirname(gitCommonDirectory)
  const primaryCheckoutParent = dirname(primaryCheckout)
  const trustedExternalRoot = join(primaryCheckoutParent, "other_harness_refs")
  assertGate(
    dirname(trustedExternalRoot) === primaryCheckoutParent && isInside(primaryCheckoutParent, trustedExternalRoot),
    "config_trusted_root_invalid",
    "config.snapshot",
  )
  await validateTrustedDirectory(gitCommonDirectory, deadline)
  return validateTrustedDirectory(trustedExternalRoot, deadline)
}
const runIdentityTool = async (executable, arguments_, deadline, cwd = REPOSITORY_ROOT) => {
  const remaining = deadline - Date.now()
  assertGate(remaining > 0, "absolute_deadline_exceeded", "provenance.listener")
  try {
    return await execFileAsync(executable, arguments_, {
      cwd,
      env: FIXED_GIT_ENVIRONMENT,
      encoding: "utf8",
      timeout: remaining,
      killSignal: "SIGKILL",
      maxBuffer: 64 * 1024,
    })
  } catch (error) {
    if (
      (error?.killed === true && error?.signal === "SIGKILL")
      || Date.now() >= deadline
    ) {
      fail("absolute_deadline_exceeded", "provenance.listener")
    }
    fail("backend_listener_identity_invalid", "provenance.listener")
  }
}

const assertNoGitReplacementRefs = async (root, deadline, stage) => {
  const { stdout } = await runIdentityTool(
    "/usr/bin/git",
    ["for-each-ref", "--format=%(refname)", "refs/replace/"],
    deadline,
    root,
  )
  assertGate(stdout === "", "git_replacement_refs_forbidden", stage)
}

export const assertNoGitReplacementRefsForTest = async (
  root,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
) => assertNoGitReplacementRefs(root, deadline, "provenance.listener")

const attestBackendListener = async (baseUrl, expectedCommit, deadline, expectedIdentity = null) => {
  const parsed = new URL(baseUrl)
  const host = parsed.hostname
  const port = parsed.port === "" ? (parsed.protocol === "https:" ? 443 : 80) : Number(parsed.port)
  assertGate(Number.isSafeInteger(port) && port > 0 && port <= 65535, "backend_listener_identity_invalid", "provenance.listener")
  const { stdout: listenerOutput } = await runIdentityTool(
    "/usr/sbin/lsof",
    ["-nP", "-a", `-iTCP:${port}`, "-sTCP:LISTEN", "-Fpn"],
    deadline,
  )
  const pids = new Set()
  const endpoints = []
  for (const line of listenerOutput.split("\n")) {
    if (/^p[1-9]\d*$/.test(line)) pids.add(Number(line.slice(1)))
    if (line.startsWith("n")) endpoints.push(line.slice(1))
  }
  const expectedEndpoint = host === "[::1]" ? `[::1]:${port}` : `${host}:${port}`
  assertGate(
    pids.size === 1 && endpoints.includes(expectedEndpoint),
    "backend_listener_identity_invalid",
    "provenance.listener",
  )
  const pid = [...pids][0]
  const { stdout: commandOutput } = await runIdentityTool("/bin/ps", ["-p", String(pid), "-o", "command="], deadline)
  const command = commandOutput.trim()
  const fixtureMatch = /^((?:\/[^/\s]+)+\/python(?:3(?:\.\d+)?)?)\s+(\/\S+\.py)\s+--host\s+(\S+)\s+--port\s+([1-9]\d*)$/i.exec(command)
  const trackedFixture = fixtureMatch !== null
    && fixtureMatch[3] === host.replace(/^\[|\]$/g, "")
    && Number(fixtureMatch[4]) === port
  assertGate(trackedFixture, "backend_listener_identity_invalid", "provenance.listener")
  const executableToken = command.slice(0, command.indexOf(" "))
  const canonicalExecutable = await withinDeadline(
    realpath(executableToken),
    deadline,
    "backend_listener_identity_timeout",
    "provenance.listener",
  ).catch(() => null)
  assertGate(canonicalExecutable !== null, "backend_listener_identity_invalid", "provenance.listener")
  const { stdout: executableOutput } = await runIdentityTool(
    "/usr/sbin/lsof",
    ["-a", "-p", String(pid), "-d", "txt", "-Fn"],
    deadline,
  )
  const executablePaths = executableOutput.split("\n").filter((line) => line.startsWith("n")).map((line) => line.slice(1))
  const canonicalExecutablePaths = await Promise.all(executablePaths.map((path) => withinDeadline(
    realpath(path),
    deadline,
    "backend_listener_identity_timeout",
    "provenance.listener",
  ).catch(() => null)))
  assertGate(canonicalExecutablePaths.includes(canonicalExecutable), "backend_listener_identity_invalid", "provenance.listener")
  const { stdout: cwdOutput } = await runIdentityTool(
    "/usr/sbin/lsof",
    ["-a", "-p", String(pid), "-d", "cwd", "-Fn"],
    deadline,
  )
  const cwdValues = cwdOutput.split("\n").filter((line) => line.startsWith("n")).map((line) => line.slice(1))
  assertGate(cwdValues.length === 1 && isAbsolute(cwdValues[0]), "backend_listener_identity_invalid", "provenance.listener")
  const cwd = await validateTrustedDirectory(cwdValues[0], deadline)
  const { stdout: rootOutput } = await runIdentityTool(
    "/usr/bin/git",
    ["rev-parse", "--path-format=absolute", "--show-toplevel"],
    deadline,
    cwd,
  )
  assertGate(rootOutput === `${cwd}\n`, "backend_listener_identity_invalid", "provenance.listener")
  const { stdout: headOutput } = await runIdentityTool("/usr/bin/git", ["rev-parse", "HEAD"], deadline, cwd)
  assertGate(headOutput === `${expectedCommit}\n`, "backend_listener_commit_mismatch", "provenance.listener")
  const { stdout: statusOutput } = await runIdentityTool(
    "/usr/bin/git",
    ["status", "--porcelain=v1", "--untracked-files=all"],
    deadline,
    cwd,
  )
  assertGate(statusOutput === "", "backend_listener_not_clean", "provenance.listener")
  if (trackedFixture) {
    const entrypoint = await withinDeadline(
      realpath(fixtureMatch[2]),
      deadline,
      "backend_listener_identity_timeout",
      "provenance.listener",
    ).catch(() => null)
    assertGate(entrypoint !== null && isInside(cwd, entrypoint), "backend_listener_identity_invalid", "provenance.listener")
    const repositoryPath = relative(cwd, entrypoint)
    assertGate(repositoryPath !== "" && !repositoryPath.startsWith(`..${sep}`), "backend_listener_identity_invalid", "provenance.listener")
    const { stdout: trackedOutput } = await runIdentityTool(
      "/usr/bin/git",
      ["ls-files", "--error-unmatch", "--", repositoryPath],
      deadline,
      cwd,
    )
    assertGate(trackedOutput === `${repositoryPath}\n`, "backend_listener_identity_invalid", "provenance.listener")
    const { stdout: committedEntrypoint } = await runIdentityTool(
      "/usr/bin/git",
      ["show", `HEAD:${repositoryPath}`],
      deadline,
      cwd,
    )
    const liveEntrypoint = await withinDeadline(
      readFile(entrypoint, "utf8"),
      deadline,
      "backend_listener_identity_timeout",
      "provenance.listener",
    )
    assertGate(committedEntrypoint === liveEntrypoint, "backend_listener_identity_invalid", "provenance.listener")
  }
  const identity = {
    pid,
    cwd,
    command,
    expectedEndpoint,
    commit: expectedCommit,
    kind: "tracked-python-fixture",
  }
  if (expectedIdentity !== null) {
    assertGate(
      identity.pid === expectedIdentity.pid
        && identity.cwd === expectedIdentity.cwd
        && identity.command === expectedIdentity.command
        && identity.expectedEndpoint === expectedIdentity.expectedEndpoint
        && identity.commit === expectedIdentity.commit
        && identity.kind === expectedIdentity.kind,
      "backend_listener_changed",
      "provenance.listener",
    )
  }
  return identity
}

const OWNED_BACKEND_BOOTSTRAP = String.raw`import json, os, sys
control = json.loads(sys.stdin.buffer.readline(65537))
if set(control) != {"root", "host", "port", "token", "commit"}:
    raise SystemExit(70)
root = control["root"]
host = control["host"]
port = control["port"]
token = control["token"]
commit = control["commit"]
if not isinstance(root, str) or not isinstance(host, str) or not isinstance(port, int) or not isinstance(token, str):
    raise SystemExit(70)
if not isinstance(commit, str) or len(commit) != 40 or any(value not in "0123456789abcdef" for value in commit):
    raise SystemExit(70)
os.environ["BREADBOARD_API_TOKEN"] = token
sys.dont_write_bytecode = True
import uvicorn
uvicorn_path = os.path.realpath(uvicorn.__file__)
runtime_roots = [os.path.realpath(value) for value in [sys.prefix, sys.base_prefix, *sys.path] if value]
if not any(os.path.commonpath([candidate, uvicorn_path]) == candidate for candidate in runtime_roots):
 raise SystemExit(70)
if os.path.commonpath([root, uvicorn_path]) == root:
 raise SystemExit(70)
sys.path.insert(0, root)
from agentic_coder_prototype.api.cli_bridge import app as gate_app
gate_app.ENGINE_PROVENANCE = {
    "repo_root": "gate-owned-immutable-snapshot",
    "commit": commit,
    "branch": "HEAD",
    "dirty": False,
}
uvicorn.Server(uvicorn.Config(gate_app.create_app(), host=host, port=port, access_log=False, log_config=None)).run()
`

const PROVIDER_ENVIRONMENT_NAMES = new Set([
  "ANTHROPIC_API_KEY",
  "AWS_ACCESS_KEY_ID",
  "AWS_DEFAULT_REGION",
  "AWS_REGION",
  "AWS_SECRET_ACCESS_KEY",
  "AWS_SESSION_TOKEN",
  "AZURE_OPENAI_API_KEY",
  "AZURE_OPENAI_ENDPOINT",
  "GEMINI_API_KEY",
  "GOOGLE_API_KEY",
  "GROQ_API_KEY",
  "MISTRAL_API_KEY",
  "BREADBOARD_OPENAI_AUTH_BASE_URL",
  "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
  "OPENAI_API_KEY",
  "OPENROUTER_API_KEY",
  "XAI_API_KEY",
])
const FORWARDED_OPENAI_ENVIRONMENT_NAMES = new Set([
  "OPENAI_API_KEY",
  "BREADBOARD_OPENAI_AUTH_BASE_URL",
  "BREADBOARD_OPENAI_AUTH_HEADERS_JSON",
])

const DEFAULT_OPENAI_PROVIDER_ENDPOINT = "https://api.openai.com/v1"
const APPROVED_OPENAI_PROVIDER_ENDPOINTS = new Set([
  DEFAULT_OPENAI_PROVIDER_ENDPOINT,
  "https://chatgpt.com/backend-api/codex",
])
const providerEndpointIdentity = () => {
  const rawValue = CHILD_ENVIRONMENT.BREADBOARD_OPENAI_AUTH_BASE_URL
  let parsed
  try {
    parsed = new URL(rawValue ?? DEFAULT_OPENAI_PROVIDER_ENDPOINT)
  } catch {
    fail("provider_endpoint_unapproved", "provenance.listener")
  }
  assertGate(
    parsed.protocol === "https:"
      && parsed.username === ""
      && parsed.password === ""
      && parsed.search === ""
      && parsed.hash === "",
    "provider_endpoint_unapproved",
    "provenance.listener",
  )
  const normalized = parsed.href.replace(/\/$/, "")
  assertGate(
    APPROVED_OPENAI_PROVIDER_ENDPOINTS.has(normalized),
    "provider_endpoint_unapproved",
    "provenance.listener",
  )
  return {
    endpoint: normalized,
    digest: sha256(Buffer.from(normalized, "utf8")),
  }
}
const providerEvidenceValues = (environment) => {
  const values = [...PROVIDER_ENVIRONMENT_NAMES]
    .map((name) => environment[name])
    .filter((value) => typeof value === "string" && value.length > 0)
  const headers = environment.BREADBOARD_OPENAI_AUTH_HEADERS_JSON
  let parsedHeaders = null
  if (typeof headers === "string" && headers.length > 0) {
    try {
      parsedHeaders = JSON.parse(headers)
    } catch {
      // The canonical provider ignores malformed projected headers; the raw value remains tainted.
    }
  }
  if (parsedHeaders !== null && typeof parsedHeaders === "object" && !Array.isArray(parsedHeaders)) {
    for (const value of Object.values(parsedHeaders)) {
      if (typeof value !== "string" || value.length === 0) continue
      assertGate(
        value.length >= 8 && value.length <= 8192 && !/[\r\n\0]/.test(value),
        "provider_environment_invalid",
        "provenance.listener",
      )
      values.push(value)
    }
  }
  return [...new Set(values)]
}
const capturedProviderEvidenceValues = () => providerEvidenceValues(CHILD_ENVIRONMENT)
const APPROVED_BACKEND_RUNTIMES = Object.freeze([
  Object.freeze({
    resolvedPath: "/opt/homebrew/Cellar/python@3.11/3.11.15_3/Frameworks/Python.framework/Versions/3.11/bin/python3.11",
    version: "3.11.15",
    executableSha256: "sha256:6efa04ba77fc8c100a1238a7a3569517e2cdf5016f2be367dea9441f5b7cbe3b",
    runtimeClosureSha256: "sha256:7880034b137e7c857fc2221f339cef752e12b278e15a5f3e85f9599803209fbe",
    count: 68_002,
    bytes: 1_992_662_684,
  }),
])

const BACKEND_RUNTIME_PROBE = String.raw`import json, os, site, sys
value = {
    "basePrefix": os.path.realpath(sys.base_prefix),
    "dontWriteBytecode": bool(sys.dont_write_bytecode),
    "executable": os.path.realpath(sys.executable),
    "ignoreEnvironment": bool(sys.flags.ignore_environment),
    "isolated": bool(sys.flags.isolated),
    "noUserSite": bool(sys.flags.no_user_site),
    "prefix": os.path.realpath(sys.prefix),
    "sysPath": [os.path.realpath(value) for value in sys.path],
    "userSite": site.getusersitepackages(),
    "version": ".".join(str(value) for value in sys.version_info[:3]),
}
sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
`

const closedRuntimeEnvironment = () => ({
  LANG: "C",
  LC_ALL: "C",
  PATH: "/usr/bin:/bin",
  PYTHONDONTWRITEBYTECODE: "1",
  PYTHONNOUSERSITE: "1",
})

const stableRuntimeExecutable = async (requestedPath, deadline) => {
  assertGate(isAbsolute(requestedPath), "backend_python_unapproved", "provenance.listener")
  const requestedInfo = await withinDeadline(lstat(requestedPath), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
  assertGate(requestedInfo?.isFile() || requestedInfo?.isSymbolicLink(), "backend_python_unapproved", "provenance.listener")
  const resolvedPath = await withinDeadline(realpath(requestedPath), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
  assertGate(typeof resolvedPath === "string" && /\/python(?:3(?:\.\d+)?)?$/i.test(resolvedPath), "backend_python_unapproved", "provenance.listener")
  await validateTrustedDirectory(dirname(resolvedPath), deadline)
  let handle = null
  try {
    handle = await withinDeadline(
      open(resolvedPath, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)),
      deadline,
      "backend_runtime_timeout",
      "provenance.listener",
    )
    const before = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
    assertGate(
      before.isFile()
        && before.size <= BigInt(MAX_RUNTIME_FILE_BYTES)
        && (before.mode & 0o111n) !== 0n
        && (before.mode & 0o022n) === 0n,
      "backend_python_unapproved",
      "provenance.listener",
    )
    const digest = createHash("sha256")
    const buffer = Buffer.allocUnsafe(64 * 1024)
    let offset = 0
    while (offset < Number(before.size)) {
      const { bytesRead } = await withinDeadline(
        handle.read(buffer, 0, Math.min(buffer.byteLength, Number(before.size) - offset), offset),
        deadline,
        "backend_runtime_timeout",
        "provenance.listener",
      )
      assertGate(bytesRead > 0, "backend_runtime_changed", "provenance.listener")
      digest.update(buffer.subarray(0, bytesRead))
      offset += bytesRead
    }
    const after = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
    assertGate(
      before.dev === after.dev
        && before.ino === after.ino
        && before.size === after.size
        && before.mtimeNs === after.mtimeNs
        && before.ctimeNs === after.ctimeNs,
      "backend_runtime_changed",
      "provenance.listener",
    )
    return {
      resolvedPath,
      executableSha256: `sha256:${digest.digest("hex")}`,
      metadata: before,
    }
  } catch (error) {
    if (error instanceof GateFailure) throw error
    fail("backend_python_unapproved", "provenance.listener")
  } finally {
    await handle?.close().catch(() => undefined)
  }
}

const runtimeMetadataUnchanged = (before, after) => (
  before.dev === after.dev
  && before.ino === after.ino
  && before.mode === after.mode
  && before.size === after.size
  && before.mtimeNs === after.mtimeNs
  && before.ctimeNs === after.ctimeNs
)

const computeRuntimeClosure = async (roots, deadline, executablePath) => {
  const digest = createHash("sha256")
  const visitedDirectories = new Map()
  const visitedFiles = new Map()
  const machOFiles = new Map()
  let count = 0
  let bytes = 0
  const frame = (value) => {
    const payload = Buffer.from(JSON.stringify(value), "utf8")
    digest.update(`${payload.byteLength}\n`)
    digest.update(payload)
  }
  const identityFor = (info) => `${info.dev}:${info.ino}`
  const hashFile = async (path, logicalPath, type) => {
    let handle = null
    try {
      handle = await withinDeadline(
        open(path, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)),
        deadline,
        "backend_runtime_timeout",
        "provenance.listener",
      )
      const before = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
      assertGate(before.isFile() && before.size <= BigInt(MAX_RUNTIME_FILE_BYTES), "backend_runtime_unsupported", "provenance.listener")
      const identity = identityFor(before)
      const prior = visitedFiles.get(identity)
      if (prior !== undefined) {
        assertGate(runtimeMetadataUnchanged(prior.metadata, before), "backend_runtime_changed", "provenance.listener")
        frame({ path: logicalPath, reference: prior.path, type: `${type}-reference` })
        return
      }
      count += 1
      bytes += Number(before.size)
      assertGate(count <= MAX_RUNTIME_CLOSURE_FILES && bytes <= MAX_RUNTIME_CLOSURE_BYTES, "backend_runtime_budget", "provenance.listener")
      const fileDigest = createHash("sha256")
      const buffer = Buffer.allocUnsafe(64 * 1024)
      let magic = null
      let offset = 0
      while (offset < Number(before.size)) {
        const { bytesRead } = await withinDeadline(
          handle.read(buffer, 0, Math.min(buffer.byteLength, Number(before.size) - offset), offset),
          deadline,
          "backend_runtime_timeout",
          "provenance.listener",
        )
        assertGate(bytesRead > 0, "backend_runtime_changed", "provenance.listener")
        fileDigest.update(buffer.subarray(0, bytesRead))
        if (offset === 0 && bytesRead >= 4) magic = buffer.readUInt32BE(0)
        offset += bytesRead
      }
      const after = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
      assertGate(runtimeMetadataUnchanged(before, after), "backend_runtime_changed", "provenance.listener")
      visitedFiles.set(identity, { metadata: after, path: logicalPath })
      if (new Set([0xfeedface, 0xcefaedfe, 0xfeedfacf, 0xcffaedfe, 0xcafebabe, 0xbebafeca]).has(magic)) {
        machOFiles.set(identity, { path, logicalPath })
      }
      frame({
        hash: `sha256:${fileDigest.digest("hex")}`,
        mode: Number(before.mode & 0o7777n),
        path: logicalPath,
        size: Number(before.size),
        type,
      })
    } catch (error) {
      if (error instanceof GateFailure) throw error
      fail("backend_runtime_unsupported", "provenance.listener")
    } finally {
      await handle?.close().catch(() => undefined)
    }
  }
  const scan = async (path, logicalPath, activeDirectories) => {
    const before = await withinDeadline(lstat(path, { bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
    assertGate(before !== null, "backend_runtime_unsupported", "provenance.listener")
    if (before.isSymbolicLink()) {
      const linkText = await withinDeadline(readlink(path), deadline, "backend_runtime_timeout", "provenance.listener")
      const target = await withinDeadline(realpath(path), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
      assertGate(typeof target === "string", "backend_runtime_unsupported", "provenance.listener")
      const afterLink = await withinDeadline(lstat(path, { bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
      const afterText = await withinDeadline(readlink(path), deadline, "backend_runtime_timeout", "provenance.listener")
      assertGate(runtimeMetadataUnchanged(before, afterLink) && linkText === afterText, "backend_runtime_changed", "provenance.listener")
      frame({ mode: Number(before.mode & 0o7777n), path: logicalPath, target: linkText, type: "symlink" })
      const targetInfo = await withinDeadline(lstat(target, { bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
      assertGate(targetInfo !== null && !targetInfo.isSymbolicLink(), "backend_runtime_unsupported", "provenance.listener")
      if (targetInfo.isFile()) {
        await hashFile(target, `${logicalPath}/@target`, "symlink-target-file")
        return
      }
      assertGate(targetInfo.isDirectory(), "backend_runtime_unsupported", "provenance.listener")
      await scan(target, `${logicalPath}/@target`, activeDirectories)
      return
    }
    if (before.isFile()) {
      await hashFile(path, logicalPath, "file")
      return
    }
    assertGate(before.isDirectory(), "backend_runtime_unsupported", "provenance.listener")
    const canonical = await withinDeadline(realpath(path), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
    assertGate(canonical === path && !activeDirectories.has(canonical), "backend_runtime_unsupported", "provenance.listener")
    const identity = identityFor(before)
    const prior = visitedDirectories.get(identity)
    if (prior !== undefined) {
      assertGate(runtimeMetadataUnchanged(prior.metadata, before), "backend_runtime_changed", "provenance.listener")
      frame({ path: logicalPath, reference: prior.path, type: "directory-reference" })
      return
    }
    await validateTrustedDirectory(canonical, deadline)
    visitedDirectories.set(identity, { metadata: before, path: logicalPath })
    const nextActive = new Set(activeDirectories)
    nextActive.add(canonical)
    const names = await withinDeadline(readdir(canonical), deadline, "backend_runtime_timeout", "provenance.listener")
    names.sort()
    frame({ mode: Number(before.mode & 0o7777n), path: logicalPath, type: "directory" })
    for (const name of names) {
      assertGate(name !== "." && name !== ".." && !name.includes(sep), "backend_runtime_unsupported", "provenance.listener")
      await scan(join(canonical, name), `${logicalPath}/${name}`, nextActive)
    }
    const after = await withinDeadline(lstat(canonical, { bigint: true }), deadline, "backend_runtime_timeout", "provenance.listener")
    assertGate(runtimeMetadataUnchanged(before, after), "backend_runtime_changed", "provenance.listener")
    visitedDirectories.set(identity, { metadata: after, path: logicalPath })
  }
  const descriptors = []
  for (const root of [...new Set(roots)].sort()) {
    const info = await withinDeadline(lstat(root), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
    if (info === null) {
      descriptors.push({ root, canonical: null, isDirectory: false })
      continue
    }
    const canonical = await withinDeadline(realpath(root), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
    assertGate(typeof canonical === "string", "backend_runtime_unsupported", "provenance.listener")
    const canonicalInfo = await withinDeadline(lstat(canonical), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
    assertGate(canonicalInfo?.isFile() || canonicalInfo?.isDirectory(), "backend_runtime_unsupported", "provenance.listener")
    descriptors.push({ root, canonical, isDirectory: canonicalInfo.isDirectory() })
  }
  const selected = []
  for (const descriptor of descriptors
    .filter(({ canonical }) => canonical !== null)
    .sort((left, right) => left.canonical.length - right.canonical.length || left.canonical.localeCompare(right.canonical))) {
    const covering = selected.find(
      (candidate) => candidate.isDirectory && isInside(candidate.canonical, descriptor.canonical),
    )
    if (covering === undefined) selected.push(descriptor)
  }
  for (const descriptor of descriptors) {
    const covering = descriptor.canonical === null
      ? null
      : selected.find(
        (candidate) => candidate.canonical === descriptor.canonical
          || (candidate.isDirectory && isInside(candidate.canonical, descriptor.canonical)),
      )
    frame({
      canonical: descriptor.canonical,
      path: descriptor.root,
      selectedRoot: covering?.canonical ?? null,
      type: descriptor.canonical === null ? "missing-root" : "root-binding",
    })
  }
  selected.sort((left, right) => left.canonical.localeCompare(right.canonical))
  for (let index = 0; index < selected.length; index += 1) {
    await scan(selected[index].canonical, `root-${index}`, new Set())
  }
  const otool = async (path, option) => {
    const remaining = deadline - Date.now()
    assertGate(remaining > 0, "absolute_deadline_exceeded", "provenance.listener")
    try {
      const { stdout } = await execFileAsync("/usr/bin/otool", [option, path], {
        cwd: "/",
        encoding: "utf8",
        env: closedRuntimeEnvironment(),
        killSignal: "SIGKILL",
        maxBuffer: 1024 * 1024,
        timeout: remaining,
      })
      return stdout
    } catch {
      fail("backend_runtime_dependency_invalid", "provenance.listener")
    }
  }
  const loaderExpansion = (value, binaryPath) => {
    if (value === "@loader_path") return dirname(binaryPath)
    if (value === "@executable_path") return dirname(executablePath)
    if (value.startsWith("@loader_path/")) return join(dirname(binaryPath), value.slice("@loader_path/".length))
    if (value.startsWith("@executable_path/")) return join(dirname(executablePath), value.slice("@executable_path/".length))
    return value
  }
  const runtimeRoots = selected.filter(({ isDirectory }) => isDirectory).map(({ canonical }) => canonical)
  const processedMachO = new Set()
  while (true) {
    const pending = [...machOFiles.entries()]
      .filter(([identity]) => !processedMachO.has(identity))
      .sort((left, right) => left[1].path.localeCompare(right[1].path))[0]
    if (pending === undefined) break
    const [identity, binary] = pending
    processedMachO.add(identity)
    const loadOutput = await otool(binary.path, "-L")
    const rawDependencies = loadOutput.split("\n").slice(1).map((line) => {
      const marker = line.indexOf(" (compatibility version ")
      return marker === -1 ? null : line.slice(0, marker).trim()
    }).filter((value) => value !== null)
    const otoolLoadCommands = await otool(binary.path, "-l")
    const rpaths = []
    const installNames = []
    const commandLines = otoolLoadCommands.split("\n")
    for (let index = 0; index < commandLines.length; index += 1) {
      const command = commandLines[index].trim()
      if (command === "cmd LC_RPATH") {
        for (let cursor = index + 1; cursor < Math.min(commandLines.length, index + 6); cursor += 1) {
          const match = /^\s*path (.+) \(offset \d+\)$/.exec(commandLines[cursor])
          if (match !== null) {
            rpaths.push(loaderExpansion(match[1], binary.path))
            break
          }
        }
      } else if (command === "cmd LC_ID_DYLIB") {
        for (let cursor = index + 1; cursor < Math.min(commandLines.length, index + 6); cursor += 1) {
          const match = /^\s*name (.+) \(offset \d+\)$/.exec(commandLines[cursor])
          if (match !== null) {
            installNames.push(match[1])
            break
          }
        }
      }
    }
    for (const rawDependency of rawDependencies.sort()) {
      if (installNames.includes(rawDependency)) {
        frame({ dependency: rawDependency, from: binary.logicalPath, resolved: binary.path, type: "mach-o-self-install-name" })
        continue
      }
      if (
        isAbsolute(rawDependency)
        && (
          rawDependency === "/usr/lib"
          || rawDependency.startsWith("/usr/lib/")
          || rawDependency === "/System"
          || rawDependency.startsWith("/System/")
        )
      ) {
        assertGate(resolve(rawDependency) === rawDependency, "backend_runtime_dependency_invalid", "provenance.listener")
        frame({ dependency: rawDependency, from: binary.logicalPath, resolved: rawDependency, type: "system-dependency" })
        continue
      }
      const candidates = rawDependency.startsWith("@rpath/")
        ? rpaths.map((rpath) => join(rpath, rawDependency.slice("@rpath/".length)))
        : [loaderExpansion(rawDependency, binary.path)]
      let resolvedDependency = null
      for (const candidate of candidates) {
        if (!isAbsolute(candidate)) continue
        resolvedDependency = await withinDeadline(realpath(candidate), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
        if (resolvedDependency !== null) break
      }
      if (typeof resolvedDependency !== "string") {
        const error = new GateFailure("backend_runtime_dependency_invalid", "provenance.listener")
        error.runtimeDiagnostic = {
          binary: binary.path,
          rawDependency,
          candidates,
          otoolLoadCommands,
        }
        throw error
      }
      if (resolvedDependency === "/usr/lib" || resolvedDependency.startsWith("/usr/lib/") || resolvedDependency === "/System" || resolvedDependency.startsWith("/System/")) {
        frame({ dependency: rawDependency, from: binary.logicalPath, resolved: resolvedDependency, type: "system-dependency" })
        continue
      }
      const dependencyInfo = await withinDeadline(lstat(resolvedDependency), deadline, "backend_runtime_timeout", "provenance.listener").catch(() => null)
      assertGate(dependencyInfo?.isFile() && !dependencyInfo.isSymbolicLink(), "backend_runtime_dependency_invalid", "provenance.listener")
      const covered = runtimeRoots.some((root) => isInside(root, resolvedDependency))
      frame({
        dependency: rawDependency,
        from: binary.logicalPath,
        resolved: resolvedDependency,
        type: covered ? "runtime-root-dependency" : "external-runtime-dependency",
      })
      await hashFile(resolvedDependency, `dependency/${resolvedDependency}`, "mach-o-dependency")
    }
  }
  return {
    runtimeClosureSha256: `sha256:${digest.digest("hex")}`,
    count,
    bytes,
  }
}

const computeBackendRuntimeCandidate = async (requestedPath, deadline, pinnedExecutable = null) => {
  const executable = pinnedExecutable ?? await stableRuntimeExecutable(requestedPath, deadline)
  const remaining = deadline - Date.now()
  assertGate(remaining > 0, "absolute_deadline_exceeded", "provenance.listener")
  let probeOutput
  try {
    ;({ stdout: probeOutput } = await execFileAsync(
      executable.resolvedPath,
      ["-I", "-B", "-c", BACKEND_RUNTIME_PROBE],
      {
        cwd: "/",
        encoding: "utf8",
        env: closedRuntimeEnvironment(),
        killSignal: "SIGKILL",
        maxBuffer: 64 * 1024,
        timeout: remaining,
      },
    ))
  } catch {
    fail("backend_runtime_probe_failed", "provenance.listener")
  }
  let probe
  try {
    probe = JSON.parse(probeOutput)
  } catch {
    fail("backend_runtime_probe_failed", "provenance.listener")
  }
  exactObject(
    probe,
    ["basePrefix", "dontWriteBytecode", "executable", "ignoreEnvironment", "isolated", "noUserSite", "prefix", "sysPath", "userSite", "version"],
    "backend_runtime_probe_invalid",
    "provenance.listener",
  )
  assertGate(
    probe.executable === executable.resolvedPath
      && typeof probe.version === "string"
      && /^\d+\.\d+\.\d+$/.test(probe.version)
      && probe.isolated === true
      && probe.ignoreEnvironment === true
      && probe.noUserSite === true
      && probe.dontWriteBytecode === true
      && isAbsolute(probe.prefix)
      && isAbsolute(probe.basePrefix)
      && Array.isArray(probe.sysPath)
      && probe.sysPath.length > 0
      && probe.sysPath.length <= 64
      && probe.sysPath.every((path) => typeof path === "string" && isAbsolute(path) && path !== "/"),
    "backend_runtime_probe_invalid",
    "provenance.listener",
  )
  const userSites = (Array.isArray(probe.userSite) ? probe.userSite : [probe.userSite])
    .filter((path) => typeof path === "string" && isAbsolute(path))
    .map((path) => resolve(path))
  assertGate(
    probe.sysPath.every((path) => userSites.every((userSite) => !isInside(userSite, path))),
    "backend_runtime_user_site",
    "provenance.listener",
  )
  const closure = await computeRuntimeClosure(
    [executable.resolvedPath, probe.prefix, probe.basePrefix, ...probe.sysPath],
    deadline,
  )
  const rehashed = await stableRuntimeExecutable(executable.resolvedPath, deadline)
  assertGate(
    rehashed.executableSha256 === executable.executableSha256,
    "backend_runtime_changed",
    "provenance.listener",
  )
  return {
    resolvedPath: executable.resolvedPath,
    version: probe.version,
    executableSha256: executable.executableSha256,
    runtimeClosureSha256: closure.runtimeClosureSha256,
    count: closure.count,
    bytes: closure.bytes,
  }
}

export const computeApprovedBackendRuntimeCandidateForTest = async (path, deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS) => (
  computeBackendRuntimeCandidate(path, deadline)
)

export const computeApprovedBackendRuntimeDiagnosticForTest = async (
  path,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
) => {
  try {
    await computeBackendRuntimeCandidate(path, deadline)
    return null
  } catch (error) {
    if (error instanceof GateFailure && error.runtimeDiagnostic !== undefined) return error.runtimeDiagnostic
    throw error
  }
}

const approvedBackendRuntime = async (requestedPath, deadline) => {
  const executable = await stableRuntimeExecutable(requestedPath, deadline)
  const approved = APPROVED_BACKEND_RUNTIMES.find(
    (candidate) => candidate.resolvedPath === executable.resolvedPath
      && candidate.executableSha256 === executable.executableSha256,
  )
  assertGate(approved !== undefined, "backend_python_unapproved", "provenance.listener")
  const actual = await computeBackendRuntimeCandidate(requestedPath, deadline, executable)
  assertGate(isDeepStrictEqual(actual, approved), "backend_runtime_unapproved", "provenance.listener")
  return actual
}

const forwardedProviderEnvironment = (expectedProviderModel) => {
  assertGate(OPENAI_PROVIDER_MODEL.test(expectedProviderModel), "provider_model_route_unapproved", "provenance.listener")
  const environment = closedRuntimeEnvironment()
  const providerEndpoint = providerEndpointIdentity()
  const forbiddenValues = providerEvidenceValues(CHILD_ENVIRONMENT)
  for (const name of FORWARDED_OPENAI_ENVIRONMENT_NAMES) {
    const value = CHILD_ENVIRONMENT[name]
    if (typeof value !== "string" || value.length === 0) continue
    assertGate(
      value.length >= 8 && value.length <= 8192 && !/[\r\n\0]/.test(value),
      "provider_environment_value_unsafe",
      "provenance.listener",
    )
    environment[name] = value
  }
  return { environment, forbiddenValues, providerEndpoint }
}

const validateOwnedBackendInputs = async (args, deadline) => {
  assertGate(isAbsolute(args.backendRoot) && isAbsolute(args.backendPython), "backend_launch_path_not_absolute", "provenance.listener")
  const root = await withinDeadline(realpath(args.backendRoot), deadline, "backend_launch_validation_timeout", "provenance.listener").catch(() => null)
  assertGate(root === args.backendRoot, "backend_root_invalid", "provenance.listener")
  await validateTrustedDirectory(root, deadline)
  const { stdout: rootOutput } = await runIdentityTool("/usr/bin/git", ["rev-parse", "--path-format=absolute", "--show-toplevel"], deadline, root)
  assertGate(rootOutput === `${root}\n`, "backend_root_invalid", "provenance.listener")
  const { stdout: backendCommonOutput } = await runIdentityTool("/usr/bin/git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], deadline, root)
  const { stdout: gateCommonOutput } = await verifiedGit(
    ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    deadline,
    { encoding: "utf8", maxBuffer: 4096 },
  )
  const backendCommon = await withinDeadline(realpath(backendCommonOutput.trim()), deadline, "backend_launch_validation_timeout", "provenance.listener").catch(() => null)
  const gateCommon = await withinDeadline(realpath(gateCommonOutput.trim()), deadline, "backend_launch_validation_timeout", "provenance.listener").catch(() => null)
  assertGate(backendCommon !== null && backendCommon === gateCommon, "backend_git_common_dir_mismatch", "provenance.listener")
  await assertNoGitReplacementRefs(root, deadline, "provenance.listener")
  const { stdout: headOutput } = await runIdentityTool("/usr/bin/git", ["rev-parse", "HEAD"], deadline, root)
  assertGate(headOutput === `${args.expectedBackendCommit}\n`, "backend_listener_commit_mismatch", "provenance.listener")
  const { stdout: statusOutput } = await runIdentityTool(
    "/usr/bin/git",
    ["status", "--porcelain=v1", "--untracked-files=all"],
    deadline,
    root,
  )
  const { stdout: ignoredOutput } = await runIdentityTool(
    "/usr/bin/git",
    ["ls-files", "--others", "--ignored", "--exclude-standard"],
    deadline,
    root,
  )
  assertGate(ignoredOutput === "", "backend_listener_not_clean", "provenance.listener")
  assertGate(statusOutput === "", "backend_listener_not_clean", "provenance.listener")
  const applicationPath = join(root, "agentic_coder_prototype", "api", "cli_bridge", "app.py")
  const repositoryPath = relative(root, applicationPath)
  const { stdout: trackedOutput } = await runIdentityTool(
    "/usr/bin/git",
    ["ls-files", "--error-unmatch", "--", repositoryPath],
    deadline,
    root,
  )
  assertGate(trackedOutput === `${repositoryPath}\n`, "backend_application_untracked", "provenance.listener")
  const { stdout: committedSource } = await runIdentityTool(
    "/usr/bin/git",
    ["show", `HEAD:${repositoryPath}`],
    deadline,
    root,
  )
  const liveSource = await withinDeadline(readFile(applicationPath, "utf8"), deadline, "backend_launch_validation_timeout", "provenance.listener")
  assertGate(liveSource === committedSource, "backend_application_mismatch", "provenance.listener")
  const applicationInfo = await withinDeadline(lstat(applicationPath), deadline, "backend_launch_validation_timeout", "provenance.listener").catch(() => null)
  assertGate(applicationInfo?.isFile() && !applicationInfo.isSymbolicLink(), "backend_application_untracked", "provenance.listener")
  const runtime = await approvedBackendRuntime(args.backendPython, deadline)
  return { root, python: runtime.resolvedPath, runtime }
}

const backendTreeManifest = async (root, commit, deadline) => {
  const remaining = deadline - Date.now()
  assertGate(remaining > 0, "absolute_deadline_exceeded", "provenance.listener")
  let stdout
  try {
    ;({ stdout } = await execFileAsync(
      "/usr/bin/git",
      ["ls-tree", "-r", "-z", "--long", commit],
      {
        cwd: root,
        encoding: null,
        env: FIXED_GIT_ENVIRONMENT,
        killSignal: "SIGKILL",
        maxBuffer: 64 * 1024 * 1024,
        timeout: remaining,
      },
    ))
  } catch {
    fail("backend_snapshot_manifest_failed", "provenance.listener")
  }
  const records = new Map()
  for (const encoded of stdout.toString("utf8").split("\0")) {
    if (encoded.length === 0) continue
    const match = /^(100644|100755) blob ([0-9a-f]{40}) +([0-9]+)\t(.+)$/.exec(encoded)
    assertGate(match !== null, "backend_snapshot_tree_unsupported", "provenance.listener")
    const [, mode, object, sizeText, path] = match
    assertGate(
      !isAbsolute(path)
        && path.length <= 4096
        && !path.includes("\0")
        && path.split("/").every((part) => part.length > 0 && part !== "." && part !== ".."),
      "backend_snapshot_tree_unsupported",
      "provenance.listener",
    )
    const size = Number(sizeText)
    assertGate(Number.isSafeInteger(size) && size >= 0 && size <= MAX_RUNTIME_FILE_BYTES, "backend_snapshot_budget", "provenance.listener")
    assertGate(!records.has(path), "backend_snapshot_tree_unsupported", "provenance.listener")
    records.set(path, { mode, object, size })
  }
  assertGate(records.size > 0 && records.size <= MAX_RUNTIME_CLOSURE_FILES, "backend_snapshot_budget", "provenance.listener")
  const bytes = [...records.values()].reduce((total, record) => total + record.size, 0)
  assertGate(bytes <= MAX_RUNTIME_CLOSURE_BYTES, "backend_snapshot_budget", "provenance.listener")
  return records
}

const hashBackendSnapshotFile = async (path, record, locked, deadline) => {
  let handle = null
  try {
    handle = await withinDeadline(
      open(path, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)),
      deadline,
      "backend_snapshot_timeout",
      "provenance.listener",
    )
    const before = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_snapshot_timeout", "provenance.listener")
    const expectedMode = locked ? (record.mode === "100755" ? 0o500 : 0o400) : (record.mode === "100755" ? 0o755 : 0o644)
    assertGate(
      before.isFile()
        && before.nlink === 1n
        && before.size === BigInt(record.size)
        && Number(before.mode & 0o777n) === expectedMode,
      "backend_snapshot_entry_invalid",
      "provenance.listener",
    )
    const digest = createHash("sha1")
    digest.update(`blob ${record.size}\0`)
    const buffer = Buffer.allocUnsafe(64 * 1024)
    let offset = 0
    while (offset < record.size) {
      const { bytesRead } = await withinDeadline(
        handle.read(buffer, 0, Math.min(buffer.byteLength, record.size - offset), offset),
        deadline,
        "backend_snapshot_timeout",
        "provenance.listener",
      )
      assertGate(bytesRead > 0, "backend_snapshot_changed", "provenance.listener")
      digest.update(buffer.subarray(0, bytesRead))
      offset += bytesRead
    }
    const after = await withinDeadline(handle.stat({ bigint: true }), deadline, "backend_snapshot_timeout", "provenance.listener")
    assertGate(runtimeMetadataUnchanged(before, after), "backend_snapshot_changed", "provenance.listener")
    assertGate(digest.digest("hex") === record.object, "backend_snapshot_content_mismatch", "provenance.listener")
  } catch (error) {
    if (error instanceof GateFailure) throw error
    fail("backend_snapshot_entry_invalid", "provenance.listener")
  } finally {
    await handle?.close().catch(() => undefined)
  }
}

const validateBackendSnapshot = async (snapshot, locked, deadline) => {
  const expectedDirectories = new Set([""])
  for (const path of snapshot.records.keys()) {
    const parts = path.split("/")
    for (let index = 1; index < parts.length; index += 1) {
      expectedDirectories.add(parts.slice(0, index).join("/"))
    }
  }
  const seenFiles = new Set()
  const seenDirectories = new Set()
  const scan = async (directory, relativeDirectory) => {
    const before = await withinDeadline(lstat(directory, { bigint: true }), deadline, "backend_snapshot_timeout", "provenance.listener").catch(() => null)
    assertGate(
      before?.isDirectory()
        && !before.isSymbolicLink()
        && Number(before.mode & 0o777n) === (locked ? 0o500 : (relativeDirectory === "" ? 0o700 : 0o755)),
      "backend_snapshot_entry_invalid",
      "provenance.listener",
    )
    seenDirectories.add(relativeDirectory)
    const names = await withinDeadline(readdir(directory), deadline, "backend_snapshot_timeout", "provenance.listener")
    names.sort()
    for (const name of names) {
      assertGate(name !== "." && name !== ".." && !name.includes(sep), "backend_snapshot_entry_invalid", "provenance.listener")
      const relativePath = relativeDirectory === "" ? name : `${relativeDirectory}/${name}`
      const path = join(directory, name)
      const info = await withinDeadline(lstat(path), deadline, "backend_snapshot_timeout", "provenance.listener").catch(() => null)
      assertGate(info !== null && !info.isSymbolicLink(), "backend_snapshot_entry_invalid", "provenance.listener")
      if (info.isDirectory()) {
        assertGate(expectedDirectories.has(relativePath), "backend_snapshot_extra_entry", "provenance.listener")
        await scan(path, relativePath)
      } else {
        const record = snapshot.records.get(relativePath)
        assertGate(info.isFile() && record !== undefined && !seenFiles.has(relativePath), "backend_snapshot_extra_entry", "provenance.listener")
        await hashBackendSnapshotFile(path, record, locked, deadline)
        seenFiles.add(relativePath)
      }
    }
    const after = await withinDeadline(lstat(directory, { bigint: true }), deadline, "backend_snapshot_timeout", "provenance.listener")
    assertGate(runtimeMetadataUnchanged(before, after), "backend_snapshot_changed", "provenance.listener")
  }
  await scan(snapshot.root, "")
  assertGate(
    seenFiles.size === snapshot.records.size
      && seenDirectories.size === expectedDirectories.size
      && [...expectedDirectories].every((path) => seenDirectories.has(path)),
    "backend_snapshot_missing_entry",
    "provenance.listener",
  )
}

const lockBackendSnapshot = async (snapshot, deadline) => {
  const directories = []
  const scan = async (directory) => {
    directories.push(directory)
    const names = await withinDeadline(readdir(directory), deadline, "backend_snapshot_timeout", "provenance.listener")
    names.sort()
    for (const name of names) {
      const path = join(directory, name)
      const info = await withinDeadline(lstat(path), deadline, "backend_snapshot_timeout", "provenance.listener")
      if (info.isDirectory() && !info.isSymbolicLink()) {
        await scan(path)
      } else {
        const relativePath = relative(snapshot.root, path).split(sep).join("/")
        const record = snapshot.records.get(relativePath)
        assertGate(info.isFile() && !info.isSymbolicLink() && record !== undefined, "backend_snapshot_entry_invalid", "provenance.listener")
        await withinDeadline(chmod(path, record.mode === "100755" ? 0o500 : 0o400), deadline, "backend_snapshot_timeout", "provenance.listener")
      }
    }
  }
  await scan(snapshot.root)
  for (const directory of directories.reverse()) {
    await withinDeadline(chmod(directory, 0o500), deadline, "backend_snapshot_timeout", "provenance.listener")
  }
}

const createBackendSnapshot = async (root, commit, deadline, cleanupDeadline = deadline) => {
  const records = await backendTreeManifest(root, commit, deadline)
  const createdContainer = await withinDeadline(mkdtemp(join(tmpdir(), "bb89n14-backend-snapshot-")), deadline, "backend_snapshot_timeout", "provenance.listener")
  const container = await withinDeadline(realpath(createdContainer), deadline, "backend_snapshot_timeout", "provenance.listener").catch(async () => {
    await rm(createdContainer, { recursive: true, force: true }).catch(() => undefined)
    return null
  })
  assertGate(typeof container === "string" && isAbsolute(container), "backend_snapshot_container_invalid", "provenance.listener")
  await withinDeadline(chmod(container, 0o700), deadline, "backend_snapshot_timeout", "provenance.listener")
  const snapshotRoot = join(container, "root")
  await withinDeadline(mkdir(snapshotRoot, { mode: 0o700 }), deadline, "backend_snapshot_timeout", "provenance.listener")
  const git = spawn("/usr/bin/git", ["archive", "--format=tar", commit], {
    cwd: root,
    env: FIXED_GIT_ENVIRONMENT,
    stdio: ["ignore", "pipe", "ignore"],
  })
  const tar = spawn("/usr/bin/tar", ["-xpf", "-", "-C", snapshotRoot], {
    cwd: "/",
    env: closedRuntimeEnvironment(),
    stdio: ["pipe", "ignore", "ignore"],
  })
  git.stdout.on("error", () => undefined)
  tar.stdin.on("error", () => undefined)
  git.stdout.pipe(tar.stdin)
  const wait = (child) => new Promise((resolveExit) => {
    child.once("error", (error) => resolveExit({ code: null, signal: null, error }))
    child.once("close", (code, signal) => resolveExit({ code, signal, error: null }))
  })
  const gitExitPromise = wait(git)
  const tarExitPromise = wait(tar)
  try {
    const [gitExit, tarExit] = await withinDeadline(
      Promise.all([gitExitPromise, tarExitPromise]),
      deadline,
      "backend_snapshot_timeout",
      "provenance.listener",
    )
    assertGate(
      gitExit.error === null && gitExit.code === 0 && gitExit.signal === null
        && tarExit.error === null && tarExit.code === 0 && tarExit.signal === null,
      "backend_snapshot_extract_failed",
      "provenance.listener",
    )
    const snapshot = { container, root: snapshotRoot, records }
    await lockBackendSnapshot(snapshot, deadline)
    await validateBackendSnapshot(snapshot, true, deadline)
    return snapshot
  } catch (error) {
    git.kill("SIGKILL")
    tar.kill("SIGKILL")
    await withinDeadline(Promise.allSettled([gitExitPromise, tarExitPromise]), cleanupDeadline, "backend_snapshot_cleanup_timeout", "cleanup").catch(() => undefined)
    await removeBackendSnapshot({ container, root: snapshotRoot }, cleanupDeadline).catch(() => undefined)
    throw error
  }
}
const removeBackendSnapshot = async (snapshot, deadline) => {
  const unlock = async (path) => {
    const info = await withinDeadline(lstat(path), deadline, "backend_snapshot_cleanup_timeout", "cleanup").catch(() => null)
    if (info === null) return
    if (info.isDirectory() && !info.isSymbolicLink()) {
      await withinDeadline(chmod(path, 0o700), deadline, "backend_snapshot_cleanup_timeout", "cleanup")
      const names = await withinDeadline(readdir(path), deadline, "backend_snapshot_cleanup_timeout", "cleanup")
      for (const name of names) await unlock(join(path, name))
    } else {
      await withinDeadline(chmod(path, 0o600), deadline, "backend_snapshot_cleanup_timeout", "cleanup")
    }
  }
  await unlock(snapshot.container)
  await withinDeadline(rm(snapshot.container, { recursive: true, force: true }), deadline, "backend_snapshot_cleanup_timeout", "cleanup")
}

const allocateOwnedBackendPort = async (host, deadline) => {
  const server = createNetServer()
  try {
    await withinDeadline(new Promise((resolveListen, rejectListen) => {
      server.once("error", rejectListen)
      server.listen(0, host, resolveListen)
    }), deadline, "backend_port_allocation_timeout", "provenance.listener")
    const address = server.address()
    assertGate(address !== null && typeof address !== "string" && Number.isSafeInteger(address.port), "backend_port_allocation_failed", "provenance.listener")
    return address.port
  } finally {
    await withinDeadline(new Promise((resolveClose) => server.close(resolveClose)), deadline, "backend_port_cleanup_timeout", "provenance.listener").catch(() => undefined)
  }
}

const attestOwnedBackendListener = async (owned, deadline, revalidate = true) => {
  assertGate(owned.exitResult === null, "owned_backend_exited", "provenance.listener")
  if (revalidate) {
    const revalidated = await validateOwnedBackendInputs({
      backendRoot: owned.root,
      backendPython: owned.python,
      expectedBackendCommit: owned.commit,
    }, deadline)
    assertGate(
      revalidated.root === owned.root
        && revalidated.python === owned.python
        && isDeepStrictEqual(revalidated.runtime, owned.runtime),
      "backend_listener_changed",
      "provenance.listener",
    )
    await validateBackendSnapshot(owned.snapshot, true, deadline)
  }
  const runtimeStateInfo = await withinDeadline(lstat(owned.runtimeStateRoot), deadline, "backend_runtime_state_timeout", "provenance.listener").catch(() => null)
  const canonicalRuntimeStateRoot = await withinDeadline(realpath(owned.runtimeStateRoot), deadline, "backend_runtime_state_timeout", "provenance.listener").catch(() => null)
  assertGate(
    runtimeStateInfo?.isDirectory()
      && !runtimeStateInfo.isSymbolicLink()
      && (runtimeStateInfo.mode & 0o777) === 0o700
      && canonicalRuntimeStateRoot === owned.runtimeStateRoot
      && owned.runtimeStateRoot === join(owned.snapshot.container, "runtime-state")
      && isInside(owned.snapshot.container, owned.runtimeStateRoot)
      && !isInside(owned.snapshot.root, owned.runtimeStateRoot),
    "backend_runtime_state_invalid",
    "provenance.listener",
  )
  const parsed = new URL(owned.baseUrl)
  const { stdout } = await runIdentityTool(
    "/usr/sbin/lsof",
    ["-nP", "-a", `-iTCP:${parsed.port}`, "-sTCP:LISTEN", "-Fpn"],
    deadline,
  )
  const pids = stdout.split("\n").filter((line) => /^p[1-9]\d*$/.test(line)).map((line) => Number(line.slice(1)))
  const endpoints = stdout.split("\n").filter((line) => line.startsWith("n")).map((line) => line.slice(1))
  assertGate(
    pids.length === 1
      && pids[0] === owned.child.pid
      && endpoints.includes(`${parsed.hostname}:${parsed.port}`),
    "owned_backend_listener_mismatch",
    "provenance.listener",
  )
  const { stdout: cwdOutput } = await runIdentityTool(
    "/usr/sbin/lsof",
    ["-a", "-p", String(owned.child.pid), "-d", "cwd", "-Fn"],
    deadline,
  )
  const cwdValues = cwdOutput.split("\n").filter((line) => line.startsWith("n")).map((line) => line.slice(1))
  assertGate(cwdValues.length === 1 && isAbsolute(cwdValues[0]), "owned_backend_cwd_invalid", "provenance.listener")
  const cwd = await withinDeadline(realpath(cwdValues[0]), deadline, "owned_backend_cwd_timeout", "provenance.listener").catch(() => null)
  assertGate(cwd === owned.workspace, "owned_backend_cwd_invalid", "provenance.listener")
  return {
    pid: owned.child.pid,
    cwd,
    command: "gate-owned-isolated-python",
    expectedEndpoint: `${parsed.hostname}:${parsed.port}`,
    commit: owned.commit,
    kind: "gate-owned-canonical",
  }
}

const startOwnedCanonicalBackend = async (args, deadline, cleanupDeadline = deadline) => {
  const providerEnvironment = forwardedProviderEnvironment(args.expectedProviderModel)
  const providerEndpointSha256 = providerEnvironment.providerEndpoint.digest
  const validated = await validateOwnedBackendInputs(args, deadline)
  const host = new URL(args.baseUrl).hostname.replace(/^\[|\]$/g, "")
  const port = await allocateOwnedBackendPort(host, deadline)
  const workspace = await withinDeadline(realpath(args.workspace), deadline, "backend_workspace_timeout", "provenance.listener").catch(() => null)
  const workspaceInfo = typeof workspace === "string"
    ? await withinDeadline(lstat(workspace), deadline, "backend_workspace_timeout", "provenance.listener").catch(() => null)
    : null
  assertGate(workspaceInfo?.isDirectory() && !workspaceInfo.isSymbolicLink(), "backend_workspace_invalid", "provenance.listener")
  const snapshot = await createBackendSnapshot(validated.root, args.expectedBackendCommit, deadline, cleanupDeadline)
  const runtimeStateRoot = join(snapshot.container, "runtime-state")
  try {
    await withinDeadline(mkdir(runtimeStateRoot, { mode: 0o700 }), deadline, "backend_runtime_state_timeout", "provenance.listener")
    await validateBackendSnapshot(snapshot, true, deadline)
  } catch (error) {
    await removeBackendSnapshot(snapshot, cleanupDeadline).catch(() => undefined)
    throw error
  }
  const token = randomBytes(32).toString("hex")
  const control = `${JSON.stringify({ root: snapshot.root, host, port, token, commit: args.expectedBackendCommit })}\n`
  assertGate(Buffer.byteLength(control) <= 65_536, "backend_launch_control_budget", "provenance.listener")
  const child = spawn(validated.python, ["-I", "-B", "-c", OWNED_BACKEND_BOOTSTRAP], {
    cwd: workspace,
    env: { ...providerEnvironment.environment, BREADBOARD_SESSION_STATE_ROOT: runtimeStateRoot },
    stdio: ["pipe", "pipe", "pipe"],
  })
  const owned = {
    child,
    root: validated.root,
    snapshot,
    workspace,
    runtimeStateRoot,
    commit: args.expectedBackendCommit,
    python: validated.python,
    runtime: validated.runtime,
    forbiddenProviderValues: providerEnvironment.forbiddenValues,
    providerEndpointSha256,
    token,
    baseUrl: `http://${host === "::1" ? `[${host}]` : host}:${port}/`,
    exitResult: null,
    outputBytes: 0,
    outputExceeded: false,
    exit: null,
    stderrBytes: 0,
    stderrChunks: [],
  }
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => {
      owned.outputBytes += chunk.byteLength
      if (stream === child.stderr && owned.stderrBytes < 8192) {
        const captured = Buffer.from(chunk.subarray(0, 8192 - owned.stderrBytes))
        owned.stderrChunks.push(captured)
        owned.stderrBytes += captured.byteLength
      }
      if (owned.outputBytes > 64 * 1024) {
        owned.outputExceeded = true
        child.kill("SIGTERM")
      }
    })
  }
  owned.exit = new Promise((resolveExit) => {
    child.once("error", (error) => {
      owned.exitResult = { error, code: null, signal: null }
      resolveExit(owned.exitResult)
    })
    child.once("close", (code, signal) => {
      if (owned.exitResult === null) owned.exitResult = { error: null, code, signal }
      resolveExit(owned.exitResult)
    })
  })
  child.stdin.end(control)
  try {
    while (Date.now() < deadline) {
      if (owned.outputExceeded) fail("owned_backend_output_budget", "provenance.listener")
      if (owned.exitResult !== null) fail("owned_backend_exited", "provenance.listener")
      try {
        owned.identity = await attestOwnedBackendListener(owned, deadline, false)
        return owned
      } catch (error) {
        if (
          !(error instanceof GateFailure)
          || (error.code !== "owned_backend_listener_mismatch" && error.code !== "backend_listener_identity_invalid")
        ) throw error
        await withinDeadline(new Promise((resolveDelay) => setTimeout(resolveDelay, 10)), deadline, "backend_start_timeout", "provenance.listener")
      }
    }
    fail("backend_start_timeout", "provenance.listener")
  } catch (error) {
    if (error !== null && typeof error === "object") {
      error.ownedDiagnostic = {
        exitCode: owned.exitResult?.code ?? null,
        signal: owned.exitResult?.signal ?? null,
        stderr: Buffer.concat(owned.stderrChunks, owned.stderrBytes).toString("utf8"),
        redactionValues: [
          owned.root,
          owned.snapshot.root,
          owned.snapshot.container,
          owned.workspace,
          owned.runtimeStateRoot,
          CHILD_ENVIRONMENT.HOME,
          tmpdir(),
          owned.token,
          ...owned.forbiddenProviderValues,
        ],
      }
    }
    await stopOwnedCanonicalBackend(owned, cleanupDeadline)
    throw error
  }
}

const stopOwnedCanonicalBackend = async (owned, deadline) => {
  if (owned === null) return
  let processError = null
  if (owned.exitResult === null) {
    owned.child.kill("SIGTERM")
    try {
      await withinDeadline(owned.exit, deadline, "owned_backend_cleanup_timeout", "cleanup")
    } catch (error) {
      owned.child.kill("SIGKILL")
      try {
        await withinDeadline(owned.exit, deadline, "owned_backend_cleanup_timeout", "cleanup")
      } catch (killError) {
        processError = killError
      }
      if (processError === null) processError = error
    }
  }
  let snapshotError = null
  try {
    await removeBackendSnapshot(owned.snapshot, deadline)
  } catch (error) {
    snapshotError = error
  }
  if (processError !== null) throw processError
  if (snapshotError !== null) throw snapshotError
}

export const readOwnedBackendProvenanceForTest = async (
  args,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
) => {
  let owned = null
  try {
    owned = await startOwnedCanonicalBackend(args, deadline, deadline)
    return await readBackendProvenance({
      baseUrl: owned.baseUrl,
      authToken: owned.token,
      expectedCommit: args.expectedBackendCommit,
      deadline,
    })
  } finally {
    if (owned !== null) await stopOwnedCanonicalBackend(owned, deadline)
  }
}

export const diagnoseOwnedCanonicalBackendForTest = async (
  args,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
) => {
  let owned = null
  try {
    owned = await startOwnedCanonicalBackend(args, deadline, deadline)
    return { exitCode: null, signal: null, stderr: "" }
  } catch (error) {
    const diagnostic = error?.ownedDiagnostic
    if (diagnostic === undefined) throw error
    let stderr = diagnostic.stderr
    const redactionValues = [...new Set(diagnostic.redactionValues)]
      .filter((value) => typeof value === "string" && value.length > 0)
      .sort((left, right) => right.length - left.length)
    for (let index = 0; index < redactionValues.length; index += 1) {
      stderr = stderr.replaceAll(redactionValues[index], `<redacted-${index}>`)
    }
    return {
      exitCode: Number.isInteger(diagnostic.exitCode) ? diagnostic.exitCode : null,
      signal: typeof diagnostic.signal === "string" && /^[A-Z0-9]+$/.test(diagnostic.signal) ? diagnostic.signal : null,
      stderr: stderr.slice(0, 8192),
    }
  } finally {
    if (owned !== null) await stopOwnedCanonicalBackend(owned, deadline)
  }
}



const verifyClientArtifacts = async (expectedCommit, deadline) => {
  assertGate(expectedCommit === CLIENT_BUILD_MANIFEST.commit, "client_manifest_commit_mismatch", "provenance.client")
  await assertNoGitReplacementRefs(REPOSITORY_ROOT, deadline, "provenance.client")
  try {
    await verifiedGit(["merge-base", "--is-ancestor", expectedCommit, "HEAD"], deadline)
  } catch {
    fail("client_source_commit_not_ancestor", "provenance.client")
  }
  for (const [repositoryPath, digest] of Object.entries(CLIENT_BUILD_MANIFEST.committed)) {
    const workingPath = join(REPOSITORY_ROOT, repositoryPath)
    await verifyFileDigest(workingPath, digest, "provenance.client", deadline)
    let committed
    try {
      ;({ stdout: committed } = await verifiedGit(
        ["show", `${expectedCommit}:${repositoryPath}`],
        deadline,
        { encoding: "buffer" },
      ))
    } catch {
      fail("client_committed_source_unavailable", "provenance.client")
    }
    assertGate(sha256(committed) === digest, "client_committed_source_mismatch", "provenance.client")
  }
  for (const [sdkPath, digest] of Object.entries(CLIENT_BUILD_MANIFEST.loaded)) {
    await verifyFileDigest(join(SDK_ROOT, sdkPath), digest, "provenance.client", deadline)
  }
}

const loadVerifiedCanonicalClient = async (expectedCommit, deadline) => {
  await verifyClientArtifacts(expectedCommit, deadline)
  const module = await withinDeadline(
    import(pathToFileURL(join(SDK_ROOT, "dist/index.js")).href),
    deadline,
    "client_import_timeout",
    "provenance.client",
  )
  assertGate(typeof module.createCanonicalE4Client === "function", "canonical_client_export_missing", "provenance.client")
  assertGate(typeof module.CanonicalE4ClientError === "function", "canonical_client_error_export_missing", "provenance.client")
  await verifyClientArtifacts(expectedCommit, deadline)
  CanonicalE4ClientErrorClass = module.CanonicalE4ClientError
  return module.createCanonicalE4Client
}

const readAuthToken = () => {
  const token = capturedAuthToken
  capturedAuthToken = undefined
  if (token === undefined) return undefined
  assertGate(token.length >= 16 && token.length <= 8192 && !/[\r\n]/.test(token), "invalid_auth_token", "auth")
  return token
}

const captureDirectoryPin = async (requestedDirectory) => {
  const directory = await realpath(requestedDirectory).catch(() => null)
  assertGate(directory !== null, "output_directory_missing", "output")
  const currentUid = typeof process.geteuid === "function" ? process.geteuid() : null
  const paths = []
  for (let current = directory; ; current = dirname(current)) {
    paths.push(current)
    if (dirname(current) === current) break
  }
  paths.reverse()
  const ancestors = []
  for (const path of paths) {
    const info = await lstat(path).catch(() => null)
    assertGate(info?.isDirectory() && !info.isSymbolicLink(), "untrusted_output_ancestor", "output")
    if (currentUid !== null) {
      assertGate(info.uid === 0 || info.uid === currentUid, "untrusted_output_ancestor_owner", "output")
      const writableByOthers = (info.mode & 0o022) !== 0
      const trustedStickyRoot = info.uid === 0 && (info.mode & 0o1000) !== 0
      assertGate(!writableByOthers || trustedStickyRoot, "untrusted_output_ancestor_permissions", "output")
    }
    ancestors.push({ path, dev: String(info.dev), ino: String(info.ino), uid: info.uid, mode: info.mode & 0o7777 })
  }
  const leaf = ancestors.at(-1)
  if (currentUid !== null) {
    assertGate(leaf.uid === currentUid, "output_directory_not_owned", "output")
    assertGate((leaf.mode & 0o022) === 0, "output_directory_not_private", "output")
  }
  return { directory, ancestors }
}

const revalidateDirectoryPin = async (pin) => {
  for (const expected of pin.ancestors) {
    const info = await lstat(expected.path).catch(() => null)
    assertGate(
      info?.isDirectory()
        && !info.isSymbolicLink()
        && String(info.dev) === expected.dev
        && String(info.ino) === expected.ino
        && info.uid === expected.uid
        && (info.mode & 0o7777) === expected.mode,
      "output_directory_changed",
      "output",
    )
  }
}

const parseOptionValues = (argv) => {
  assertGate(Array.isArray(argv) && argv.length % 2 === 0, "invalid_cli_arity", "cli")
  const values = new Map()
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index]
    const value = argv[index + 1]
    assertGate(typeof name === "string" && OPTION_NAMES.has(name), "unknown_cli_argument", "cli")
    assertGate(typeof value === "string" && value.length > 0 && !value.startsWith("--"), "missing_cli_value", "cli")
    assertGate(!values.has(name), name === "--output" ? "missing_or_duplicate_output" : "duplicate_cli_argument", "cli")
    values.set(name, value)
  }
  assertGate(values.has("--output"), "missing_or_duplicate_output", "cli")
  return values
}

const preparseInvocation = (argv) => {
  const values = parseOptionValues(argv)
  const rawTimeout = values.get("--timeout-ms")
  const timeoutMs = rawTimeout === undefined ? DEFAULT_RUN_TIMEOUT_MS : Number(rawTimeout)
  assertGate(Number.isSafeInteger(timeoutMs) && timeoutMs >= MIN_RUN_TIMEOUT_MS && timeoutMs <= DEFAULT_RUN_TIMEOUT_MS, "invalid_timeout", "cli")
  return { outputPath: resolve(values.get("--output")), timeoutMs }
}

const prepareOutput = async (requestedPath) => {
  const requested = resolve(requestedPath)
  const pin = await captureDirectoryPin(dirname(requested))
  const outputPath = join(pin.directory, basename(requested))
  assertGate(!isInside(await realpath(REPOSITORY_ROOT), outputPath), "output_inside_repository", "output")
  await revalidateDirectoryPin(pin)
  return { outputPath, pin }
}


export function parseArgs(argv) {
  const values = parseOptionValues(argv)
  for (const name of REQUIRED_OPTIONS) assertGate(values.has(name), "missing_required_cli_argument", "cli")
  const backendCommit = values.get("--expected-backend-commit")
  const clientCommit = values.get("--expected-client-commit")
  assertGate(isCommit(backendCommit), "invalid_backend_commit", "cli")
  assertGate(isAbsolute(values.get("--backend-root")), "backend_root_not_absolute", "cli")
  assertGate(isAbsolute(values.get("--backend-python")), "backend_python_not_absolute", "cli")
  assertGate(isCommit(clientCommit), "invalid_client_commit", "cli")
  const expectedProviderModel = values.get("--expected-provider-model")
  assertGate(expectedProviderModel.trim() === expectedProviderModel, "invalid_expected_provider_model", "cli")
  assertExpectedProviderModel(expectedProviderModel, expectedProviderModel, "cli")
  let baseUrl
  try {
    baseUrl = new URL(values.get("--base-url"))
  } catch {
    fail("invalid_base_url", "cli")
  }
  assertGate(["http:", "https:"].includes(baseUrl.protocol), "invalid_base_url_protocol", "cli")
  assertGate(baseUrl.username === "" && baseUrl.password === "" && baseUrl.search === "" && baseUrl.hash === "", "unsafe_base_url", "cli")
  assertGate(baseUrl.pathname === "/", "base_url_must_be_origin", "cli")
  assertGate(baseUrl.hostname === "127.0.0.1" || baseUrl.hostname === "[::1]", "base_url_not_literal_loopback", "cli")
  const rawTimeout = values.get("--timeout-ms")
  const timeoutMs = rawTimeout === undefined ? DEFAULT_RUN_TIMEOUT_MS : Number(rawTimeout)
  assertGate(Number.isSafeInteger(timeoutMs) && timeoutMs >= MIN_RUN_TIMEOUT_MS && timeoutMs <= DEFAULT_RUN_TIMEOUT_MS, "invalid_timeout", "cli")
  return {
    baseUrl: baseUrl.href,
    configPath: resolve(values.get("--config-path")),
    backendRoot: resolve(values.get("--backend-root")),
    backendPython: resolve(values.get("--backend-python")),
    workspace: resolve(values.get("--workspace")),
    outputPath: resolve(values.get("--output")),
    expectedBackendCommit: backendCommit,
    expectedClientCommit: clientCommit,
    expectedProviderModel,
    timeoutMs,
  }
}

const validateInputPaths = async (args, outputTarget) => {
  assertGate(join(outputTarget.pin.directory, basename(args.outputPath)) === outputTarget.outputPath, "output_normalization_mismatch", "cli")
  const configInfo = await lstat(args.configPath).catch(() => null)
  assertGate(configInfo?.isFile() && !configInfo.isSymbolicLink(), "config_path_not_regular_file", "cli")
  const workspaceInfo = await stat(args.workspace).catch(() => null)
  assertGate(workspaceInfo?.isDirectory(), "workspace_not_directory", "cli")
  const repositoryRoot = await realpath(REPOSITORY_ROOT)
  const workspace = await realpath(args.workspace)
  assertGate(!isInside(repositoryRoot, workspace), "workspace_inside_repository", "cli")
  return { ...args, workspace, outputPath: outputTarget.outputPath }
}

const CONFIG_SNAPSHOT_HELPER = String.raw`
import copy, errno, hashlib, json, os, re, signal, stat, subprocess, sys, tempfile

MAX_CONFIG_BYTES = ${MAX_CONFIG_BYTES}
MAX_CLOSURE_BYTES = ${MAX_CONFIG_CLOSURE_BYTES}
MAX_CLOSURE_FILES = ${MAX_CONFIG_CLOSURE_FILES}
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
budget_files = 0
budget_bytes = 0
manifest = {}
copied_files = {}
prompt_files = set()
recorded_directories = set()

def reject(code):
    raise RuntimeError(code)

def interrupted(signum, frame):
    raise TimeoutError("config_snapshot_interrupted")

signal.signal(signal.SIGTERM, interrupted)

def read_frame():
    header = sys.stdin.buffer.readline(32)
    if not header or not header.endswith(b"\n") or not header[:-1].isdigit():
        reject("config_snapshot_protocol")
    size = int(header[:-1])
    if size <= 0 or size > 65536:
        reject("config_snapshot_protocol")
    payload = sys.stdin.buffer.read(size)
    if len(payload) != size:
        reject("config_snapshot_protocol")
    return json.loads(payload)

def write_frame(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    os.write(sys.stdout.fileno(), str(len(payload)).encode("ascii") + b"\n" + payload)

def inside(root, path):
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False

def open_directory(path):
    path = os.path.normpath(path)
    if not os.path.isabs(path):
        reject("config_reference_not_absolute")
    descriptor = os.open(os.sep, os.O_RDONLY | DIRECTORY | NOFOLLOW)
    try:
        for component in [part for part in path.split(os.sep) if part]:
            next_descriptor = os.open(component, os.O_RDONLY | DIRECTORY | NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            reject("config_closure_non_regular_entry")
        return descriptor, info
    except OSError as error:
        os.close(descriptor)
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            reject("config_closure_symlink_unsupported")
        raise
    except BaseException:
        os.close(descriptor)
        raise

def read_file_nofollow(path, maximum=16 * 1024 * 1024):
    parent_descriptor, _ = open_directory(os.path.dirname(path))
    descriptor = -1
    try:
        descriptor = os.open(os.path.basename(path), os.O_RDONLY | NOFOLLOW, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            reject("config_size_budget_exceeded")
        if before.st_nlink != 1:
            reject("config_closure_hardlink_unsupported")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                reject("config_changed_during_snapshot")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            reject("config_changed_during_snapshot")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or before.st_nlink != after.st_nlink
            or after.st_nlink != 1
        ):
            reject("config_changed_during_snapshot")
        return b"".join(chunks), before
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            reject("config_closure_symlink_unsupported")
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)

def consume_file(data):
    global budget_files, budget_bytes
    budget_files += 1
    budget_bytes += len(data)
    if budget_files > MAX_CLOSURE_FILES:
        reject("config_closure_file_budget_exceeded")
    if budget_bytes > MAX_CLOSURE_BYTES:
        reject("config_closure_byte_budget_exceeded")

control = read_frame()
if not isinstance(control, dict) or set(control) != {"configPath", "repositoryRoot", "snapshotRoot", "trustedExternalRoot"}:
    reject("config_snapshot_protocol")
source_config = os.path.normpath(control["configPath"])
repository_root = os.path.normpath(control["repositoryRoot"])
snapshot_root = os.path.normpath(control["snapshotRoot"])
trusted_external_root = os.path.normpath(control["trustedExternalRoot"])
source_directory = os.path.dirname(source_config)
config_anchor = repository_root if inside(repository_root, source_config) else source_directory
source_project_root = None
cursor = source_directory
while os.path.dirname(cursor) != cursor:
    if os.path.basename(cursor) == "agent_configs":
        candidate_root = os.path.dirname(cursor)
        if candidate_root != os.path.abspath(os.sep):
            source_project_root = candidate_root
        break
    cursor = os.path.dirname(cursor)
logical_roots = []
if config_anchor != repository_root:
    logical_roots.append(("configuration", config_anchor))
if source_project_root not in {None, config_anchor, repository_root}:
    logical_roots.append(("source-project", source_project_root))
logical_roots.append(("repository", repository_root))
if trusted_external_root not in {config_anchor, source_project_root, repository_root}:
    logical_roots.append(("trusted-external-prompts", trusted_external_root))
if any(root == os.path.abspath(os.sep) for _, root in logical_roots):
    reject("config_closure_root_too_broad")
closure_root = os.path.join(snapshot_root, "closure")
os.mkdir(closure_root, 0o700)

def logical_path(path):
    path = os.path.normpath(path)
    for label, root in logical_roots:
        if inside(root, path):
            relative_path = os.path.relpath(path, root)
            if relative_path == ".":
                relative_path = "_root"
            return label + "/" + relative_path.replace(os.sep, "/")
    reject("config_reference_outside_closure")

def target_for(path):
    return os.path.join(closure_root, *logical_path(path).split("/"))

def ensure_target_directory(path):
    relative_path = os.path.relpath(path, snapshot_root)
    if relative_path == ".." or relative_path.startswith(".." + os.sep):
        reject("config_snapshot_target_escape")
    current = snapshot_root
    for component in [part for part in relative_path.split(os.sep) if part and part != "."]:
        current = os.path.join(current, component)
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                reject("config_snapshot_target_changed")
        os.chmod(current, 0o700)

def record_file(path, data, info):
    key = logical_path(path)
    existing = manifest.get(key)
    entry = {
        "path": key,
        "type": "file",
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "hash": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    if existing is not None and existing != entry:
        reject("config_manifest_path_collision")
    if existing is None:
        consume_file(data)
        manifest[key] = entry
    return key

def write_private_file(target, data):
    ensure_target_directory(os.path.dirname(target))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                reject("config_snapshot_short_write")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            reject("config_snapshot_not_private")
    finally:
        os.close(descriptor)

def copy_file(path, maximum=16 * 1024 * 1024):
    path = os.path.normpath(path)
    if path in copied_files:
        return copied_files[path]
    data, info = read_file_nofollow(path, maximum)
    record_file(path, data, info)
    target = target_for(path)
    write_private_file(target, data)
    copied_files[path] = target
    return target
def copy_prompt_file(path):
    path = os.path.normpath(path)
    if path in copied_files:
        if path not in prompt_files:
            reject("config_prompt_reference_role_collision")
        return copied_files[path]
    source_data, info = read_file_nofollow(path, 16 * 1024 * 1024)
    data = source_data if source_data.endswith(b"\n") else source_data + b"\n"
    record_file(path, source_data, info)
    target = target_for(path)
    write_private_file(target, data)
    copied_files[path] = target
    prompt_files.add(path)
    return target


def record_directory(path, info, child_entries):
    key = logical_path(path)
    digest_input = json.dumps(child_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest[key] = {
        "path": key,
        "type": "directory",
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "hash": "sha256:" + hashlib.sha256(digest_input).hexdigest(),
    }

def materialize_inline_prompt(value):
    data = (value + "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    key = "inline-prompts/" + digest + ".txt"
    target = os.path.join(closure_root, *key.split("/"))
    entry = {
        "path": key,
        "type": "file",
        "mode": "0600",
        "hash": "sha256:" + digest,
    }
    existing = manifest.get(key)
    if existing is not None and existing != entry:
        reject("config_manifest_path_collision")
    if existing is None:
        consume_file(data)
        manifest[key] = entry
        write_private_file(target, data)
    return target

def copy_tool_directory(path):
    path = os.path.normpath(path)
    if path in recorded_directories:
        return target_for(path)
    descriptor, info = open_directory(path)
    try:
        names = sorted(os.listdir(descriptor))
        yaml_names = [name for name in names if name.endswith(".yaml") or name.endswith(".yml")]
        record_directory(path, info, [{"name": name, "type": "file"} for name in yaml_names])
        ensure_target_directory(target_for(path))
        recorded_directories.add(path)
        for name in yaml_names:
            copy_file(os.path.join(path, name))
    finally:
        os.close(descriptor)
    return target_for(path)

RUBY_YAML_PARSER = r'''
Process.setrlimit(Process::RLIMIT_FSIZE, ${MAX_CONFIG_BYTES * 2}, ${MAX_CONFIG_BYTES * 2})
document = Psych.safe_load(
  STDIN.read,
  permitted_classes: [],
  permitted_symbols: [],
  aliases: false
)
JSON.dump(document, STDOUT)
'''


def parse_supported_yaml(data):
    with tempfile.TemporaryFile() as parser_output:
        parser = subprocess.Popen(
            ["/usr/bin/ruby", "--disable-gems", "-rjson", "-rpsych", "-e", RUBY_YAML_PARSER],
            stdin=subprocess.PIPE,
            stdout=parser_output,
            stderr=subprocess.DEVNULL,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            start_new_session=True,
        )
        try:
            parser.communicate(data)
        finally:
            if parser.poll() is None:
                os.killpg(parser.pid, signal.SIGKILL)
                parser.wait()
        if parser.returncode != 0:
            reject("config_yaml_invalid")
        parser_output.seek(0)
        output = parser_output.read(MAX_CONFIG_BYTES * 2 + 1)
    if len(output) > MAX_CONFIG_BYTES * 2:
        reject("config_yaml_invalid")
    try:
        return json.loads(output)
    except BaseException:
        reject("config_yaml_invalid")

def load_yaml_document(path):
    data, info = read_file_nofollow(path, MAX_CONFIG_BYTES)
    record_file(path, data, info)
    document = parse_supported_yaml(data)
    if document is None:
        document = {}
    if not isinstance(document, dict):
        reject("config_yaml_root_not_mapping")
    return document

def resolve_bounded(reference, base, kind):
    if not isinstance(reference, str) or not reference or len(reference) > 1024 or "\x00" in reference:
        reject("config_reference_unsupported")
    candidate = os.path.normpath(reference if os.path.isabs(reference) else os.path.join(base, reference))
    logical_path(candidate)
    return candidate

prompt_bases = [repository_root, source_directory]
if source_project_root is not None and source_project_root not in prompt_bases:
    prompt_bases.append(source_project_root)
if trusted_external_root not in prompt_bases:
    prompt_bases.append(trusted_external_root)

def trusted_external_candidate(reference):
    if not isinstance(reference, str) or os.path.isabs(reference):
        return None
    parts = [part for part in os.path.normpath(reference).split(os.sep) if part not in {"", "."}]
    try:
        marker = parts.index("other_harness_refs")
    except ValueError:
        return None
    if marker == 0 or any(part != ".." for part in parts[:marker]) or marker == len(parts) - 1:
        return None
    tail = parts[marker + 1:]
    if any(part in {"", ".", "..", "other_harness_refs"} for part in tail):
        reject("config_reference_outside_closure")
    return os.path.join(trusted_external_root, *tail)

def prompt_reference(value):
    if not isinstance(value, str) or not value or value.startswith("@pack(") or "\n" in value or len(value) > 256:
        return value
    mapped_external = trusted_external_candidate(value)
    if mapped_external is not None:
        try:
            return copy_prompt_file(mapped_external)
        except FileNotFoundError:
            reject("config_prompt_reference_unresolved")
    for base in prompt_bases:
        candidate = os.path.normpath(value if os.path.isabs(value) else os.path.join(base, value))
        if not any(inside(root, candidate) for _, root in logical_roots):
            continue
        try:
            return copy_prompt_file(candidate)
        except FileNotFoundError:
            pass
    if os.path.isabs(value) or value == "." or value == ".." or value.startswith("." + os.sep) or value.startswith(".." + os.sep):
        reject("config_reference_outside_closure")
    return materialize_inline_prompt(value)


def rewrite_prompt_references(document):
    prompts = document.get("prompts")
    if isinstance(prompts, dict):
        packs = prompts.get("packs")
        if isinstance(packs, dict):
            for pack in packs.values():
                if isinstance(pack, dict):
                    for key, value in list(pack.items()):
                        pack[key] = prompt_reference(value)
        for key in ("system", "per_turn"):
            if key in prompts:
                prompts[key] = prompt_reference(prompts[key])
    modes = document.get("modes")
    if isinstance(modes, list):
        for mode in modes:
            if isinstance(mode, dict) and "prompt" in mode:
                mode["prompt"] = prompt_reference(mode["prompt"])

def resolve_tool_directory(reference):
    candidate = resolve_bounded(reference, repository_root, "tool")
    try:
        return copy_tool_directory(candidate)
    except FileNotFoundError:
        reject("config_tool_reference_unresolved")

def rewrite_tool_references(document):
    tools = document.get("tools")
    if not isinstance(tools, dict):
        return
    registry = tools.get("registry")
    if isinstance(registry, dict) and "paths" in registry:
        paths = registry["paths"]
        if not isinstance(paths, list) or not paths:
            reject("config_tool_reference_unsupported")
        registry["paths"] = [resolve_tool_directory(path) for path in paths]
    if "defs_dir" in tools:
        tools["defs_dir"] = resolve_tool_directory(tools["defs_dir"])

def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result

documents = {}
def process_config(path, stack=()):
    path = os.path.normpath(path)
    if path in stack:
        reject("config_extends_cycle")
    if path in documents:
        return documents[path]["effective"]
    raw = load_yaml_document(path)
    rewritten = copy.deepcopy(raw)
    extends_value = raw.get("extends")
    if extends_value is None:
        references = []
    elif isinstance(extends_value, str):
        references = [extends_value]
    elif isinstance(extends_value, list) and all(isinstance(item, str) for item in extends_value):
        references = extends_value
    else:
        reject("config_extends_unsupported")
    effective = {}
    rewritten_extends = []
    for reference in references:
        base_path = resolve_bounded(reference, os.path.dirname(path), "extends")
        base_effective = process_config(base_path, stack + (path,))
        effective = deep_merge(effective, base_effective)
        rewritten_extends.append(target_for(base_path))
    if references:
        rewritten["extends"] = rewritten_extends
    rewrite_prompt_references(rewritten)
    rewrite_tool_references(rewritten)
    effective = deep_merge(effective, {key: value for key, value in raw.items() if key != "extends"})
    documents[path] = {"rewritten": rewritten, "effective": effective}
    return effective

def lock_snapshot_and_digest():
    for directory, directory_names, file_names in os.walk(snapshot_root, topdown=False, followlinks=False):
        for name in file_names:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                reject("config_snapshot_non_regular_entry")
            os.chmod(path, 0o400, follow_symlinks=False)
        for name in directory_names:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                reject("config_snapshot_non_regular_entry")
            os.chmod(path, 0o500, follow_symlinks=False)
    os.chmod(snapshot_root, 0o500, follow_symlinks=False)

    entries = []
    def scan(directory, relative_directory=""):
        names = sorted(os.listdir(directory))
        children = []
        for name in names:
            path = os.path.join(directory, name)
            relative_path = name if not relative_directory else relative_directory + "/" + name
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                reject("config_snapshot_non_regular_entry")
            if stat.S_ISDIR(info.st_mode):
                scan(path, relative_path)
                child_type = "directory"
                directory_children = []
                for child_name in sorted(os.listdir(path)):
                    child_path = os.path.join(path, child_name)
                    child_info = os.lstat(child_path)
                    if stat.S_ISLNK(child_info.st_mode):
                        reject("config_snapshot_non_regular_entry")
                    if stat.S_ISDIR(child_info.st_mode):
                        child_kind = "directory"
                    elif stat.S_ISREG(child_info.st_mode):
                        child_kind = "file"
                    else:
                        reject("config_snapshot_non_regular_entry")
                    directory_children.append({"name": child_name, "type": child_kind})
                after_directory = os.lstat(path)
                if (
                    after_directory.st_dev != info.st_dev
                    or after_directory.st_ino != info.st_ino
                    or stat.S_IMODE(after_directory.st_mode) != 0o500
                ):
                    reject("config_snapshot_changed_during_scan")
                child_hash = hashlib.sha256(
                    json.dumps(directory_children, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                mode = format(stat.S_IMODE(info.st_mode), "04o")
            elif stat.S_ISREG(info.st_mode):
                data, stable_info = read_file_nofollow(path, 16 * 1024 * 1024)
                child_type = "file"
                child_hash = hashlib.sha256(data).hexdigest()
                mode = format(stat.S_IMODE(stable_info.st_mode), "04o")
            else:
                reject("config_snapshot_non_regular_entry")
            entries.append({
                "path": relative_path,
                "type": child_type,
                "mode": mode,
                "hash": "sha256:" + child_hash,
            })
            children.append({"name": name, "type": child_type})
        return children
    scan(snapshot_root)
    entries.sort(key=lambda entry: (entry["path"], entry["type"]))
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest(), len(entries)

try:
    effective = process_config(source_config)
    tools = effective.get("tools") if isinstance(effective, dict) else None
    registry = tools.get("registry") if isinstance(tools, dict) else None
    has_registry_paths = isinstance(registry, dict) and bool(registry.get("paths"))
    has_defs_dir = isinstance(tools, dict) and bool(tools.get("defs_dir"))
    if not has_registry_paths and not has_defs_dir:
        default_tools = resolve_tool_directory("implementations/tools/defs")
        root_tools = documents[source_config]["rewritten"].setdefault("tools", {})
        if not isinstance(root_tools, dict):
            reject("config_tools_unsupported")
        root_tools["defs_dir"] = default_tools


    for path in sorted(documents):
        target = target_for(path)
        rewritten_bytes = (json.dumps(documents[path]["rewritten"], sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        write_private_file(target, rewritten_bytes)
        copied_files[path] = target
    source_target = target_for(source_config)
    effective_name = ".bb89n14-effective-" + hashlib.sha256(source_config.encode("utf-8")).hexdigest() + ".yaml"
    effective_path = os.path.join(os.path.dirname(source_target), effective_name)
    effective_bytes = (
        json.dumps(
            {
                "extends": source_target,
                "provider_tools": {"responses_stateful": False, "store": False},
                "providers": {"routing": {"disable_stream_on_probe_failure": False}},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    consume_file(effective_bytes)
    write_private_file(effective_path, effective_bytes)

    source_manifest_entries = sorted(manifest.values(), key=lambda entry: (entry["path"], entry["type"]))
    source_manifest_bytes = json.dumps(source_manifest_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    source_digest = "sha256:" + hashlib.sha256(source_manifest_bytes).hexdigest()
    materialized_digest, materialized_entries = lock_snapshot_and_digest()
    combined_bytes = json.dumps(
        {"materialized": materialized_digest, "source": source_digest},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    write_frame({
        "ok": True,
        "path": effective_path,
        "digest": "sha256:" + hashlib.sha256(combined_bytes).hexdigest(),
        "materializedDigest": materialized_digest,
        "manifestEntries": len(source_manifest_entries) + materialized_entries,
        "providerPersistence": "disabled",
        "providerStreaming": "required",
        "providerConversationState": "stateless",
    })
except BaseException as error:
    code = str(error)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", code):
        code = "config_snapshot_helper_failed"
    write_frame({"ok": False, "code": code})
`

const snapshotConfiguration = async (configPath, deadline, cleanupDeadline = deadline) => {
  const lexicalSourceConfig = resolve(configPath)
  const firstComponent = lexicalSourceConfig.split(sep).find((component) => component.length > 0)
  assertGate(firstComponent !== undefined, "config_path_not_regular_file", "config.snapshot")
  const lexicalSystemRoot = join(sep, firstComponent)
  const canonicalSystemRoot = await withinDeadline(realpath(lexicalSystemRoot), deadline, "config_snapshot_timeout", "config.snapshot")
  const sourceConfig = join(canonicalSystemRoot, relative(lexicalSystemRoot, lexicalSourceConfig))
  const repositoryRoot = await withinDeadline(realpath(REPOSITORY_ROOT), deadline, "config_snapshot_timeout", "config.snapshot")
  const trustedExternalRoot = await resolveTrustedExternalRoot(deadline)
  const lexicalRoot = await withinDeadline(mkdtemp(join(tmpdir(), "bb89n14-config-")), deadline, "config_snapshot_timeout", "config.snapshot")
  const root = await withinDeadline(realpath(lexicalRoot), deadline, "config_snapshot_timeout", "config.snapshot")
  await withinDeadline(chmod(root, 0o700), deadline, "config_snapshot_timeout", "config.snapshot")
  const child = spawn("/usr/bin/python3", ["-I", "-c", CONFIG_SNAPSHOT_HELPER], {
    env: { LANG: "C", LC_ALL: "C", PATH: "/usr/bin:/bin" },
    stdio: ["pipe", "pipe", "ignore"],
  })
  const control = Buffer.from(JSON.stringify({
    configPath: sourceConfig,
    repositoryRoot,
    snapshotRoot: root,
    trustedExternalRoot,
  }), "utf8")
  child.stdin.end(Buffer.concat([Buffer.from(`${control.byteLength}\n`, "ascii"), control]))
  const chunks = []
  let outputBytes = 0
  child.stdout.on("data", (chunk) => {
    outputBytes += chunk.byteLength
    if (outputBytes > 64 * 1024) child.kill("SIGTERM")
    else chunks.push(chunk)
  })
  const exit = new Promise((resolveExit) => {
    child.once("error", (error) => resolveExit({ code: null, signal: null, error }))
    child.once("close", (code, signal) => resolveExit({ code, signal, error: null }))
  })
  try {
    const result = await withinDeadline(exit, deadline, "config_snapshot_timeout", "config.snapshot")
    assertGate(result.error === null && result.code === 0 && result.signal === null, "config_snapshot_helper_failed", "config.snapshot")
    const output = Buffer.concat(chunks)
    const newline = output.indexOf(0x0a)
    assertGate(newline > 0 && newline <= 16, "config_snapshot_protocol", "config.snapshot")
    const declared = Number(output.subarray(0, newline).toString("ascii"))
    assertGate(Number.isSafeInteger(declared) && declared === output.byteLength - newline - 1, "config_snapshot_protocol", "config.snapshot")
    const response = JSON.parse(output.subarray(newline + 1).toString("utf8"))
    exactObject(
      response,
      response.ok === true
        ? ["ok", "path", "digest", "materializedDigest", "manifestEntries", "providerPersistence", "providerStreaming", "providerConversationState"]
        : ["ok", "code"],
      "config_snapshot_protocol",
      "config.snapshot",
    )
    if (response.ok !== true) fail(safeDiagnosticCode(response.code), "config.snapshot")
    assertGate(isInside(root, response.path), "config_snapshot_target_escape", "config.snapshot")
    assertGate(isSha256(response.digest), "config_snapshot_manifest_digest", "config.snapshot")
    assertGate(isSha256(response.materializedDigest), "config_snapshot_materialized_digest", "config.snapshot")
    assertGate(isInteger(response.manifestEntries, 1) && response.manifestEntries <= MAX_CONFIG_CLOSURE_FILES * 2, "config_snapshot_manifest_budget", "config.snapshot")
    assertGate(response.providerPersistence === PROVIDER_PERSISTENCE, "config_provider_persistence", "config.snapshot")
    assertGate(response.providerStreaming === PROVIDER_STREAMING, "config_provider_streaming", "config.snapshot")
    assertGate(response.providerConversationState === PROVIDER_CONVERSATION_STATE, "config_provider_conversation_state", "config.snapshot")
    return {
      root,
      path: response.path,
      digest: response.digest,
      materializedDigest: response.materializedDigest,
      providerPersistence: response.providerPersistence,
      providerStreaming: response.providerStreaming,
      providerConversationState: response.providerConversationState,
      sourcePath: sourceConfig,
      trustedExternalRoot,
    }
  } catch (error) {
    child.kill("SIGTERM")
    await withinDeadline(exit, cleanupDeadline, "cleanup_timeout", "cleanup").catch(() => undefined)
    await requiredCleanup([() => removeConfigurationSnapshotRequired(root)], cleanupDeadline)
    throw error
  }
}
const recomputeMaterializedConfigurationDigest = async (snapshot, deadline) => {
  const entries = []
  let totalBytes = 0
  const scan = async (directory, relativeDirectory = "") => {
    const names = await withinDeadline(
      readdir(directory),
      deadline,
      "config_snapshot_verify_timeout",
      "config.verify",
    )
    names.sort()
    const children = []
    for (const name of names) {
      const path = join(directory, name)
      const relativePath = relativeDirectory === "" ? name : `${relativeDirectory}/${name}`
      const before = await withinDeadline(lstat(path), deadline, "config_snapshot_verify_timeout", "config.verify")
      assertGate(!before.isSymbolicLink(), "config_snapshot_drift", "config.verify")
      let type
      let hash
      if (before.isDirectory()) {
        assertGate((before.mode & 0o777) === 0o500, "config_snapshot_permissions", "config.verify")
        const childEntries = await scan(path, relativePath)
        type = "directory"
        hash = sha256(Buffer.from(JSON.stringify(childEntries), "utf8"))
      } else {
        assertGate(before.isFile() && before.nlink === 1 && (before.mode & 0o777) === 0o400, "config_snapshot_permissions", "config.verify")
        const bytes = await withinDeadline(readFile(path), deadline, "config_snapshot_verify_timeout", "config.verify")
        totalBytes += bytes.byteLength
        assertGate(totalBytes <= MAX_CONFIG_CLOSURE_BYTES, "config_snapshot_verify_budget", "config.verify")
        const after = await withinDeadline(lstat(path), deadline, "config_snapshot_verify_timeout", "config.verify")
        assertGate(
          after.dev === before.dev
            && after.ino === before.ino
            && after.size === before.size
            && after.mtimeMs === before.mtimeMs
            && after.ctimeMs === before.ctimeMs
            && after.nlink === before.nlink,
          "config_snapshot_drift",
          "config.verify",
        )
        type = "file"
        hash = sha256(bytes)
      }
      const mode = (before.mode & 0o777).toString(8).padStart(4, "0")
      entries.push({ hash, mode, path: relativePath, type })
      children.push({ name, type })
      assertGate(entries.length <= MAX_CONFIG_CLOSURE_FILES * 2, "config_snapshot_verify_budget", "config.verify")
    }
    return children
  }
  const rootInfo = await withinDeadline(lstat(snapshot.root), deadline, "config_snapshot_verify_timeout", "config.verify")
  assertGate(rootInfo.isDirectory() && !rootInfo.isSymbolicLink() && (rootInfo.mode & 0o777) === 0o500, "config_snapshot_permissions", "config.verify")
  await scan(snapshot.root)
  entries.sort((left, right) => (
    left.path < right.path
      ? -1
      : left.path > right.path
        ? 1
        : left.type < right.type
          ? -1
          : left.type > right.type
            ? 1
            : 0
  ))
  return sha256(Buffer.from(JSON.stringify(entries), "utf8"))
}

const startConfigurationWatcher = async (snapshot, deadline) => {
  assertGate(Date.now() < deadline, "config_snapshot_watch_timeout", "config.watch")
  let mutation = null
  let watcher
  try {
    watcher = watch(snapshot.root, { recursive: true, persistent: false }, (eventType) => {
      mutation ??= eventType === "rename" ? "rename" : "change"
    })
    watcher.on("error", () => {
      mutation ??= "error"
    })
  } catch {
    fail("config_snapshot_watch_failed", "config.watch")
  }
  const verify = async (stageDeadline = deadline) => {
    await withinDeadline(
      new Promise((resolveTurn) => setImmediate(resolveTurn)),
      stageDeadline,
      "config_snapshot_watch_timeout",
      "config.watch",
    )
    assertGate(mutation === null, "config_snapshot_mutated", "config.watch")
    const digest = await recomputeMaterializedConfigurationDigest(snapshot, stageDeadline)
    assertGate(digest === snapshot.materializedDigest, "config_snapshot_drift", "config.verify")
    await withinDeadline(
      new Promise((resolveTurn) => setImmediate(resolveTurn)),
      stageDeadline,
      "config_snapshot_watch_timeout",
      "config.watch",
    )
    assertGate(mutation === null, "config_snapshot_mutated", "config.watch")
  }
  let closePromise = null
  const close = (stageDeadline = deadline) => {
    closePromise ??= (async () => {
      let capturedMutation
      try {
        await withinDeadline(
          new Promise((resolveTurn) => setImmediate(resolveTurn)),
          stageDeadline,
          "config_snapshot_watch_timeout",
          "config.watch",
        )
        capturedMutation = mutation
      } finally {
        watcher.close()
      }
      assertGate(capturedMutation === null, "config_snapshot_mutated", "config.watch")
      const digest = await recomputeMaterializedConfigurationDigest(snapshot, stageDeadline)
      assertGate(digest === snapshot.materializedDigest, "config_snapshot_drift", "config.verify")
    })()
    return closePromise
  }
  try {
    await verify(deadline)
  } catch (error) {
    watcher.close()
    throw error
  }
  return { verify, close }
}
const removeConfigurationSnapshotRequired = async (root) => {
  const unlock = async (path) => {
    const info = await lstat(path)
    assertGate(!info.isSymbolicLink(), "required_cleanup_failed", "cleanup")
    if (info.isDirectory()) {
      await chmod(path, 0o700)
      for (const name of await readdir(path)) await unlock(join(path, name))
    } else {
      assertGate(info.isFile(), "required_cleanup_failed", "cleanup")
      await chmod(path, 0o600)
    }
  }
  await unlock(root)
  await removeRequired(root, { recursive: true, force: true })
}



const parseReceipt = (value, stage) => {
  exactObject(value, ["clientMessageId", "inputId", "turnId", "disposition", "originalDisposition"], "evidence_receipt_schema", stage)
  return {
    clientMessageId: boundedText(value.clientMessageId, "evidence_receipt_text", stage, MAX_ID_TEXT),
    inputId: boundedText(value.inputId, "evidence_receipt_text", stage, MAX_ID_TEXT),
    turnId: boundedText(value.turnId, "evidence_receipt_text", stage, MAX_ID_TEXT),
    disposition: boundedText(value.disposition, "evidence_receipt_disposition", stage, 32),
    originalDisposition: boundedText(value.originalDisposition, "evidence_receipt_disposition", stage, 32),
  }
}

const parseSnapshot = (value, stage) => {
  exactObject(value, [
    "sessionId", "model", "turnAdmission", "activeTurnId", "queuedTurnCount", "terminalTurns",
    "headSequence", "headEventId", "retainedHistory", "sessionReplayContractDigest",
  ], "evidence_snapshot_schema", stage)
  assertGate(Array.isArray(value.terminalTurns) && value.terminalTurns.length <= MAX_TERMINAL_TURNS, "evidence_terminal_budget", stage)
  const terminalTurns = value.terminalTurns.map((terminal) => {
    exactObject(terminal, ["inputId", "turnId", "outcome", "originalDisposition"], "evidence_terminal_schema", stage)
    assertGate(["completed", "failed", "cancelled"].includes(terminal.outcome), "evidence_terminal_outcome", stage)
    assertGate(["started", "queued"].includes(terminal.originalDisposition), "evidence_terminal_disposition", stage)
    return {
      inputId: boundedText(terminal.inputId, "evidence_terminal_text", stage, MAX_ID_TEXT),
      turnId: boundedText(terminal.turnId, "evidence_terminal_text", stage, MAX_ID_TEXT),
      outcome: terminal.outcome,
      originalDisposition: terminal.originalDisposition,
    }
  })
  assertGate(["idle", "active"].includes(value.turnAdmission), "evidence_snapshot_admission", stage)
  assertGate(isInteger(value.queuedTurnCount) && isInteger(value.headSequence), "evidence_snapshot_integer", stage)
  assertGate(["complete", "partial"].includes(value.retainedHistory), "evidence_snapshot_retention", stage)
  assertGate(isSha256(value.sessionReplayContractDigest), "evidence_snapshot_digest", stage)
  return {
    sessionId: boundedText(value.sessionId, "evidence_snapshot_text", stage, MAX_ID_TEXT),
    model: nullableBoundedText(value.model, "evidence_snapshot_text", stage, MAX_ID_TEXT),
    turnAdmission: value.turnAdmission,
    activeTurnId: nullableBoundedText(value.activeTurnId, "evidence_snapshot_text", stage, MAX_ID_TEXT),
    queuedTurnCount: value.queuedTurnCount,
    terminalTurns,
    headSequence: value.headSequence,
    headEventId: nullableBoundedText(value.headEventId, "evidence_snapshot_text", stage, MAX_ID_TEXT),
    retainedHistory: value.retainedHistory,
    sessionReplayContractDigest: value.sessionReplayContractDigest,
  }
}

const parseSequence = (value, stage) => {
  exactObject(value, ["eventId", "sequence", "sessionId", "inputId", "turnId", "occurredAtMs", "kind"], "evidence_sequence_schema", stage)
  assertGate(isInteger(value.sequence, 1) && isInteger(value.occurredAtMs), "evidence_sequence_integer", stage)
  return {
    eventId: boundedText(value.eventId, "evidence_sequence_text", stage, MAX_ID_TEXT),
    sequence: value.sequence,
    sessionId: boundedText(value.sessionId, "evidence_sequence_text", stage, MAX_ID_TEXT),
    inputId: boundedText(value.inputId, "evidence_sequence_text", stage, MAX_ID_TEXT),
    turnId: boundedText(value.turnId, "evidence_sequence_text", stage, MAX_ID_TEXT),
    occurredAtMs: value.occurredAtMs,
    kind: boundedText(value.kind, "evidence_sequence_kind", stage, 128),
  }
}

const parseEnvelope = (value, stage) => {
  exactObject(value, ["eventId", "sequence", "sessionId", "inputId", "turnId", "occurredAtMs", "kind", "payload"], "evidence_event_schema", stage)
  assertGate(MAIN_CAPTURE_KINDS.has(value.kind), "evidence_event_kind", stage)
  const base = parseSequence({
    eventId: value.eventId,
    sequence: value.sequence,
    sessionId: value.sessionId,
    inputId: value.inputId,
    turnId: value.turnId,
    occurredAtMs: value.occurredAtMs,
    kind: value.kind,
  }, stage)
  let payload
  if (["input_observed", "assistant_text_delta", "assistant_text_completed"].includes(value.kind)) {
    exactObject(value.payload, ["text"], "evidence_event_payload_schema", stage)
    payload = {
      text: value.payload.text === null
        ? null
        : boundedText(value.payload.text, "evidence_event_text", stage),
    }
    assertGate(value.kind === "assistant_text_completed" || payload.text !== null, "evidence_event_null_text", stage)
  } else if (value.kind === "turn_started") {
    const keys = Reflect.ownKeys(value.payload)
    assertGate(keys.length === 0 || (keys.length === 1 && keys[0] === "mode"), "evidence_event_payload_schema", stage)
    payload = keys.length === 0 ? {} : { mode: boundedText(value.payload.mode, "evidence_event_mode", stage, 128) }
  } else {
    exactObject(value.payload, [], "evidence_event_payload_schema", stage)
    payload = {}
  }
  return { ...base, payload }
}

export function validateGateEvidence(evidence) {
  exactObject(evidence, ["schemaVersion", "ticket", "generatedAt", "threatModel", "providerPersistence", "providerStreaming", "providerConversationState", "provenance", "mainProof", "syntheticControl", "durability"], "evidence_schema", "evidence")
  assertGate(evidence.schemaVersion === "bb.p30.bb89n14.gate_evidence.v1", "evidence_schema", "evidence")
  assertGate(evidence.ticket === "bb-89n.14", "evidence_ticket", "evidence")
  assertGate(evidence.threatModel === THREAT_MODEL, "evidence_threat_model", "evidence")
  assertGate(evidence.providerPersistence === PROVIDER_PERSISTENCE, "evidence_provider_persistence", "evidence")
  assertGate(evidence.providerStreaming === PROVIDER_STREAMING, "evidence_provider_streaming", "evidence")
  assertGate(evidence.providerConversationState === PROVIDER_CONVERSATION_STATE, "evidence_provider_conversation_state", "evidence")
  const generatedAt = boundedText(evidence.generatedAt, "evidence_generated_at", "evidence", 64)
  assertGate(Number.isFinite(Date.parse(generatedAt)), "evidence_generated_at", "evidence")

  const provenanceValue = exactObject(evidence.provenance, [
    "backendCommit", "backendDirty", "clientCommit", "clientBuildManifestSha256", "configurationSha256",
    "protocolVersion", "engineVersion", "listenerKind", "providerEndpointSha256",
  ], "evidence_provenance_schema", "evidence")
  assertGate(isCommit(provenanceValue.backendCommit) && isCommit(provenanceValue.clientCommit), "evidence_commit_format", "evidence")
  assertGate(provenanceValue.clientCommit === CLIENT_BUILD_MANIFEST.commit, "evidence_client_manifest_commit", "evidence")
  assertGate(provenanceValue.backendDirty === false, "evidence_backend_not_clean", "evidence")
  assertGate(provenanceValue.clientBuildManifestSha256 === CLIENT_BUILD_MANIFEST_SHA256, "evidence_manifest_digest", "evidence")
  assertGate(isSha256(provenanceValue.configurationSha256), "evidence_configuration_digest", "evidence")
  assertGate(isSha256(provenanceValue.providerEndpointSha256), "evidence_provider_endpoint_digest", "evidence")
  const syntheticProviderEndpointSha256 = sha256(Buffer.from("synthetic-local-fixture", "utf8"))
  assertGate(
    provenanceValue.listenerKind === "gate-owned-canonical"
      ? [...APPROVED_OPENAI_PROVIDER_ENDPOINTS].some(
          (endpoint) => sha256(Buffer.from(endpoint, "utf8")) === provenanceValue.providerEndpointSha256,
        )
      : provenanceValue.providerEndpointSha256 === syntheticProviderEndpointSha256,
    "evidence_provider_endpoint_digest",
    "evidence",
  )
  assertGate(
    provenanceValue.listenerKind === "gate-owned-canonical" || provenanceValue.listenerKind === "tracked-python-fixture",
    "evidence_listener_kind",
    "evidence",
  )
  const provenance = {
    backendCommit: provenanceValue.backendCommit,
    backendDirty: false,
    clientCommit: provenanceValue.clientCommit,
    clientBuildManifestSha256: provenanceValue.clientBuildManifestSha256,
    configurationSha256: provenanceValue.configurationSha256,
    protocolVersion: nullableBoundedText(provenanceValue.protocolVersion, "evidence_protocol_version", "evidence", 128),
    engineVersion: nullableBoundedText(provenanceValue.engineVersion, "evidence_engine_version", "evidence", 128),
    listenerKind: provenanceValue.listenerKind,
    providerEndpointSha256: provenanceValue.providerEndpointSha256,
  }

  const mainValue = exactObject(evidence.mainProof, [
    "classification", "sessionId", "selected_model", "nonce", "requestText", "preSubmitSnapshot", "submitReceipt",
    "disconnect", "reconnect", "canonicalEventEnvelopes", "sequenceTrace", "assistantText", "completedTerminalCount",
    "streamedTerminal", "capturedHead", "finalSnapshot",
  ], "evidence_main_schema", "evidence")
  const expectedMainClassification = provenance.listenerKind === "gate-owned-canonical"
    ? "provider-correlated nonce observation"
    : "local synthetic backend observation"
  assertGate(mainValue.classification === expectedMainClassification, "main_proof_classification", "evidence")
  const selectedModel = boundedText(mainValue.selected_model, "evidence_selected_model", "evidence", MAX_ID_TEXT)
  assertExpectedProviderModel(selectedModel, selectedModel, "evidence")
  const nonce = boundedText(mainValue.nonce, "evidence_nonce", "evidence", 256)
  assertGate(/^BB89N14_[0-9a-f]{64}$/.test(nonce), "evidence_nonce_format", "evidence")
  const requestText = boundedText(mainValue.requestText, "evidence_request", "evidence")
  assertGate(requestText === `Return only this exact nonce and no other text: ${nonce}`, "evidence_request_nonce_binding", "evidence")
  const mainSessionId = boundedText(mainValue.sessionId, "evidence_session_id", "evidence", MAX_ID_TEXT)
  const preSubmitSnapshot = parseSnapshot(mainValue.preSubmitSnapshot, "evidence")
  requireNewSession(preSubmitSnapshot, "evidence")
  assertExpectedProviderModel(preSubmitSnapshot.model, selectedModel, "evidence")
  assertGate(preSubmitSnapshot.sessionId === mainSessionId, "evidence_session_identity", "evidence")
  const submitReceipt = parseReceipt(mainValue.submitReceipt, "evidence")
  assertGate(submitReceipt.disposition === "started" && submitReceipt.originalDisposition === "started", "evidence_main_not_started", "evidence")
  const disconnectValue = exactObject(mainValue.disconnect, ["stableEventId", "stableSequence", "uncommittedLookaheadEventId", "uncommittedLookaheadSequence"], "evidence_disconnect_schema", "evidence")
  const disconnect = {
    stableEventId: boundedText(disconnectValue.stableEventId, "evidence_disconnect_text", "evidence", MAX_ID_TEXT),
    stableSequence: disconnectValue.stableSequence,
    uncommittedLookaheadEventId: boundedText(disconnectValue.uncommittedLookaheadEventId, "evidence_disconnect_text", "evidence", MAX_ID_TEXT),
    uncommittedLookaheadSequence: disconnectValue.uncommittedLookaheadSequence,
  }
  assertGate(isInteger(disconnect.stableSequence, 1) && disconnect.uncommittedLookaheadSequence === disconnect.stableSequence + 1, "evidence_disconnect_sequence", "evidence")
  const reconnectValue = exactObject(mainValue.reconnect, ["firstEventId", "firstSequence", "exclusive", "duplicateApplied", "gapObserved", "cursorCommittedThroughHead"], "evidence_reconnect_schema", "evidence")
  const reconnect = {
    firstEventId: boundedText(reconnectValue.firstEventId, "evidence_reconnect_text", "evidence", MAX_ID_TEXT),
    firstSequence: reconnectValue.firstSequence,
    exclusive: reconnectValue.exclusive,
    duplicateApplied: reconnectValue.duplicateApplied,
    gapObserved: reconnectValue.gapObserved,
    cursorCommittedThroughHead: reconnectValue.cursorCommittedThroughHead,
  }
  assertGate(
    reconnect.exclusive === true
      && reconnect.duplicateApplied === false
      && reconnect.gapObserved === false
      && reconnect.cursorCommittedThroughHead === true,
    "evidence_resume_claim",
    "evidence",
  )
  assertGate(reconnect.firstEventId === disconnect.uncommittedLookaheadEventId && reconnect.firstSequence === disconnect.stableSequence + 1, "evidence_resume_sequence", "evidence")
  assertGate(Array.isArray(mainValue.canonicalEventEnvelopes) && mainValue.canonicalEventEnvelopes.length <= MAX_EVENTS, "evidence_event_budget", "evidence")
  assertGate(Array.isArray(mainValue.sequenceTrace) && mainValue.sequenceTrace.length <= MAX_EVENTS, "evidence_sequence_budget", "evidence")
  const canonicalEventEnvelopes = mainValue.canonicalEventEnvelopes.map((event) => parseEnvelope(event, "evidence"))
  const sequenceTrace = mainValue.sequenceTrace.map((event) => parseSequence(event, "evidence"))
  assertContiguousUnique(sequenceTrace, "evidence")
  for (const event of sequenceTrace) assertGate(event.inputId === submitReceipt.inputId && event.turnId === submitReceipt.turnId, "evidence_event_correlation", "evidence")
  assertGate(sequenceTrace.length > 0, "evidence_empty_trace", "evidence")
  const traceById = new Map(sequenceTrace.map((event) => [event.eventId, event]))
  assertGate(traceById.size === sequenceTrace.length, "evidence_duplicate_trace_id", "evidence")
  assertGate(sequenceTrace.every((event) => event.sessionId === mainSessionId), "evidence_session_identity", "evidence")
  const stableTrace = traceById.get(disconnect.stableEventId)
  assertGate(stableTrace?.sequence === disconnect.stableSequence, "evidence_disconnect_trace_binding", "evidence")
  const lookaheadTrace = traceById.get(disconnect.uncommittedLookaheadEventId)
  assertGate(lookaheadTrace?.sequence === disconnect.uncommittedLookaheadSequence, "evidence_lookahead_trace_binding", "evidence")
  assertGate(lookaheadTrace?.eventId === reconnect.firstEventId && lookaheadTrace?.sequence === reconnect.firstSequence, "evidence_reconnect_trace_binding", "evidence")
  const capturedIds = new Set()
  for (const event of canonicalEventEnvelopes) {
    assertGate(!capturedIds.has(event.eventId), "evidence_duplicate_envelope", "evidence")
    capturedIds.add(event.eventId)
    assertGate(event.inputId === submitReceipt.inputId && event.turnId === submitReceipt.turnId, "evidence_event_correlation", "evidence")
    const traceEvent = traceById.get(event.eventId)
    assertGate(
      traceEvent !== undefined
        && isDeepStrictEqual(traceEvent, {
          eventId: event.eventId,
          sequence: event.sequence,
          sessionId: event.sessionId,
          inputId: event.inputId,
          turnId: event.turnId,
          occurredAtMs: event.occurredAtMs,
          kind: event.kind,
        }),
      "evidence_envelope_trace_binding",
      "evidence",
    )
  }
  for (const [kind, count] of new Map([["input_observed", 1], ["turn_started", 1], ["assistant_text_completed", 1], ["turn_completed", 1]])) {
    assertGate(canonicalEventEnvelopes.filter((event) => event.kind === kind).length === count, "evidence_event_count", "evidence")
  }
  const orderedInput = canonicalEventEnvelopes.find((event) => event.kind === "input_observed")
  const orderedStart = canonicalEventEnvelopes.find((event) => event.kind === "turn_started")
  const orderedAssistant = canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_completed")
  const orderedTerminal = canonicalEventEnvelopes.find((event) => event.kind === "turn_completed")
  assertGate(
    orderedInput.sequence < orderedStart.sequence
      && orderedStart.sequence < orderedAssistant.sequence
      && orderedAssistant.sequence < orderedTerminal.sequence,
    "evidence_main_event_order",
    "evidence",
  )
  assertGate(
    canonicalEventEnvelopes
      .filter((event) => event.kind === "assistant_text_delta")
      .every((event) => event.sequence > orderedStart.sequence && event.sequence < orderedAssistant.sequence),
    "evidence_main_delta_order",
    "evidence",
  )
  const traceTerminals = sequenceTrace.filter((event) => TERMINAL_KINDS.has(event.kind))
  assertGate(traceTerminals.length === 1 && traceTerminals[0].kind === "turn_completed", "evidence_main_trace_terminal_set", "evidence")
  const inputEnvelope = canonicalEventEnvelopes.find((event) => event.kind === "input_observed")
  assertGate(inputEnvelope.payload.text === requestText, "evidence_input_request_binding", "evidence")
  const completedAssistantEnvelope = canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_completed")
  assertGate(completedAssistantEnvelope.payload.text === nonce, "evidence_assistant_nonce_binding", "evidence")
  const assistantDeltas = canonicalEventEnvelopes.filter((event) => event.kind === "assistant_text_delta")
  assertGate(assistantDeltas.length > 0, "evidence_assistant_delta_count", "evidence")
  assertGate(assistantDeltas.map((event) => event.payload.text).join("") === nonce, "evidence_delta_nonce_binding", "evidence")
  const assistantText = boundedText(mainValue.assistantText, "evidence_assistant_text", "evidence")
  assertGate(assistantText === nonce, "evidence_nonce_mismatch", "evidence")
  assertGate(assistantText === completedAssistantEnvelope.payload.text, "evidence_assistant_envelope_binding", "evidence")
  assertGate(mainValue.completedTerminalCount === 1, "evidence_terminal_count", "evidence")
  const streamedTerminal = parseSequence(mainValue.streamedTerminal, "evidence")
  assertGate(streamedTerminal.kind === "turn_completed", "evidence_streamed_terminal", "evidence")
  const terminalEnvelope = canonicalEventEnvelopes.find((event) => event.kind === "turn_completed")
  assertGate(isDeepStrictEqual(streamedTerminal, traceById.get(terminalEnvelope.eventId)), "evidence_stream_terminal_binding", "evidence")
  const capturedHeadValue = exactObject(mainValue.capturedHead, ["sequence", "eventId"], "evidence_head_schema", "evidence")
  const capturedHead = {
    sequence: capturedHeadValue.sequence,
    eventId: boundedText(capturedHeadValue.eventId, "evidence_head_event", "evidence", MAX_ID_TEXT),
  }
  assertGate(capturedHead.sequence >= streamedTerminal.sequence, "evidence_head_before_terminal", "evidence")
  const lastTrace = sequenceTrace.at(-1)
  assertGate(lastTrace.sequence === capturedHead.sequence && lastTrace.eventId === capturedHead.eventId, "evidence_head_trace_binding", "evidence")
  const finalSnapshot = parseSnapshot(mainValue.finalSnapshot, "evidence")
  requireIdle(finalSnapshot, "evidence")
  assertExpectedProviderModel(finalSnapshot.model, selectedModel, "evidence")
  assertGate(finalSnapshot.headSequence === capturedHead.sequence && finalSnapshot.headEventId === capturedHead.eventId, "evidence_final_head", "evidence")
  assertGate(finalSnapshot.sessionId === mainSessionId, "evidence_session_identity", "evidence")
  assertGate(finalSnapshot.terminalTurns.length === 1, "evidence_extra_final_terminal", "evidence")
  const matchingTerminal = finalSnapshot.terminalTurns.filter((terminal) => terminal.inputId === submitReceipt.inputId && terminal.turnId === submitReceipt.turnId)
  assertGate(matchingTerminal.length === 1 && matchingTerminal[0].outcome === "completed" && matchingTerminal[0].originalDisposition === "started", "evidence_final_terminal", "evidence")
  assertGate(streamedTerminal.inputId === submitReceipt.inputId && streamedTerminal.turnId === submitReceipt.turnId, "evidence_terminal_correlation", "evidence")

  const controlValue = exactObject(evidence.syntheticControl, [
    "classification", "sessionId", "fixtures", "initialSnapshot", "firstReceipt", "attachSnapshotBefore", "attachSnapshotAfter",
    "attachUnchanged", "secondReceipt", "thirdReceipt", "fifoSequences", "sequenceTrace", "capturedHead", "finalSnapshot",
  ], "evidence_control_schema", "evidence")
  assertGate(controlValue.classification === "provider-free synthetic control", "control_classification", "evidence")
  const controlSessionId = boundedText(controlValue.sessionId, "evidence_session_id", "evidence", MAX_ID_TEXT)
  exactObject(controlValue.fixtures, ["temporary", "outsideRepository", "controlledDelayMs"], "evidence_fixture_schema", "evidence")
  assertGate(controlValue.fixtures.temporary === true && controlValue.fixtures.outsideRepository === true && controlValue.fixtures.controlledDelayMs === SYNTHETIC_HOLD_MS, "evidence_fixture_claim", "evidence")
  const initialSnapshot = parseSnapshot(controlValue.initialSnapshot, "evidence")
  requireNewSession(initialSnapshot, "evidence")
  assertGate(initialSnapshot.sessionId === controlSessionId, "evidence_control_session_identity", "evidence")
  const firstReceipt = parseReceipt(controlValue.firstReceipt, "evidence")
  const secondReceipt = parseReceipt(controlValue.secondReceipt, "evidence")
  const thirdReceipt = parseReceipt(controlValue.thirdReceipt, "evidence")
  assertGate(firstReceipt.disposition === "started" && firstReceipt.originalDisposition === "started", "evidence_control_first", "evidence")
  assertGate(secondReceipt.disposition === "queued" && secondReceipt.originalDisposition === "queued", "evidence_control_queue", "evidence")
  assertGate(thirdReceipt.disposition === "queued" && thirdReceipt.originalDisposition === "queued", "evidence_control_queue", "evidence")
  const receipts = [firstReceipt, secondReceipt, thirdReceipt]
  for (const key of ["clientMessageId", "inputId", "turnId"]) {
    assertGate(new Set(receipts.map((receipt) => receipt[key])).size === receipts.length, "evidence_control_receipts_not_distinct", "evidence")
  }
  const attachSnapshotBefore = parseSnapshot(controlValue.attachSnapshotBefore, "evidence")
  const attachSnapshotAfter = parseSnapshot(controlValue.attachSnapshotAfter, "evidence")
  assertGate(controlValue.attachUnchanged === true && isDeepStrictEqual(attachSnapshotBefore, attachSnapshotAfter), "evidence_attach_changed", "evidence")
  assertGate(attachSnapshotBefore.sessionId === controlSessionId && attachSnapshotAfter.sessionId === controlSessionId, "evidence_control_session_identity", "evidence")
  assertGate(
    attachSnapshotBefore.turnAdmission === "active"
      && attachSnapshotBefore.activeTurnId === firstReceipt.turnId
      && attachSnapshotBefore.queuedTurnCount === 0,
    "evidence_control_attach_not_first_active",
    "evidence",
  )
  assertGate(Array.isArray(controlValue.sequenceTrace) && controlValue.sequenceTrace.length <= MAX_EVENTS, "evidence_control_trace_budget", "evidence")
  const controlTrace = controlValue.sequenceTrace.map((event) => parseSequence(event, "evidence"))
  assertContiguousUnique(controlTrace, "evidence")
  assertGate(controlTrace.length > 0 && controlTrace.every((event) => event.sessionId === controlSessionId), "evidence_control_trace_session", "evidence")
  const receiptForControlEvent = (event) => receipts.find(
    (receipt) => event.inputId === receipt.inputId && event.turnId === receipt.turnId,
  )
  assertGate(controlTrace.every((event) => receiptForControlEvent(event) !== undefined), "evidence_control_trace_correlation", "evidence")
  const exactControlEvent = (kind, receipt) => {
    const matches = controlTrace.filter(
      (event) => event.kind === kind && event.inputId === receipt.inputId && event.turnId === receipt.turnId,
    )
    assertGate(matches.length === 1, "evidence_control_event_cardinality", "evidence")
    return matches[0]
  }
  const firstStart = exactControlEvent("turn_started", firstReceipt)
  const firstTerminal = exactControlEvent("turn_completed", firstReceipt)
  const secondStart = exactControlEvent("turn_started", secondReceipt)
  const secondTerminal = exactControlEvent("turn_completed", secondReceipt)
  const thirdStart = exactControlEvent("turn_started", thirdReceipt)
  const thirdTerminal = exactControlEvent("turn_completed", thirdReceipt)
  assertGate(
    controlTrace.filter((event) => event.kind === "turn_started").length === 3
      && controlTrace.filter((event) => TERMINAL_KINDS.has(event.kind)).length === 3,
    "evidence_control_start_terminal_set",
    "evidence",
  )
  const fifoValue = exactObject(controlValue.fifoSequences, ["terminalFirst", "startSecond", "terminalSecond", "startThird", "terminalThird"], "evidence_fifo_schema", "evidence")
  const fifoSequences = { ...fifoValue }
  assertGate(Object.values(fifoSequences).every((value) => isInteger(value, 1)), "evidence_fifo_integer", "evidence")
  assertGate(
    fifoSequences.terminalFirst === firstTerminal.sequence
      && fifoSequences.startSecond === secondStart.sequence
      && fifoSequences.terminalSecond === secondTerminal.sequence
      && fifoSequences.startThird === thirdStart.sequence
      && fifoSequences.terminalThird === thirdTerminal.sequence,
    "evidence_fifo_trace_binding",
    "evidence",
  )
  assertGate(
    firstStart.sequence < fifoSequences.terminalFirst
      && fifoSequences.terminalFirst < fifoSequences.startSecond
      && fifoSequences.startSecond < fifoSequences.terminalSecond
      && fifoSequences.terminalSecond < fifoSequences.startThird
      && fifoSequences.startThird < fifoSequences.terminalThird,
    "evidence_fifo_order",
    "evidence",
  )
  const controlHeadValue = exactObject(controlValue.capturedHead, ["sequence", "eventId"], "evidence_control_head_schema", "evidence")
  const controlCapturedHead = {
    sequence: controlHeadValue.sequence,
    eventId: boundedText(controlHeadValue.eventId, "evidence_control_head_event", "evidence", MAX_ID_TEXT),
  }
  assertGate(isInteger(controlCapturedHead.sequence, 1), "evidence_control_head_sequence", "evidence")
  const lastControlEvent = controlTrace.at(-1)
  assertGate(
    lastControlEvent.sequence === controlCapturedHead.sequence && lastControlEvent.eventId === controlCapturedHead.eventId,
    "evidence_control_head_trace_binding",
    "evidence",
  )
  const controlFinalSnapshot = parseSnapshot(controlValue.finalSnapshot, "evidence")
  requireIdle(controlFinalSnapshot, "evidence")
  assertGate(controlFinalSnapshot.sessionId === controlSessionId, "evidence_control_session_identity", "evidence")
  assertGate(controlFinalSnapshot.terminalTurns.length === 3, "evidence_control_terminal_cardinality", "evidence")
  assertGate(
    controlFinalSnapshot.headSequence === controlCapturedHead.sequence
      && controlFinalSnapshot.headEventId === controlCapturedHead.eventId,
    "evidence_control_final_head",
    "evidence",
  )
  const expectedTerminalSet = new Set(receipts.map((receipt) => `${receipt.inputId}\u0000${receipt.turnId}`))
  const actualTerminalSet = new Set(controlFinalSnapshot.terminalTurns.map((terminal) => `${terminal.inputId}\u0000${terminal.turnId}`))
  assertGate(
    expectedTerminalSet.size === actualTerminalSet.size
      && [...expectedTerminalSet].every((identity) => actualTerminalSet.has(identity)),
    "evidence_control_terminal_set",
    "evidence",
  )
  for (const receipt of receipts) {
    const terminals = terminalFor(controlFinalSnapshot, receipt)
    assertGate(terminals.length === 1 && terminals[0].outcome === "completed" && terminals[0].originalDisposition === receipt.originalDisposition, "evidence_control_terminal", "evidence")
  }

  const durabilityValue = exactObject(evidence.durability, ["restartPerformed", "claim"], "evidence_durability_schema", "evidence")
  assertGate(durabilityValue.restartPerformed === false, "restart_claim_mismatch", "evidence")
  assertGate(durabilityValue.claim === "in-memory only; process restart not exercised", "durability_claim_mismatch", "evidence")

  return {
    schemaVersion: "bb.p30.bb89n14.gate_evidence.v1",
    ticket: "bb-89n.14",
    threatModel: THREAT_MODEL,
    providerPersistence: PROVIDER_PERSISTENCE,
    providerStreaming: PROVIDER_STREAMING,
    providerConversationState: PROVIDER_CONVERSATION_STATE,
    generatedAt,
    provenance,
    mainProof: {
      classification: expectedMainClassification,
      sessionId: mainSessionId,
      selected_model: selectedModel,
      nonce,
      requestText,
      preSubmitSnapshot,
      submitReceipt,
      disconnect,
      reconnect,
      canonicalEventEnvelopes,
      sequenceTrace,
      assistantText,
      completedTerminalCount: 1,
      streamedTerminal,
      capturedHead,
      finalSnapshot,
    },
    syntheticControl: {
      classification: "provider-free synthetic control",
      sessionId: controlSessionId,
      fixtures: { temporary: true, outsideRepository: true, controlledDelayMs: SYNTHETIC_HOLD_MS },
      initialSnapshot,
      firstReceipt,
      attachSnapshotBefore,
      attachSnapshotAfter,
      attachUnchanged: true,
      secondReceipt,
      thirdReceipt,
      fifoSequences,
      sequenceTrace: controlTrace,
      capturedHead: controlCapturedHead,
      finalSnapshot: controlFinalSnapshot,
    },
    durability: { restartPerformed: false, claim: "in-memory only; process restart not exercised" },
  }
}

const PINNED_OUTPUT_WRITER = String.raw`import errno, fcntl, json, os, re, signal, stat, sys, time

directory_fd = -1
reservation_owned = False
reservation_fd = -1
temporary_fd = -1
temporary_created = False
ready = False
committed_identity = None

def interrupted(signum, frame):
    raise TimeoutError("absolute_deadline_exceeded")

def reject(code):
    raise RuntimeError(code)

def read_frame():
    header = sys.stdin.buffer.readline(32)
    if not header or not header.endswith(b"\n") or not header[:-1].isdigit():
        reject("pinned_output_writer_protocol")
    size = int(header[:-1])
    if size <= 0 or size > 65536:
        reject("pinned_output_writer_protocol")
    payload = sys.stdin.buffer.read(size)
    if len(payload) != size:
        reject("pinned_output_writer_protocol")
    return json.loads(payload)

def protocol_line(value):
    os.write(sys.stdout.fileno(), value.encode("ascii") + b"\n")

def reject_existing_final():
    try:
        os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    reject("output_already_exists")

try:
    control = read_frame()
    required = {"directory", "finalName", "expectedDev", "expectedIno", "temporaryName", "deadlineMs"}
    if not isinstance(control, dict) or set(control) != required:
        reject("pinned_output_writer_protocol")
    directory = control["directory"]
    final_name = control["finalName"]
    expected_dev = control["expectedDev"]
    expected_ino = control["expectedIno"]
    temporary_name = control["temporaryName"]
    deadline_ms = control["deadlineMs"]
    if (
        not all(isinstance(value, str) and value for value in (directory, final_name, expected_dev, expected_ino, temporary_name))
        or os.path.basename(final_name) != final_name
        or os.path.basename(temporary_name) != temporary_name
        or not isinstance(deadline_ms, int)
    ):
        reject("pinned_output_writer_protocol")
    output_target = os.path.join(directory, final_name)
    if any(
        directory in argument or output_target in argument or argument == final_name
        for argument in sys.argv[1:]
    ):
        reject("output_target_in_argv")

    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    remaining = (deadline_ms / 1000.0) - time.time()
    if remaining <= 0:
        reject("absolute_deadline_exceeded")
    signal.setitimer(signal.ITIMER_REAL, remaining)

    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    pinned = os.fstat(directory_fd)
    if str(pinned.st_dev) != expected_dev or str(pinned.st_ino) != expected_ino:
        reject("output_directory_changed")

    reservation_name = "." + final_name + ".bb89n14.lock"
    for _ in range(4):
        try:
            existing_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing_reservation = None
        if existing_reservation is not None:
            safe_existing_reservation = (
                stat.S_ISREG(existing_reservation.st_mode)
                and stat.S_IMODE(existing_reservation.st_mode) == 0o600
                and existing_reservation.st_nlink == 1
                and existing_reservation.st_uid == os.geteuid()
            )
            if not safe_existing_reservation:
                if stat.S_ISDIR(existing_reservation.st_mode):
                    try:
                        os.rmdir(reservation_name, dir_fd=directory_fd)
                    except OSError as error:
                        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                            raise
                        reject_existing_final()
                        reject("unsafe_output_reservation")
                else:
                    os.unlink(reservation_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
                continue
        try:
            reservation_fd = os.open(
                reservation_name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except OSError as error:
            if error.errno != errno.ELOOP:
                raise
            existing_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISLNK(existing_reservation.st_mode):
                reject("unsafe_output_reservation")
            os.unlink(reservation_name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            continue
        reservation_info = os.fstat(reservation_fd)
        safe_reservation = (
            stat.S_ISREG(reservation_info.st_mode)
            and stat.S_IMODE(reservation_info.st_mode) == 0o600
            and reservation_info.st_nlink == 1
            and reservation_info.st_uid == os.geteuid()
        )
        if safe_reservation:
            break
        current_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            current_reservation.st_dev != reservation_info.st_dev
            or current_reservation.st_ino != reservation_info.st_ino
        ):
            reject("output_reservation_changed")
        os.unlink(reservation_name, dir_fd=directory_fd)
        os.close(reservation_fd)
        reservation_fd = -1
        os.fsync(directory_fd)
    if reservation_fd < 0:
        reject("unsafe_output_reservation")
    try:
        fcntl.flock(reservation_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        reject("output_reserved")
    reservation_owned = True
    current_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        current_reservation.st_dev != reservation_info.st_dev
        or current_reservation.st_ino != reservation_info.st_ino
    ):
        reject("output_reservation_changed")

    reject_existing_final()

    temporary_fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    temporary_created = True
    opened = os.fstat(temporary_fd)
    if not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1:
        reject("temporary_identity")
    ready = True
    protocol_line("READY")

    data = sys.stdin.buffer.read(${MAX_EVIDENCE_BYTES + 1})
    if not data or len(data) > ${MAX_EVIDENCE_BYTES}:
        reject("evidence_budget")
    if data == b"ABORT\n":
        os.close(temporary_fd)
        temporary_fd = -1
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_created = False
        current_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
        locked_reservation = os.fstat(reservation_fd)
        if (
            current_reservation.st_dev != locked_reservation.st_dev
            or current_reservation.st_ino != locked_reservation.st_ino
        ):
            reject("output_reservation_changed")
        os.unlink(reservation_name, dir_fd=directory_fd)
        reservation_owned = False
        os.fsync(directory_fd)
        protocol_line("ABORTED")
        os._exit(0)
    view = memoryview(data)
    while view:
        written = os.write(temporary_fd, view)
        if written <= 0:
            reject("short_write")
        view = view[written:]
    os.fsync(temporary_fd)
    after_write = os.fstat(temporary_fd)
    if (
        not stat.S_ISREG(after_write.st_mode)
        or after_write.st_dev != opened.st_dev
        or after_write.st_ino != opened.st_ino
        or stat.S_IMODE(after_write.st_mode) != 0o600
        or after_write.st_nlink != 1
    ):
        reject("temporary_changed")
    os.close(temporary_fd)
    temporary_fd = -1

    current = os.stat(directory, follow_symlinks=False)
    if str(current.st_dev) != expected_dev or str(current.st_ino) != expected_ino:
        reject("output_directory_changed")
    try:
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        reject("output_reappeared")
    linked = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    if linked.st_dev != opened.st_dev or linked.st_ino != opened.st_ino or linked.st_nlink != 2:
        reject("final_identity")
    committed_identity = (linked.st_dev, linked.st_ino)
    os.unlink(temporary_name, dir_fd=directory_fd)
    temporary_created = False
    os.fsync(directory_fd)
    final_info = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(final_info.st_mode)
        or (final_info.st_dev, final_info.st_ino) != committed_identity
        or stat.S_IMODE(final_info.st_mode) != 0o600
        or final_info.st_nlink != 1
    ):
        reject("final_identity")
    protocol_line("COMMITTED")
except BaseException as error:
    if committed_identity is not None and directory_fd >= 0:
        try:
            current_final = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
            if (current_final.st_dev, current_final.st_ino) == committed_identity:
                os.unlink(final_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
        except FileNotFoundError:
            pass
    code = str(error)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", code):
        code = "pinned_output_writer_failed"
    protocol_line("ERROR " + code)
    raise SystemExit(1)
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
    if temporary_fd >= 0:
        os.close(temporary_fd)
    if temporary_created and directory_fd >= 0:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
    if reservation_fd >= 0:
        if reservation_owned:
            try:
                current_reservation = os.stat(reservation_name, dir_fd=directory_fd, follow_symlinks=False)
                locked_reservation = os.fstat(reservation_fd)
                if (
                    current_reservation.st_dev == locked_reservation.st_dev
                    and current_reservation.st_ino == locked_reservation.st_ino
                ):
                    os.unlink(reservation_name, dir_fd=directory_fd)
                    os.fsync(directory_fd)
            except FileNotFoundError:
                pass
        os.close(reservation_fd)
    if directory_fd >= 0:
        os.close(directory_fd)
`

const startPinnedOutputWriter = async (target, deadline, startDeadline = deadline) => {
  const leaf = target.pin.ancestors.at(-1)
  const temporaryName = `.${basename(target.outputPath)}.${process.pid}.${randomUUID()}.tmp`
  const protocol = { text: "", errorCode: null }
  const child = spawn("/usr/bin/python3", ["-I", "-c", PINNED_OUTPUT_WRITER], {
    env: { LANG: "C", LC_ALL: "C", PATH: "/usr/bin:/bin" },
    stdio: ["pipe", "pipe", "ignore"],
  })
  child.stdin.on("error", () => undefined)
  const control = Buffer.from(JSON.stringify({
    directory: target.pin.directory,
    finalName: basename(target.outputPath),
    expectedDev: leaf.dev,
    expectedIno: leaf.ino,
    temporaryName,
    deadlineMs: deadline,
  }), "utf8")
  child.stdin.write(Buffer.concat([Buffer.from(`${control.byteLength}\n`, "ascii"), control]))
  let resolveReady
  let rejectReady
  let readySettled = false
  const ready = new Promise((resolvePromise, rejectPromise) => {
    resolveReady = resolvePromise
    rejectReady = rejectPromise
  })
  child.stdout.on("data", (chunk) => {
    protocol.text += chunk.toString("utf8")
    if (protocol.text.length > 512) {
      if (!readySettled) rejectReady(new GateFailure("pinned_output_writer_protocol", "output"))
      readySettled = true
      child.kill("SIGTERM")
      return
    }
    const errorMatch = protocol.text.match(/(?:^|\n)ERROR ([A-Za-z0-9_.-]{1,128})\n/)
    if (errorMatch !== null) {
      protocol.errorCode = errorMatch[1]
      if (!readySettled) {
        readySettled = true
        rejectReady(new GateFailure(protocol.errorCode, "output"))
      }
      return
    }
    if (protocol.text.startsWith("READY\n") && !readySettled) {
      readySettled = true
      resolveReady()
    } else if (
      !protocol.text.startsWith("READY\n")
      && !"READY\n".startsWith(protocol.text)
      && !"ERROR ".startsWith(protocol.text)
      && !/^ERROR [A-Za-z0-9_.-]{0,128}$/.test(protocol.text)
    ) {
      readySettled = true
      rejectReady(new GateFailure("pinned_output_writer_protocol", "output"))
      child.kill("SIGTERM")
    }
  })
  const exit = new Promise((resolveExit) => {
    child.once("error", (error) => resolveExit({ code: null, signal: null, error }))
    child.once("close", (code, signal) => resolveExit({ code, signal, error: null }))
  })
  void exit.then(() => {
    if (!readySettled) {
      readySettled = true
      rejectReady(new GateFailure(protocol.errorCode ?? "pinned_output_writer_start_failed", "output"))
    }
  })
  try {
    await withinDeadline(ready, startDeadline, "output_writer_start_timeout", "output")
  } catch (error) {
    child.kill("SIGTERM")
    await withinDeadline(exit, deadline, "output_writer_stop_timeout", "cleanup").catch(() => undefined)
    throw error
  }
  return { child, exit, protocol, finished: false }
}

const stopPinnedOutputWriter = async (writer, deadline) => {
  if (writer === null || writer.finished) return
  writer.child.stdin.end("ABORT\n")
  let result
  try {
    result = await withinDeadline(writer.exit, deadline, "cleanup_timeout", "cleanup")
  } catch {
    writer.child.kill("SIGTERM")
    result = await withinDeadline(writer.exit, deadline, "cleanup_timeout", "cleanup")
  }
  writer.finished = true
  assertGate(
    result.error === null
      && result.code === 0
      && result.signal === null
      && writer.protocol.text === "READY\nABORTED\n",
    "required_cleanup_failed",
    "cleanup",
  )
}

const commitPinnedOutput = async (writer, serialized, deadline) => {
  assertGate(!writer.finished, "output_writer_already_closed", "output")
  writer.child.stdin.end(serialized)
  const result = await withinDeadline(writer.exit, deadline, "output_write_timeout", "output")
  writer.finished = true
  assertGate(result.error === null && result.code === 0 && result.signal === null, writer.protocol.errorCode ?? "pinned_output_write_failed", "output")
  assertGate(
    writer.protocol.text === "READY\nCOMMITTED\n",
    writer.protocol.errorCode ?? "pinned_output_write_failed",
    "output",
  )
}
export const writeEvidenceAtomically = async (_target, writer, evidence, deadline) => {
  const serialized = Buffer.from(`${JSON.stringify(evidence, null, 2)}\n`, "utf8")
  assertGate(serialized.byteLength <= MAX_EVIDENCE_BYTES, "evidence_byte_budget_exceeded", "output")
  await commitPinnedOutput(writer, serialized, deadline)
  return evidence
}


const assertEvidenceExcludesForbiddenStrings = (evidence, forbiddenStrings) => {
  const forbidden = [...forbiddenStrings].filter((value) => typeof value === "string" && value.length > 0)
  const visit = (value) => {
    if (typeof value === "string") {
      assertGate(!isAbsolute(value) && !/^file:/i.test(value), "evidence_forbidden_string", "evidence")
      assertGate(
        forbidden.every((candidate) => !value.includes(candidate)),
        "evidence_forbidden_string",
        "evidence",
      )
      return
    }
    if (Array.isArray(value)) {
      for (const item of value) visit(item)
      return
    }
    if (value !== null && typeof value === "object") {
      for (const item of Object.values(value)) visit(item)
    }
  }
  visit(evidence)
}

export async function executeEvidenceRun(
  target,
  writer,
  produceEvidence,
  deadline = Date.now() + DEFAULT_RUN_TIMEOUT_MS,
  beforeWrite = async () => {},
  cleanupDeadline = deadline,
  forbiddenStrings = [],
) {
  try {
    const produced = await produceEvidence()
    const evidence = validateGateEvidence(produced)
    assertEvidenceExcludesForbiddenStrings(evidence, forbiddenStrings)
    assertGate(Date.now() < deadline, "absolute_deadline_exceeded", "evidence")
    await beforeWrite()
    await writeEvidenceAtomically(target, writer, evidence, cleanupDeadline)
    return evidence
  } catch (error) {
    await stopPinnedOutputWriter(writer, cleanupDeadline)
    throw error
  }
}

async function produceGateEvidence(args, createCanonicalE4Client, authToken, deadline, cleanupDeadline) {
  const attestedListener = args.ownedBackend === null
    ? await attestBackendListener(args.baseUrl, args.expectedBackendCommit, deadline)
    : await attestOwnedBackendListener(args.ownedBackend, deadline)
  const listenerIdentity = args.ownedBackend === null
    ? { ...attestedListener, kind: "tracked-python-fixture" }
    : attestedListener
  assertGate(
    listenerIdentity.kind !== "tracked-python-fixture" || authToken === undefined,
    "synthetic_backend_auth_forbidden",
    "provenance.listener",
  )
  const configWatcher = await startConfigurationWatcher(args.configSnapshot, deadline)
  let mainSession = null
  try {
  const backend = await readBackendProvenance({
    baseUrl: args.baseUrl,
    authToken,
    expectedCommit: args.expectedBackendCommit,
    deadline,
  })
  const operationSignal = AbortSignal.timeout(Math.max(1, deadline - Date.now()))
  const boundedFetch = (input, init = {}) => fetch(input, {
    ...init,
    signal: init.signal === undefined
      ? operationSignal
      : AbortSignal.any([init.signal, operationSignal]),
  })
  const client = createCanonicalE4Client({
    baseUrl: args.baseUrl,
    authToken,
    fetch: boundedFetch,
    requestTimeoutMs: Math.min(30_000, Math.max(1, deadline - Date.now())),
  })
  const nonce = `BB89N14_${randomBytes(32).toString("hex")}`
  const requestText = `Return only this exact nonce and no other text: ${nonce}`
    await configWatcher.verify(deadline)
    mainSession = await withinDeadline(client.create({
      configPath: args.configSnapshot.path,
      workspace: args.workspace,
      stream: true,
      maxSteps: 1,
      metadata: {
        gate: "bb-89n.14",
        proof: listenerIdentity.kind === "gate-owned-canonical"
          ? "provider-correlated-nonce-observation"
          : "local-synthetic-backend-observation",
        model: args.expectedProviderModel,
        configuration_sha256: args.configSnapshot.digest,
      },
    }), deadline, "create_timeout", "main.create")
    await configWatcher.verify(deadline)
    const preSubmitSnapshot = await withinDeadline(mainSession.snapshot(), deadline, "snapshot_timeout", "main.pre_submit")
    requireNewSession(preSubmitSnapshot)
    assertExpectedProviderModel(preSubmitSnapshot.model, args.expectedProviderModel, "main.pre_submit")
    const observed = await observeMainProof(mainSession, nonce, requestText, deadline, cleanupDeadline)
    const finalSnapshot = await withinDeadline(mainSession.snapshot(), deadline, "snapshot_timeout", "main.final_snapshot")
    requireIdle(finalSnapshot, "main.final_snapshot")
    assertExpectedProviderModel(finalSnapshot.model, args.expectedProviderModel, "main.final_snapshot")
    assertGate(finalSnapshot.headSequence === observed.capturedHead.sequence && finalSnapshot.headEventId === observed.capturedHead.eventId, "final_snapshot_head_mismatch", "main.final_snapshot")
    const terminals = terminalFor(finalSnapshot, observed.receipt)
    assertGate(terminals.length === 1, "final_terminal_count", "main.final_snapshot")
    assertGate(terminals[0].outcome === "completed", "final_terminal_outcome", "main.final_snapshot")
    assertGate(terminals[0].originalDisposition === "started", "final_terminal_disposition", "main.final_snapshot")
    assertGate(observed.streamedTerminal.inputId === String(terminals[0].inputId) && observed.streamedTerminal.turnId === String(terminals[0].turnId), "stream_snapshot_terminal_mismatch", "main.final_snapshot")
    await configWatcher.verify(deadline)

    const syntheticControl = await runSyntheticControl({
      client,
      configPath: args.configSnapshot.path,
      configurationDigest: args.configSnapshot.digest,
      verifyConfiguration: () => configWatcher.verify(deadline),
      forbiddenEvidenceStrings: args.forbiddenEvidenceStrings,
      workspace: args.workspace,
      deadline,
      cleanupDeadline,
    })
    await configWatcher.verify(deadline)
    if (args.ownedBackend !== null) {
      const runtimeReattested = await attestOwnedBackendListener(args.ownedBackend, deadline)
      assertGate(
        runtimeReattested.pid === listenerIdentity.pid && runtimeReattested.commit === listenerIdentity.commit,
        "backend_listener_changed",
        "provenance.listener",
      )
    }

    return {
      schemaVersion: "bb.p30.bb89n14.gate_evidence.v1",
      ticket: "bb-89n.14",
      generatedAt: new Date().toISOString(),
      threatModel: THREAT_MODEL,
      providerPersistence: args.configSnapshot.providerPersistence,
      providerStreaming: args.configSnapshot.providerStreaming,
      providerConversationState: args.configSnapshot.providerConversationState,
      provenance: {
        backendCommit: backend.commit,
        backendDirty: backend.dirty,
        clientCommit: args.expectedClientCommit,
        clientBuildManifestSha256: CLIENT_BUILD_MANIFEST_SHA256,
        configurationSha256: args.configSnapshot.digest,
        protocolVersion: backend.protocolVersion,
        engineVersion: backend.engineVersion,
        listenerKind: listenerIdentity.kind,
        providerEndpointSha256: args.ownedBackend === null
          ? sha256(Buffer.from("synthetic-local-fixture", "utf8"))
          : args.ownedBackend.providerEndpointSha256,
      },
      mainProof: {
        classification: listenerIdentity.kind === "gate-owned-canonical"
          ? "provider-correlated nonce observation"
          : "local synthetic backend observation",
        sessionId: String(mainSession.sessionId),
        selected_model: args.expectedProviderModel,
        nonce,
        requestText,
        preSubmitSnapshot: projectSnapshot(preSubmitSnapshot, "main.evidence"),
        submitReceipt: receiptEvidence(observed.receipt),
        disconnect: observed.disconnect,
        reconnect: observed.reconnect,
        canonicalEventEnvelopes: observed.events,
        sequenceTrace: observed.sequenceTrace,
        assistantText: observed.assistantText,
        completedTerminalCount: 1,
        streamedTerminal: observed.streamedTerminal,
        capturedHead: observed.capturedHead,
        finalSnapshot: projectSnapshot(finalSnapshot, "main.evidence"),
      },
      syntheticControl,
      durability: {
        restartPerformed: false,
        claim: "in-memory only; process restart not exercised",
      },
    }
  } finally {
    let watcherError = null
    try {
      await configWatcher.close(cleanupDeadline)
    } catch (error) {
      watcherError = error
    }
    let sessionError = null
    try {
      await requiredCleanup(
        mainSession === null ? [] : [() => mainSession.close()],
        cleanupDeadline,
      )
    } catch (error) {
      sessionError = error
    }
    let listenerError = null
    try {
      if (args.ownedBackend === null) {
        await attestBackendListener(args.baseUrl, args.expectedBackendCommit, cleanupDeadline, attestedListener)
      } else {
        const reattested = await attestOwnedBackendListener(args.ownedBackend, cleanupDeadline, false)
        assertGate(
          reattested.pid === listenerIdentity.pid && reattested.commit === listenerIdentity.commit,
          "backend_listener_changed",
          "provenance.listener",
        )
      }
    } catch (error) {
      listenerError = error
    }
    if (watcherError !== null) throw watcherError
    if (sessionError !== null) throw sessionError
    if (listenerError !== null) throw listenerError
  }
}

const safeDiagnosticCode = (value, allowlist = null) => (
  typeof value === "string"
  && value.length <= 128
  && /^[A-Za-z0-9_.-]+$/.test(value)
  && (allowlist === null || allowlist.has(value))
    ? value
    : "redacted"
)

const diagnosticFor = (error) => {
  if (error instanceof GateFailure) {
    return {
      type: "gate_failure",
      stage: safeDiagnosticCode(error.stage),
      code: safeDiagnosticCode(error.code),
    }
  }
  if (CanonicalE4ClientErrorClass !== null && error instanceof CanonicalE4ClientErrorClass) {
    const failure = error.failure
    return {
      type: "canonical_client_failure",
      kind: SAFE_FAILURE_KINDS.has(failure.kind) ? failure.kind : "redacted",
      ...(typeof failure.code === "string" ? { code: safeDiagnosticCode(failure.code, SAFE_DIAGNOSTIC_CODES) } : {}),
      ...(Number.isInteger(failure.status) && failure.status >= 400 && failure.status <= 599 ? { status: failure.status } : {}),
    }
  }
  return { type: "internal_failure", code: "redacted" }
}

const runMain = async (argv, syntheticTest) => {
  const invocationStartedAt = Date.now()
  let finalDeadline = invocationStartedAt + DEFAULT_RUN_TIMEOUT_MS
  let operationDeadline = operationDeadlineFor(invocationStartedAt, finalDeadline)
  let outputTarget = null
  let outputWriter = null
  let configSnapshot = null
  let ownedBackend = null
  let executionWorkspace = null
  try {
    const preflight = preparseInvocation(argv)
    finalDeadline = invocationStartedAt + preflight.timeoutMs
    operationDeadline = operationDeadlineFor(invocationStartedAt, finalDeadline)
    outputTarget = await withinDeadline(prepareOutput(preflight.outputPath), operationDeadline, "output_preflight_timeout", "output")
    outputWriter = await startPinnedOutputWriter(outputTarget, finalDeadline, operationDeadline)
    const parsed = parseArgs(argv)
    const validatedArgs = await withinDeadline(validateInputPaths(parsed, outputTarget), operationDeadline, "path_validation_timeout", "cli")
    executionWorkspace = join(validatedArgs.workspace, `bb89n14-main-${randomUUID()}`)
    await withinDeadline(mkdir(executionWorkspace, { recursive: false, mode: 0o700 }), operationDeadline, "workspace_setup_timeout", "main.setup")
    const args = { ...validatedArgs, workspace: executionWorkspace }
    configSnapshot = await snapshotConfiguration(args.configPath, operationDeadline, finalDeadline)
    const createCanonicalE4Client = await loadVerifiedCanonicalClient(args.expectedClientCommit, operationDeadline)
    const callerAuthToken = readAuthToken()
    const capturedProviderValues = capturedProviderEvidenceValues()
    if (!syntheticTest) ownedBackend = await startOwnedCanonicalBackend(args, operationDeadline, finalDeadline)
    const authToken = syntheticTest ? callerAuthToken : ownedBackend.token
    const effectiveArgs = {
      ...args,
      baseUrl: syntheticTest ? args.baseUrl : ownedBackend.baseUrl,
      ownedBackend,
      configSnapshot,
    }
    const forbiddenEvidenceStrings = new Set([
      callerAuthToken,
      authToken,
      ...capturedProviderValues,
      args.configPath,
      dirname(args.configPath),
      configSnapshot.sourcePath,
      configSnapshot.path,
      configSnapshot.root,
      args.workspace,
      validatedArgs.workspace,
      outputTarget.outputPath,
      outputTarget.pin.directory,
      ...outputTarget.pin.ancestors.map(({ path }) => path).filter((path) => path !== sep),
      args.backendRoot,
      args.backendPython,
      REPOSITORY_ROOT,
      configSnapshot.trustedExternalRoot,
    ])
    effectiveArgs.forbiddenEvidenceStrings = forbiddenEvidenceStrings
    await executeEvidenceRun(
      outputTarget,
      outputWriter,
      () => produceGateEvidence(
        effectiveArgs,
        createCanonicalE4Client,
        authToken,
        operationDeadline,
        finalDeadline,
      ),
      operationDeadline,
      async () => {
        if (ownedBackend !== null) {
          await stopOwnedCanonicalBackend(ownedBackend, finalDeadline)
          ownedBackend = null
        }
        await requiredCleanup([
          async () => {
            await removeRequired(executionWorkspace, { recursive: true, force: true })
            executionWorkspace = null
          },
          async () => {
            await removeConfigurationSnapshotRequired(configSnapshot.root)
            configSnapshot = null
          },
        ], finalDeadline)
      },
      finalDeadline,
      forbiddenEvidenceStrings,
    )
    process.stdout.write(`${JSON.stringify({ ok: true })}\n`)
    return 0
  } catch (error) {
    let reported = error
    try {
      await stopPinnedOutputWriter(outputWriter, finalDeadline)
    } catch (cleanupError) {
      reported = cleanupError
    }
    if (ownedBackend !== null) {
      try {
        await stopOwnedCanonicalBackend(ownedBackend, finalDeadline)
        ownedBackend = null
      } catch (cleanupError) {
        reported = cleanupError
      }
    }
    if (executionWorkspace !== null) {
      try {
        await requiredCleanup(
          [() => removeRequired(executionWorkspace, { recursive: true, force: true })],
          finalDeadline,
        )
        executionWorkspace = null
      } catch (cleanupError) {
        reported = cleanupError
      }
    }
    if (configSnapshot !== null) {
      try {
        await requiredCleanup(
          [() => removeConfigurationSnapshotRequired(configSnapshot.root)],
          finalDeadline,
        )
        configSnapshot = null
      } catch (cleanupError) {
        reported = cleanupError
      }
    }
    process.stderr.write(`${JSON.stringify({ ok: false, error: diagnosticFor(reported) })}\n`)
    return 1
  } finally {
    delete process.env[AUTH_ENVIRONMENT_VARIABLE]
  }
}

export const main = async (argv = process.argv.slice(2)) => runMain(argv, false)

export const runSyntheticGateForTest = async (argv) => runMain(argv, true)

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  process.exitCode = await main()
}
