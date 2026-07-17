import assert from "node:assert/strict"
import test from "node:test"

import {
  ENGINE_IDENTITY_SCHEMA_VERSION,
  LifecycleE4ClientError,
  P30_SESSION_CONTRACT_ID,
  P30_SESSION_SCHEMA_SHA256,
  createEndpointScopedE4Client,
  createLocalEndpointScopedTransport,
  replayConfigurationDigest,
} from "../dist/index.js"

const INSTANCE_ID = "i".repeat(43)
const BOOT_ID = "b".repeat(43)
const LAUNCH_ID = "l".repeat(43)
const expectedSessionContract = Object.freeze({
  contractId: P30_SESSION_CONTRACT_ID,
  schemaSha256: P30_SESSION_SCHEMA_SHA256,
})

const identityResponse = () => ({
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
    engine_artifact_sha256: `sha256:${"a".repeat(64)}`,
    served_backend_commit: "1".repeat(40),
    served_backend_dirty: false,
  },
  protocol: { protocol_version: "1.0" },
  session_contract: {
    contract_id: P30_SESSION_CONTRACT_ID,
    schema_sha256: P30_SESSION_SCHEMA_SHA256,
    compatibility: "compatible",
    sessionReplayContractDigest: replayConfigurationDigest,
  },
  session_readiness: { ready: true, reason: "ready" },
})

const jsonResponse = (value) => new Response(JSON.stringify(value), {
  status: 200,
  headers: { "content-type": "application/json" },
})

const failureOf = (operation) => {
  try {
    operation()
    assert.fail("expected endpoint configuration failure")
  } catch (error) {
    assert.ok(error instanceof LifecycleE4ClientError)
    return error.failure
  }
}

test("one attested remote transport binds lifecycle and session requests to the same TLS/auth/SPKI identity", async () => {
  const requests = []
  const attestations = []
  const responses = [jsonResponse(identityResponse()), jsonResponse({})]
  const binding = Object.freeze({
    normalizedEndpoint: "https://remote.breadboard.test/control",
    lifecycleMode: "remote",
    tls: Object.freeze({ kind: "system-trust", spkiSha256: `sha256:${"b".repeat(64)}` }),
    auth: Object.freeze({ kind: "process-secret", credentialReference: "process:BREADBOARD_API_TOKEN" }),
  })
  const transport = Object.freeze({
    binding,
    attestBinding(expected) {
      attestations.push(expected)
      return true
    },
    async request(url, init) {
      requests.push({ url: url.toString(), method: init.method })
      const response = responses.shift()
      assert.notEqual(response, undefined)
      return response
    },
  })

  const client = createEndpointScopedE4Client({ transport, expectedSessionContract })
  const lifecycle = await client.lifecycle.handshake()
  assert.equal(lifecycle.binding.endpoint, binding.normalizedEndpoint)
  assert.equal(lifecycle.binding.sessionReplayContractDigest, replayConfigurationDigest)
  await assert.rejects(() => client.session.attach({ sessionId: "session-1" }), /invalid_replay_retention/)

  assert.deepEqual(requests, [
    { url: "https://remote.breadboard.test/control/v1/engine/identity", method: "GET" },
    { url: "https://remote.breadboard.test/control/v1/sessions/session-1", method: "GET" },
  ])
  assert.equal(attestations.length, 1)
  assert.deepEqual(attestations[0], binding)
  assert.equal(Object.isFrozen(client), true)
  assert.equal(Object.isFrozen(client.binding), true)
  assert.equal(JSON.stringify(client).includes("BREADBOARD_API_TOKEN"), true)
  assert.equal(JSON.stringify(client).includes("private-value"), false)
})

test("remote mode rejects bearer-only raw fetch and unauthenticated transport declarations before I/O", () => {
  let fetchCalls = 0
  const rawFetchFailure = failureOf(() => createEndpointScopedE4Client({
    baseUrl: "https://remote.breadboard.test",
    lifecycleMode: "remote",
    bearerToken: "raw-secret-private-value",
    fetch: async () => {
      fetchCalls += 1
      return jsonResponse(identityResponse())
    },
    expectedSessionContract,
  }))
  assert.deepEqual(rawFetchFailure, { kind: "protocol", code: "endpoint_scoped_transport_required" })

  const unauthenticatedBinding = Object.freeze({
    normalizedEndpoint: "https://remote.breadboard.test",
    lifecycleMode: "remote",
    tls: Object.freeze({ kind: "system-trust" }),
    auth: Object.freeze({ kind: "absent" }),
  })
  const unauthenticatedTransport = Object.freeze({
    binding: unauthenticatedBinding,
    attestBinding: () => true,
    request: async () => {
      fetchCalls += 1
      return jsonResponse(identityResponse())
    },
  })
  const unauthenticatedFailure = failureOf(() => createEndpointScopedE4Client({
    transport: unauthenticatedTransport,
    expectedSessionContract,
  }))
  assert.deepEqual(unauthenticatedFailure, { kind: "protocol", code: "remote_auth_reference_required" })
  assert.equal(fetchCalls, 0)
})

test("local raw fetch adapter is limited to loopback and carries an immutable no-auth binding", () => {
  const transport = createLocalEndpointScopedTransport({
    baseUrl: "http://127.0.0.1:8000/api/",
    lifecycleMode: "local-external",
    fetch: async () => assert.fail("transport remains lazy"),
  })
  assert.deepEqual(transport.binding, {
    normalizedEndpoint: "http://127.0.0.1:8000/api",
    lifecycleMode: "local-external",
    tls: { kind: "local-loopback" },
    auth: { kind: "absent" },
  })
  assert.equal(Object.isFrozen(transport), true)
  assert.equal(Object.isFrozen(transport.binding), true)

  const failure = failureOf(() => createLocalEndpointScopedTransport({
    baseUrl: "https://remote.breadboard.test",
    lifecycleMode: "local-external",
  }))
  assert.deepEqual(failure, { kind: "protocol", code: "local_endpoint_not_loopback" })
})
