import assert from "node:assert/strict"
import test from "node:test"

import {
  ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
  ENGINE_IDENTITY_SCHEMA_VERSION,
  ENGINE_OWNER_SCHEMA_VERSION,
  P30_SESSION_CONTRACT_ID,
  P30_SESSION_SCHEMA_SHA256,
  createLifecycleE4Client,
  replayConfigurationDigest,
} from "../dist/index.js"

const INSTANCE_ID = "i".repeat(43)
const BOOT_ID = "b".repeat(43)
const LAUNCH_ID = "l".repeat(43)
const CHALLENGE_ID = "c".repeat(43)
const CHALLENGE = "h".repeat(43)
const AUTHORIZATION_ID = "a".repeat(43)
const CONTROL_REQUEST_ID = "q".repeat(43)
const OWNER_CREDENTIAL = "owner-credential-private-value-01"
const BOOTSTRAP_SECRET = new TextEncoder().encode("bootstrap-credential-private-value")
const expectedSessionContract = Object.freeze({
  contractId: P30_SESSION_CONTRACT_ID,
  schemaSha256: P30_SESSION_SCHEMA_SHA256,
})

const identity = {
  schema_version: ENGINE_IDENTITY_SCHEMA_VERSION,
  liveness: { status: "live" },
  process: {
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    started_at: "2026-07-17T00:00:00Z",
    started_at_unix: 1_768_521_600,
    pid: 4321,
    os_process_start_token: "darwin:1768521600:123456",
  },
  launch: { launch_id: LAUNCH_ID, source: "supervisor" },
  artifact_revision: {
    engine_artifact_sha256: `sha256:${"e".repeat(64)}`,
    served_backend_commit: null,
    served_backend_dirty: null,
  },
  protocol: { protocol_version: "1.0" },
  session_contract: {
    contract_id: P30_SESSION_CONTRACT_ID,
    schema_sha256: P30_SESSION_SCHEMA_SHA256,
    compatibility: "compatible",
    sessionReplayContractDigest: replayConfigurationDigest,
  },
  session_readiness: { ready: true, reason: "ready" },
}

const response = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json", ...headers },
})

const bindingBody = {
  engine_instance_id: INSTANCE_ID,
  engine_boot_id: BOOT_ID,
  launch_id: LAUNCH_ID,
}

const clientWith = async (entries, requests) => {
  const client = createLifecycleE4Client({
    baseUrl: "https://breadboard.test/control/",
    expectedSessionContract,
    fetch: async (input, init = {}) => {
      requests.push({
        url: String(input),
        method: init.method,
        body: init.body,
        headers: new Headers(init.headers),
      })
      const entry = entries.shift()
      assert.notEqual(entry, undefined)
      if (typeof entry === "function") return entry(input, init)
      return response(entry)
    },
  })
  return client.handshake()
}

const rejectionOf = async (operation) => {
  try {
    await operation()
    assert.fail("expected operation to reject")
  } catch (error) {
    return error
  }
}

