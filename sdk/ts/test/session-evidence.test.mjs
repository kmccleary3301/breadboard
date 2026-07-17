import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { dirname, join } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"

import {
  REDACTED_VALUE,
  SESSION_EVIDENCE_BOUNDS,
  SESSION_EVIDENCE_REDACTION_POLICY_VERSION,
  SESSION_EVIDENCE_SCHEMA_VERSION,
  createLocalCloseEvidence,
  createStableCursorEvidence,
  detectSensitiveValues,
  digestVerifiedProductJourneyEvidenceBundle,
  digestVerifiedRuntimeCaptureEvidenceBundle,
  digestSessionEvidenceBundle,
  projectCancellationEvidence,
  projectDisplayText,
  projectEventEvidence,
  projectFailureEvidence,
  projectSnapshotEvidence,
  projectSubmitEvidence,
  projectUnknownErrorEvidence,
  serializeSessionEvidenceBundle,
  serializeVerifiedProductJourneyEvidenceBundle,
  serializeVerifiedRuntimeCaptureEvidenceBundle,
  validateSessionEvidenceBundle,
  verifyRuntimeCaptureEvidenceSource,
} from "../dist/index.js"

const HERE = dirname(fileURLToPath(import.meta.url))
const fixture = async (name) => JSON.parse(await readFile(join(HERE, "fixtures", name), "utf8"))
const findingCategories = (report) => new Set(report.findings.map((finding) => finding.category))

const ids = {
  sessionId: "session-evidence-1",
  inputId: "input-evidence-1",
  turnId: "turn-evidence-1",
  eventId: "event-evidence-1",
}

const event = (kind, payload, overrides = {}) => ({
  kind,
  eventId: ids.eventId,
  sequence: 9,
  sessionId: ids.sessionId,
  inputId: ids.inputId,
  turnId: ids.turnId,
  occurredAtMs: 1784304000000,
  payload,
  ...overrides,
})

const staticProvenance = { kind: "static-fixture", sourceTicket: "bb-89n.15" }
const bundle = (records) => ({
  schemaVersion: SESSION_EVIDENCE_SCHEMA_VERSION,
  redactionPolicyVersion: SESSION_EVIDENCE_REDACTION_POLICY_VERSION,
  evidenceClass: "static-fixture",
  journeyId: "P30-SESSION-NEW-TURN-RECONNECT",
  provenance: staticProvenance,
  records,
})

test("checked-in static and bb-89n.14 runtime snapshots remain separate and schema-valid", async () => {
  const staticSnapshot = await fixture("session-evidence.static.v1.json")
  const runtimeSnapshot = await fixture("session-evidence.bb89n14.v1.json")

  validateSessionEvidenceBundle(staticSnapshot)
  validateSessionEvidenceBundle(runtimeSnapshot)

  assert.equal(staticSnapshot.evidenceClass, "static-fixture")
  assert.equal(runtimeSnapshot.evidenceClass, "runtime-capture")
  assert.equal(runtimeSnapshot.provenance.sourceTicket, "bb-89n.14")
  assert.equal(runtimeSnapshot.provenance.captureSha256, "sha256:ea7e08f731debafcd502f4195d1063fc56caab82e6cc0e5a30191017ec2c438f")
  assert.equal(JSON.stringify(runtimeSnapshot).includes("BB89N14_e5dac93"), false)
})

test("typed projectors preserve correlation and stable-cursor identities", () => {
  const snapshot = projectSnapshotEvidence({
    sessionId: ids.sessionId,
    status: "running",
    createdAt: "2026-07-17T00:00:00Z",
    lastActivityAt: "2026-07-17T00:00:01Z",
    model: "openai/gpt-5.4",
    mode: null,
    turnAdmission: "idle",
    activeTurnId: null,
    queuedTurnCount: 0,
    replayRetention: { maxEvents: 1000, maxAgeMs: 86400000, configurationDigest: "sha256:" + "1".repeat(64) },
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-evidence-0",
    headSequence: 9,
    headEventId: ids.eventId,
    retainedHistory: "complete",
    sessionReplayContractDigest: "sha256:" + "2".repeat(64),
    terminalTurns: [{ inputId: ids.inputId, turnId: ids.turnId, outcome: "completed", originalDisposition: "started" }],
  })
  const submit = projectSubmitEvidence({
    clientMessageId: "message-evidence-1",
    inputId: ids.inputId,
    turnId: ids.turnId,
    disposition: "started",
    originalDisposition: "started",
  })
  const cancellation = projectCancellationEvidence({
    cancellationRequestId: "cancel-evidence-1",
    cancellationRequestKey: "cancel-key-evidence-1",
    inputId: ids.inputId,
    turnId: ids.turnId,
    disposition: "cancellation_requested",
    originalDisposition: "cancellation_requested",
  })
  const cursor = createStableCursorEvidence(ids.sessionId, ids.eventId, 9, { gapObserved: false, duplicateApplied: false })

  assert.deepEqual([snapshot.sessionId, submit.inputId, cancellation.turnId, cursor.eventId], [ids.sessionId, ids.inputId, ids.turnId, ids.eventId])
  validateSessionEvidenceBundle(bundle([snapshot, submit, cancellation, cursor]))
})

test("display projection redacts every prohibited free-text category", () => {
  assert.deepEqual(projectDisplayText("user-text", "ordinary prompt"), {
    kind: "user-text",
    text: "ordinary prompt",
    redacted: false,
    categories: [],
  })

  for (const [text, expected] of [
    ["authorization is Bearer canary-token-never-serialize", "credential"],
    ["header Authorization: Basic canary-never-serialize", "header"],
    ["account id=account-canary-never-serialize", "account-id"],
    ["account alias=private-canary", "alias"],
    ["open https://user:secret@example.test/private", "url"],
    ["stored at /Users/canary/private.txt", "path"],
    ["read /root/canary/private.txt", "path"],
    ["connect api.example.test/v1", "url"],
    ["secret at /data/private/key", "path"],
    ["secret at /System/Library/key", "path"],
    ["secret at /Applications/private", "path"],
    ["secret at /bin", "path"],
    ["response body: private-canary", "body"],
    ["event payload: private-canary", "event-payload"],
    ["TypeError: private-canary", "error-serialization"],
    ["BB89N14_" + "a".repeat(64), "credential"],
  ]) {
    const projected = projectDisplayText("assistant-text", text)
    assert.equal(projected.text, REDACTED_VALUE)
    assert.equal(projected.redacted, true)
    assert.equal(projected.categories.includes(expected), true)
    assert.equal(JSON.stringify(projected).includes(text), false)
  }
})

test("detector reports only bounded locations and categories for adversarial values", () => {
  const canaries = {
    token: "sk-canary-never-serialize-123456",
    header: "Bearer header-canary-never-serialize",
    url: "https://user:secret@example.test/private?token=canary",
    path: "/Users/canary/private.txt",
    alias: "private-account-alias",
    body: "{malformed-canary",
    payload: "event-payload-canary",
    accountId: "account-canary-never-serialize",
  }
  const cyclic = { nested: [{ api_token: canaries.token }] }
  cyclic.self = cyclic
  const input = {
    authorization: canaries.header,
    endpoint: canaries.url,
    workspace: canaries.path,
    account_alias: canaries.alias,
    "ChatGPT-Account-Id": canaries.accountId,
    body: canaries.body,
    payload: { text: canaries.payload },
    nested: cyclic,
    error: new Error("error-canary-never-serialize"),
  }

  const report = detectSensitiveValues(input)
  const categories = findingCategories(report)
  for (const category of ["header", "url", "path", "alias", "account-id", "body", "malformed-body", "event-payload", "credential", "cycle", "error-serialization"]) {
    assert.equal(categories.has(category), true, `missing ${category}`)
  }
  assert.equal(report.policyVersion, SESSION_EVIDENCE_REDACTION_POLICY_VERSION)
  assert.equal(report.findings.length <= SESSION_EVIDENCE_BOUNDS.maxFindings, true)
  assert.equal(report.findings.every((finding) => Object.keys(finding).join(",") === "location,category"), true)
  const serialized = JSON.stringify(report)
  for (const canary of Object.values(canaries)) assert.equal(serialized.includes(canary), false)
  assert.equal(serialized.includes("error-canary-never-serialize"), false)
})