test("generation-zero acquisition uses a mutable challenge-bound proof without transmitting bootstrap bytes", async () => {
  const requests = []
  const bound = await clientWith([
    identity,
    {
      schema_version: "bb.engine_bootstrap_challenge.v1",
      ...bindingBody,
      challenge_id: CHALLENGE_ID,
      challenge: CHALLENGE,
      expires_at_unix: 1_768_521_605,
    },
    {
      schema_version: ENGINE_OWNER_SCHEMA_VERSION,
      result: "acquired",
      ...bindingBody,
      owner_generation: 1,
      expires_at_unix: 1_768_521_630,
      lease_ttl_seconds: 30,
      renewal_interval_seconds: 10,
    },
  ], requests)

  const owner = Uint8Array.from(BOOTSTRAP_SECRET)
  const acquired = await bound.acquireOwner({
    expectedOwnerGeneration: 0,
    ownerCredential: OWNER_CREDENTIAL,
    bootstrapCredential: owner,
  })
  assert.equal(owner.every((byte) => byte === 0), true)

  assert.equal(acquired.result, "acquired")
  assert.deepEqual(requests.slice(1).map(({ url }) => url), [
    "https://breadboard.test/control/v1/engine/owner/bootstrap-challenge",
    "https://breadboard.test/control/v1/engine/owner/acquire",
  ])
  assert.deepEqual(JSON.parse(requests[1].body), bindingBody)
  const acquireBody = JSON.parse(requests[2].body)
  assert.equal(acquireBody.expected_owner_generation, 0)
  assert.equal(acquireBody.bootstrap_challenge_id, CHALLENGE_ID)
  assert.match(acquireBody.bootstrap_proof_sha256, /^sha256:[0-9a-f]{64}$/)
  assert.equal(requests[2].headers.get("x-breadboard-bootstrap-credential"), null)
  assert.equal(requests[2].headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
  assert.equal(JSON.stringify(requests).includes(new TextDecoder().decode(BOOTSTRAP_SECRET)), false)
})

test("generation-zero acquisition wipes caller bootstrap bytes on pre-abort", async () => {
  const requests = []
  const bound = await clientWith([identity], requests)
  const bootstrapBytes = Uint8Array.from(BOOTSTRAP_SECRET)
  const controller = new AbortController()
  controller.abort()
  const error = await rejectionOf(() => bound.acquireOwner({
    expectedOwnerGeneration: 0,
    ownerCredential: OWNER_CREDENTIAL,
    bootstrapCredential: bootstrapBytes,
    signal: controller.signal,
  }))
  assert.equal(error.failure.kind, "caller-abort")
  assert.equal(bootstrapBytes.every((byte) => byte === 0), true)
  assert.equal(requests.length, 1)
})

test("owner generation validation wipes caller bootstrap bytes before any I/O", async () => {
  const requests = []
  const bound = await clientWith([identity], requests)
  const bootstrapBytes = Uint8Array.from(BOOTSTRAP_SECRET)
  const error = await rejectionOf(() => bound.acquireOwner({
    expectedOwnerGeneration: -1,
    ownerCredential: OWNER_CREDENTIAL,
    bootstrapCredential: bootstrapBytes,
  }))
  assert.equal(error.failure.kind, "protocol")
  assert.equal(error.failure.code, "expected_owner_generation_invalid")
  assert.equal(bootstrapBytes.every((byte) => byte === 0), true)
  assert.equal(requests.length, 1)
})


test("request capability values cannot reflect through either correlation header", async () => {
  const bootstrapText = new TextDecoder().decode(BOOTSTRAP_SECRET)
  const bootstrapCapabilities = [
    ["bootstrap_challenge_id", (body) => body.bootstrap_challenge_id],
    ["challenge", () => CHALLENGE],
    ["bootstrap_proof_sha256", (body) => body.bootstrap_proof_sha256],
    ["owner_credential", () => OWNER_CREDENTIAL],
    ["bootstrap_credential", () => bootstrapText],
  ]
  for (const [capability, selectValue] of bootstrapCapabilities) {
    for (const header of ["x-request-id", "x-correlation-id"]) {
      const requests = []
      const bootstrapBytes = Uint8Array.from(BOOTSTRAP_SECRET)
      const bound = await clientWith([
        identity,
        {
          schema_version: "bb.engine_bootstrap_challenge.v1",
          ...bindingBody,
          challenge_id: CHALLENGE_ID,
          challenge: CHALLENGE,
          expires_at_unix: 1_768_521_605,
        },
        (_input, init) => {
          const reflectedValue = selectValue(JSON.parse(init.body))
          return response({
            error: "owner_conflict",
            detail: reflectedValue,
            path: null,
          }, 409, { [header]: reflectedValue })
        },
      ], requests)
      const failure = await rejectionOf(() => bound.acquireOwner({
        expectedOwnerGeneration: 0,
        ownerCredential: OWNER_CREDENTIAL,
        bootstrapCredential: bootstrapBytes,
      }))
      const acquireBody = JSON.parse(requests[2].body)
      const reflectedValue = selectValue(acquireBody)
      assert.equal(failure.failure.kind, "owner-conflict", `${capability}:${header}`)
      assert.deepEqual(failure.failure.correlation, {}, `${capability}:${header}`)
      assert.equal(JSON.stringify(failure).includes(reflectedValue), false, `${capability}:${header}`)
      assert.equal(bootstrapBytes.every((byte) => byte === 0), true, `${capability}:${header}`)
    }
  }

  for (const header of ["x-request-id", "x-correlation-id"]) {
    const authorizationBound = await clientWith([
      identity,
      (_input, _init) => response({
        error: "hard_signal_authorization_conflict",
        detail: AUTHORIZATION_ID,
        path: null,
      }, 409, { [header]: `prefix-${AUTHORIZATION_ID}` }),
    ], [])
    const failure = await rejectionOf(() => authorizationBound.recordHardSignalOutcome({
      ownerCredential: OWNER_CREDENTIAL,
      ownerGeneration: 1,
      drainGeneration: 2,
      authorizationId: AUTHORIZATION_ID,
      outcome: "sent",
    }))
    assert.equal(failure.failure.kind, "hard-signal-conflict", header)
    assert.deepEqual(failure.failure.correlation, {}, header)
    assert.equal(JSON.stringify(failure).includes(AUTHORIZATION_ID), false, header)
  }
})

test("temporary byte candidates used for correlation scrubbing are wiped", async () => {
  const bootstrapText = new TextDecoder().decode(BOOTSTRAP_SECRET)
  const OriginalTextEncoder = globalThis.TextEncoder
  const encodedCandidates = []
  globalThis.TextEncoder = class extends OriginalTextEncoder {
    encode(input) {
      const bytes = super.encode(input)
      if (String(input).includes(bootstrapText)) encodedCandidates.push(bytes)
      return bytes
    }
  }
  const bootstrapBytes = Uint8Array.from(BOOTSTRAP_SECRET)
  try {
    const bound = await clientWith([
      identity,
      {
        schema_version: "bb.engine_bootstrap_challenge.v1",
        ...bindingBody,
        challenge_id: CHALLENGE_ID,
        challenge: CHALLENGE,
        expires_at_unix: 1_768_521_605,
      },
      (_input, _init) => response({
        error: "owner_conflict",
        detail: bootstrapText,
        path: null,
      }, 409, {
        "x-request-id": bootstrapText,
        "x-correlation-id": `prefix-${bootstrapText}`,
      }),
    ], [])
    await rejectionOf(() => bound.acquireOwner({
      expectedOwnerGeneration: 0,
      ownerCredential: OWNER_CREDENTIAL,
      bootstrapCredential: bootstrapBytes,
    }))
  } finally {
    globalThis.TextEncoder = OriginalTextEncoder
  }
  assert.equal(encodedCandidates.length, 2)
  assert.equal(encodedCandidates.every((bytes) => bytes.every((byte) => byte === 0)), true)
  assert.equal(bootstrapBytes.every((byte) => byte === 0), true)
})

test("response-generated challenge and authorization tokens cannot survive malformed success evidence", async () => {
  const malformedSecrets = [
    "short-response-secret",
    "x".repeat(64),
    "privaté-response-secret",
  ]
  for (const field of ["challenge_id", "challenge"]) {
    for (const secret of malformedSecrets) {
      for (const header of ["x-request-id", "x-correlation-id"]) {
        const bootstrapBytes = Uint8Array.from(BOOTSTRAP_SECRET)
        const challengeBound = await clientWith([
          identity,
          (_input, _init) => response({
            schema_version: "bb.engine_bootstrap_challenge.v1",
            ...bindingBody,
            challenge_id: field === "challenge_id" ? secret : CHALLENGE_ID,
            challenge: field === "challenge" ? secret : CHALLENGE,
            expires_at_unix: 1_768_521_605,
          }, 200, { [header]: secret }),
        ], [])
        const failure = await rejectionOf(() => challengeBound.acquireOwner({
          expectedOwnerGeneration: 0,
          ownerCredential: OWNER_CREDENTIAL,
          bootstrapCredential: bootstrapBytes,
        }))
        assert.equal(failure.failure.kind, "protocol", `${field}:${header}`)
        assert.deepEqual(failure.failure.correlation, {}, `${field}:${header}`)
        assert.equal(JSON.stringify(failure).includes(secret), false, `${field}:${header}`)
        assert.equal(bootstrapBytes.every((byte) => byte === 0), true, `${field}:${header}`)
      }
    }
  }

  for (const secret of malformedSecrets) {
    for (const header of ["x-request-id", "x-correlation-id"]) {
      const authorizationBound = await clientWith([
        identity,
        (_input, _init) => response({
          schema_version: "bb.engine_hard_signal_preparation.v1",
          result: "prepared",
          ...bindingBody,
          owner_generation: 1,
          drain_generation: 2,
          authorization_id: secret,
          expires_at_unix: 1_768_521_605,
          signal_permitted: false,
        }, 200, { [header]: secret }),
      ], [])
      const failure = await rejectionOf(() => authorizationBound.prepareHardSignal({
        ownerCredential: OWNER_CREDENTIAL,
        ownerGeneration: 1,
        drainGeneration: 2,
        pid: 4321,
        osProcessStartToken: "darwin:1768521600:123456",
      }))
      assert.equal(failure.failure.kind, "protocol", header)
      assert.deepEqual(failure.failure.correlation, {}, header)
      assert.equal(JSON.stringify(failure).includes(secret), false, header)
    }
  }

  const mismatchedAuthorizationBound = await clientWith([
    identity,
    (_input, _init) => response({
      schema_version: "bb.engine_hard_signal_preparation.v1",
      result: "prepared",
      ...bindingBody,
      owner_generation: 2,
      drain_generation: 2,
      authorization_id: AUTHORIZATION_ID,
      expires_at_unix: 1_768_521_605,
      signal_permitted: false,
    }, 200, { "x-correlation-id": `prefix-${AUTHORIZATION_ID}` }),
  ], [])
  const mismatchedAuthorization = await rejectionOf(() => mismatchedAuthorizationBound.prepareHardSignal({
    ownerCredential: OWNER_CREDENTIAL,
    ownerGeneration: 1,
    drainGeneration: 2,
    pid: 4321,
    osProcessStartToken: "darwin:1768521600:123456",
  }))
  assert.equal(mismatchedAuthorization.failure.code, "hard_signal_authorization_binding_mismatch")
  assert.deepEqual(mismatchedAuthorization.failure.correlation, {})
  assert.equal(JSON.stringify(mismatchedAuthorization).includes(AUTHORIZATION_ID), false)
})

test("hard-signal control separates preparation, attempt commit, and post-attempt outcome", async () => {
  const requests = []
  const bound = await clientWith([
    identity,
    {
      schema_version: "bb.engine_hard_signal_preparation.v1",
      result: "prepared",
      ...bindingBody,
      owner_generation: 1,
      drain_generation: 2,
      authorization_id: AUTHORIZATION_ID,
      expires_at_unix: 1_768_521_605,
      signal_permitted: false,
    },
    {
      schema_version: "bb.engine_hard_signal_permit.v1",
      result: "signal_permitted",
      ...bindingBody,
      owner_generation: 1,
      drain_generation: 2,
      authorization_id: AUTHORIZATION_ID,
      expires_at_unix: 1_768_521_605,
      signal_permitted: true,
    },
    {
      schema_version: ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
      result: "signal_sent",
      ...bindingBody,
      control_request_id: CONTROL_REQUEST_ID,
      drain_generation: 2,
      admission_epoch: 5,
      session_admission_open: false,
      turn_admission_open: false,
      registrations_open: false,
      signal_permitted: false,
    },
  ], requests)

  const substituted = await rejectionOf(() => bound.prepareHardSignal({
    ownerCredential: OWNER_CREDENTIAL,
    ownerGeneration: 1,
    drainGeneration: 2,
    pid: 9999,
    osProcessStartToken: "darwin:substituted",
  }))
  assert.equal(substituted.failure.kind, "protocol")
  assert.equal(substituted.failure.code, "process_identity_mismatch")
  assert.equal(requests.length, 1)

  const authorization = await bound.prepareHardSignal({
    ownerCredential: OWNER_CREDENTIAL,
    ownerGeneration: 1,
    drainGeneration: 2,
    pid: 4321,
    osProcessStartToken: "darwin:1768521600:123456",
  })
  assert.equal(authorization.authorizationId, AUTHORIZATION_ID)
  const permit = await bound.commitHardSignal({
    ownerCredential: OWNER_CREDENTIAL,
    ownerGeneration: 1,
    drainGeneration: 2,
    pid: 4321,
    osProcessStartToken: "darwin:1768521600:123456",
    authorizationId: authorization.authorizationId,
  })
  assert.equal(permit.signalPermitted, true)
  const outcome = await bound.recordHardSignalOutcome({
    ownerCredential: OWNER_CREDENTIAL,
    ownerGeneration: 1,
    drainGeneration: 2,
    authorizationId: authorization.authorizationId,
    outcome: "sent",
  })
  assert.equal(outcome.result, "signal_sent")
  assert.deepEqual(requests.slice(1).map(({ url }) => url), [
    "https://breadboard.test/control/v1/engine/control/hard-signal/prepare",
    "https://breadboard.test/control/v1/engine/control/hard-signal/commit",
    "https://breadboard.test/control/v1/engine/control/hard-signal/outcome",
  ])
  assert.deepEqual(JSON.parse(requests[1].body), {
    ...bindingBody,
    owner_generation: 1,
    drain_generation: 2,
    pid: 4321,
    os_process_start_token: "darwin:1768521600:123456",
  })
  assert.deepEqual(JSON.parse(requests[2].body), {
    ...bindingBody,
    owner_generation: 1,
    drain_generation: 2,
    pid: 4321,
    os_process_start_token: "darwin:1768521600:123456",
    authorization_id: AUTHORIZATION_ID,
  })
  assert.deepEqual(JSON.parse(requests[3].body), {
    ...bindingBody,
    owner_generation: 1,
    drain_generation: 2,
    authorization_id: AUTHORIZATION_ID,
    outcome: "sent",
  })
  assert.equal(requests.some(({ url }) => url.includes("signal-decision")), false)
})