test("detector terminates at depth, collection, node, finding, and string bounds", () => {
  let deep = "leaf"
  for (let index = 0; index < SESSION_EVIDENCE_BOUNDS.maxDepth + 3; index += 1) deep = { nested: deep }
  const wide = Array.from({ length: SESSION_EVIDENCE_BOUNDS.maxCollectionEntries + 3 }, (_, index) => `value-${index}`)
  const oversized = "x".repeat(SESSION_EVIDENCE_BOUNDS.maxStringBytes + 1)
  let shared = "leaf"
  for (let index = 0; index < SESSION_EVIDENCE_BOUNDS.maxDepth; index += 1) shared = Array(8).fill(shared)
  const report = detectSensitiveValues({ deep, wide, oversized, zzShared: shared })
  const categories = findingCategories(report)
  assert.equal(categories.has("depth-limit"), true)
  assert.equal(categories.has("entry-limit"), true)
  assert.equal(categories.has("string-limit"), true)
  assert.equal(report.inspectedNodes, SESSION_EVIDENCE_BOUNDS.maxInspectedNodes)
  assert.equal(report.truncated, true)
  const guarded = Array(SESSION_EVIDENCE_BOUNDS.maxCollectionEntries + 2).fill("safe")
  Object.defineProperty(guarded, SESSION_EVIDENCE_BOUNDS.maxCollectionEntries + 1, {
    enumerable: true,
    get: () => { throw new Error("detector read beyond its collection bound") },
  })
  const guardedReport = detectSensitiveValues(guarded)
  assert.equal(findingCategories(guardedReport).has("entry-limit"), true)
})

test("event, failure, and close projections never serialize raw payloads or errors", () => {
  const secret = "sk-event-canary-never-serialize"
  const user = projectEventEvidence(event("input_observed", { text: secret }))
  const assistant = projectEventEvidence(event("assistant_text_completed", { text: secret }, { eventId: "event-evidence-2", sequence: 10 }))
  const cancellation = projectEventEvidence(event("turn_cancelled", { reason: "user_requested" }, { eventId: "event-evidence-3", sequence: 11 }))
  const generic = projectEventEvidence(event("tool_result_observed", { authorization: secret }, { eventId: "event-evidence-4", sequence: 12 }))
  const protocol = projectFailureEvidence({ kind: "protocol", code: "malformed_frame", eventId: ids.eventId, sequence: 9 })
  const gap = projectFailureEvidence({ kind: "resume-gap", code: "resume_window_exceeded", lastAppliedEventId: ids.eventId, lastAppliedSequence: 9 })
  const unknown = projectUnknownErrorEvidence(new Error(secret))
  const secretCode = projectFailureEvidence({ kind: "protocol", code: secret, eventId: ids.eventId, sequence: 9 })
  const close = createLocalCloseEvidence(ids.sessionId)

  const evidence = bundle([user, assistant, cancellation, generic, protocol, gap, secretCode, unknown, close])
  validateSessionEvidenceBundle(evidence)
  const serialized = new TextDecoder().decode(serializeSessionEvidenceBundle(evidence))
  assert.equal(serialized.includes(secret), false)
  assert.equal(serialized.includes("authorization"), false)
  assert.equal(user.display.text, REDACTED_VALUE)
  assert.equal(assistant.display.text, REDACTED_VALUE)
  assert.deepEqual(cancellation.display, { kind: "cancellation-status", reason: "user_requested" })
  assert.equal(generic.display, null)
  assert.deepEqual(protocol.display, { kind: "protocol-error", code: "malformed_frame" })
  assert.deepEqual(gap.display, { kind: "gap-error", code: "resume_window_exceeded" })
  assert.deepEqual(secretCode.display, { kind: "protocol-error", code: "redacted_error_code" })
  assert.deepEqual(unknown.details, { kind: "unknown-error" })
  assert.equal(close.backendSessionDeletion, "not-requested")
})

test("runtime validator rejects raw or schema-shaped escape fields", async () => {
  const safe = bundle([createLocalCloseEvidence(ids.sessionId)])
  for (const [field, value] of [
    ["body", "raw-body-canary"],
    ["payload", { token: "payload-canary" }],
    ["headers", { authorization: "header-canary" }],
    ["url", "https://secret.example"],
    ["path", "/private/path"],
    ["alias", "private-alias"],
    ["readiness", "ready"],
    ["conformancePayload", { claim: true }],
    ["sourceHash", "sha256:" + "f".repeat(64)],
  ]) {
    const unsafe = structuredClone(safe)
    unsafe.records[0][field] = value
    assert.throws(() => validateSessionEvidenceBundle(unsafe), /unapproved field/)
    await assert.rejects(() => digestSessionEvidenceBundle(unsafe), /unapproved field/)
  }

  const unsafeText = structuredClone(bundle([projectEventEvidence(event("input_observed", { text: "ordinary prompt" }))]))
  unsafeText.records[0].display.text = "sk-injected-canary-never-hash"
  assert.throws(() => validateSessionEvidenceBundle(unsafeText), /unredacted display text is not safe/)
  await assert.rejects(() => digestSessionEvidenceBundle(unsafeText), /unredacted display text is not safe/)

  const inventedEvent = bundle([projectEventEvidence(event("turn_completed", {}))])
  inventedEvent.records[0].eventKind = "invented_event_kind"
  assert.throws(() => validateSessionEvidenceBundle(inventedEvent), /event.eventKind is not an approved value/)
})

test("runtime validator enforces failure identities and record-display coupling", () => {
  const cancellationFailure = bundle([projectFailureEvidence({
    kind: "cancellation-conflict",
    sessionId: ids.sessionId,
    turnId: ids.turnId,
    code: null,
  })])
  for (const field of ["sessionId", "turnId"]) {
    const invalid = structuredClone(cancellationFailure)
    invalid.records[0].details[field] = null
    assert.throws(() => validateSessionEvidenceBundle(invalid), new RegExp(`failure\\.${field} is not an approved string`))
  }

  const invalidHttp = bundle([projectFailureEvidence({ kind: "http", status: 503, code: null })])
  invalidHttp.records[0].details.status = 99
  assert.throws(() => validateSessionEvidenceBundle(invalidHttp), /must be an HTTP status/)

  const protocolFailure = bundle([projectFailureEvidence({
    kind: "protocol",
    code: "malformed_frame",
    eventId: ids.eventId,
    sequence: 9,
  })])
  protocolFailure.records[0].display = { kind: "gap-error", code: "malformed_frame" }
  assert.throws(() => validateSessionEvidenceBundle(protocolFailure), /protocol failure display is inconsistent/)

  const wrongEventDisplay = bundle([projectEventEvidence(event("input_observed", { text: "ordinary prompt" }))])
  wrongEventDisplay.records[0].display = { kind: "assistant-text", text: "ordinary prompt", redacted: false, categories: [] }
  assert.throws(() => validateSessionEvidenceBundle(wrongEventDisplay), /input event requires user-text display/)

  const unexpectedEventDisplay = bundle([projectEventEvidence(event("turn_completed", {}))])
  unexpectedEventDisplay.records[0].display = { kind: "cancellation-status", reason: "user_requested" }
  assert.throws(() => validateSessionEvidenceBundle(unexpectedEventDisplay), /event kind does not admit display/)

  const nullTurnCorrelation = bundle([projectEventEvidence(event("turn_completed", {}))])
  nullTurnCorrelation.records[0].turnId = null
  assert.throws(() => validateSessionEvidenceBundle(nullTurnCorrelation), /event.turnId is not an approved string/)

  const missingDeltaDisplay = bundle([projectEventEvidence(event("assistant_text_delta", { text: "ordinary prompt" }))])
  missingDeltaDisplay.records[0].display = null
  assert.throws(() => validateSessionEvidenceBundle(missingDeltaDisplay), /assistant delta requires assistant-text display/)

  assert.throws(() => projectSubmitEvidence({
    clientMessageId: "sk-canary-identity-never-serialize",
    inputId: ids.inputId,
    turnId: ids.turnId,
    disposition: "started",
    originalDisposition: "started",
  }), /submit.clientMessageId contains a sensitive value/)

  const sensitiveIdentity = bundle([createLocalCloseEvidence(ids.sessionId)])
  sensitiveIdentity.records[0].sessionId = "sk-canary-identity-never-serialize"
  assert.throws(() => validateSessionEvidenceBundle(sensitiveIdentity), /close.sessionId contains a sensitive value/)

  const wrongCloseDisplay = bundle([createLocalCloseEvidence(ids.sessionId)])
  wrongCloseDisplay.records[0].display = { kind: "cancellation-status", reason: "user_requested" }
  assert.throws(() => validateSessionEvidenceBundle(wrongCloseDisplay), /local close display is inconsistent/)
})

test("runtime journeys require complete correlated transitions and source binding", async () => {
  const runtimeSnapshot = await fixture("session-evidence.bb89n14.v1.json")

  const empty = structuredClone(runtimeSnapshot)
  empty.records = []
  assert.throws(() => validateSessionEvidenceBundle(empty), /evidence records must not be empty/)

  const incomplete = structuredClone(runtimeSnapshot)
  incomplete.records = incomplete.records.filter((record) => record.eventKind !== "turn_completed")
  assert.throws(() => validateSessionEvidenceBundle(incomplete), /missing a required transition/)

  const mismatched = structuredClone(runtimeSnapshot)
  mismatched.records.find((record) => record.eventKind === "assistant_text_completed").turnId = "turn-other"
  assert.throws(() => validateSessionEvidenceBundle(mismatched), /correlation differs from submit/)

  const sourceBytes = new TextEncoder().encode("accepted runtime capture source")
  const bound = structuredClone(runtimeSnapshot)
  bound.provenance.captureSha256 = `sha256:${createHash("sha256").update(sourceBytes).digest("hex")}`
  await verifyRuntimeCaptureEvidenceSource(bound, sourceBytes)
  assert.throws(() => serializeSessionEvidenceBundle(bound), /requires verified provenance serialization/)
  assert.match(await digestVerifiedRuntimeCaptureEvidenceBundle(bound, sourceBytes), /^sha256:[a-f0-9]{64}$/)
  assert.equal((await serializeVerifiedRuntimeCaptureEvidenceBundle(bound, sourceBytes)).byteLength > 0, true)
  await assert.rejects(
    () => verifyRuntimeCaptureEvidenceSource(bound, new TextEncoder().encode("different source")),
    /runtime capture source digest differs/,
  )
})

test("product journeys cannot serialize until trusted repository and configuration provenance match", async () => {
  const runtimeSnapshot = await fixture("session-evidence.bb89n14.v1.json")
  const configurationBytes = new TextEncoder().encode("trusted product configuration")
  const productJourney = structuredClone(runtimeSnapshot)
  productJourney.evidenceClass = "product-journey"
  productJourney.provenance = {
    kind: "product-journey",
    sourceTicket: "bb-89n.16",
    candidateCommit: "1".repeat(40),
    candidateTree: "2".repeat(40),
    backendCommit: "3".repeat(40),
    clientCommit: "4".repeat(40),
    configurationSha256: `sha256:${createHash("sha256").update(configurationBytes).digest("hex")}`,
  }
  const trusted = {
    candidateCommit: productJourney.provenance.candidateCommit,
    candidateTree: productJourney.provenance.candidateTree,
    backendCommit: productJourney.provenance.backendCommit,
    clientCommit: productJourney.provenance.clientCommit,
    configurationBytes,
  }

  validateSessionEvidenceBundle(productJourney)
  assert.throws(() => serializeSessionEvidenceBundle(productJourney), /requires verified provenance serialization/)
  const bytes = await serializeVerifiedProductJourneyEvidenceBundle(productJourney, trusted)
  assert.equal(new TextDecoder().decode(bytes).includes(productJourney.provenance.candidateCommit), true)
  assert.match(await digestVerifiedProductJourneyEvidenceBundle(productJourney, trusted), /^sha256:[a-f0-9]{64}$/)
  await assert.rejects(
    () => serializeVerifiedProductJourneyEvidenceBundle(productJourney, { ...trusted, candidateCommit: "5".repeat(40) }),
    /repository provenance differs/,
  )
  await assert.rejects(
    () => serializeVerifiedProductJourneyEvidenceBundle(productJourney, {
      ...trusted,
      configurationBytes: new TextEncoder().encode("different configuration"),
    }),
    /configuration digest differs/,
  )
})

test("serialization snapshots stateful inputs once before validation and hashing", () => {
  const stateful = structuredClone(bundle([projectEventEvidence(event("input_observed", { text: "ordinary prompt" }))]))
  let reads = 0
  Object.defineProperty(stateful.records[0].display, "text", {
    enumerable: true,
    get: () => {
      reads += 1
      return reads === 1 ? "ordinary prompt" : "sk-stateful-getter-never-serialize"
    },
  })

  const serialized = new TextDecoder().decode(serializeSessionEvidenceBundle(stateful))
  assert.equal(reads, 1)
  assert.equal(serialized.includes("ordinary prompt"), true)
  assert.equal(serialized.includes("sk-stateful-getter-never-serialize"), false)
})

test("canonical evidence hashing is stable and hashes only validated redacted values", async () => {
  const runtimeSnapshot = await fixture("session-evidence.bb89n14.v1.json")
  const sourceBytes = new TextEncoder().encode("canonical runtime capture source")
  runtimeSnapshot.provenance.captureSha256 = `sha256:${createHash("sha256").update(sourceBytes).digest("hex")}`
  const first = await digestVerifiedRuntimeCaptureEvidenceBundle(runtimeSnapshot, sourceBytes)
  const second = await digestVerifiedRuntimeCaptureEvidenceBundle(structuredClone(runtimeSnapshot), sourceBytes)
  assert.equal(first, second)
  assert.match(first, /^sha256:[a-f0-9]{64}$/)
  const serialized = await serializeVerifiedRuntimeCaptureEvidenceBundle(runtimeSnapshot, sourceBytes)
  assert.equal(new TextDecoder().decode(serialized).includes("BB89N14_e5dac93"), false)
})
