import assert from "node:assert/strict"
import test from "node:test"

import * as sdk from "../dist/index.js"
import * as lifecycle from "../dist/lifecycle-client.js"
import * as sessionRuntime from "../dist/session-runtime.js"

const {
  ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION,
  ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
  ENGINE_IDENTITY_SCHEMA_VERSION,
  ENGINE_OWNER_SCHEMA_VERSION,
  LifecycleE4ClientError,
  P30_SESSION_CONTRACT_ID,
  P30_SESSION_SCHEMA_SHA256,
  createLifecycleE4Client,
} = lifecycle

const INSTANCE_ID = "i".repeat(43)
const BOOT_ID = "b".repeat(43)
const LAUNCH_ID = "l".repeat(43)
const OTHER_ID = "x".repeat(43)
const REGISTRATION_ID = "r".repeat(43)
const CLIENT_INSTANCE_ID = "client.instance-0001"
const WORKSPACE_ID = `workspace:v1:sha256:${"c".repeat(64)}`
const OWNER_CREDENTIAL = "owner-credential-private-value-01"
const BOOTSTRAP_CREDENTIAL = "bootstrap-credential-private-value"
const REGISTRATION_CREDENTIAL = "registration-credential-private-value"
const BEARER_TOKEN = "bearer-private-value"
const REDACTED_VALUE = "[redacted]"

const expectedSessionContract = Object.freeze({
  contractId: P30_SESSION_CONTRACT_ID,
  schemaSha256: P30_SESSION_SCHEMA_SHA256,
})

const identityResponse = (overrides = {}) => ({
  schema_version: ENGINE_IDENTITY_SCHEMA_VERSION,
  liveness: { status: "live" },
  process: {
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    started_at: "2026-07-17T00:00:00Z",
    started_at_unix: 1_768_521_600,
    pid: 4321,
  },
  launch: { launch_id: LAUNCH_ID, source: "supervisor" },
  artifact_revision: {
    engine_artifact_sha256: `sha256:${"a".repeat(64)}`,
    served_backend_commit: null,
    served_backend_dirty: null,
  },
  protocol: { protocol_version: "1.0" },
  session_contract: {
    contract_id: P30_SESSION_CONTRACT_ID,
    schema_sha256: P30_SESSION_SCHEMA_SHA256,
    compatibility: "compatible",
  },
  session_readiness: { ready: true, reason: "ready" },
  ...overrides,
})

const ownerResponse = (result, overrides = {}) => ({
  schema_version: ENGINE_OWNER_SCHEMA_VERSION,
  result,
  engine_instance_id: INSTANCE_ID,
  engine_boot_id: BOOT_ID,
  launch_id: LAUNCH_ID,
  owner_generation: 1,
  expires_at_unix: result === "acquired" || result === "renewed" ? 1_768_521_630 : null,
  lease_ttl_seconds: 30,
  renewal_interval_seconds: 10,
  ...overrides,
})

const registrationResponse = (result, overrides = {}) => ({
  schema_version: ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION,
  result,
  engine_instance_id: INSTANCE_ID,
  registration_id: REGISTRATION_ID,
  registration_generation: 1,
  client_instance_id: CLIENT_INSTANCE_ID,
  workspace_id: WORKSPACE_ID,
  lifecycle_mode: "local-owned",
  first_slice_contract_id: P30_SESSION_CONTRACT_ID,
  first_slice_schema_sha256: P30_SESSION_SCHEMA_SHA256,
  registered_at_unix: 1_768_521_600,
  expires_at_unix: result === "registered" || result === "renewed" ? 1_768_521_630 : null,
  admission_epoch: 4,
  lease_ttl_seconds: 30,
  renewal_interval_seconds: 10,
  ...overrides,
})

const drainResponse = (result, overrides = {}) => {
  const admissionOpen = result === "rolled_back"
  return {
    schema_version: ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
    result,
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    launch_id: LAUNCH_ID,
    drain_generation: 2,
    admission_epoch: admissionOpen ? 6 : 5,
    session_admission_open: admissionOpen,
    turn_admission_open: admissionOpen,
    registrations_open: admissionOpen,
    signal_permitted: result === "hard_signal_decision_pending",
    ...overrides,
  }
}

const jsonResponse = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json", ...headers },
})

const deferred = () => {
  let resolve
  const promise = new Promise((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

const delayedJsonResponse = (status, headers = {}) => {
  const bodyStarted = deferred()
  return {
    bodyStarted: bodyStarted.promise,
    response: {
      status,
      ok: status >= 200 && status < 300,
      redirected: false,
      headers: new Headers({ "content-type": "application/json", ...headers }),
      json: () => {
        bodyStarted.resolve()
        return new Promise(() => {})
      },
    },
  }
}

const capturedRequest = (input, init = {}) => ({
  url: String(input),
  method: init.method,
  body: init.body,
  headers: new Headers(init.headers),
  redirect: init.redirect,
  signal: init.signal,
})

const queueFetch = (entries, requests = []) => async (input, init = {}) => {
  requests.push(capturedRequest(input, init))
  const entry = entries.shift()
  assert.notEqual(entry, undefined, `unexpected request ${init.method ?? "GET"} ${String(input)}`)
  if (typeof entry === "function") return entry(input, init)
  if (entry instanceof Error) throw entry
  return entry
}

const bindClient = async ({
  responses = [],
  baseUrl = "https://breadboard.test/control/",
  config = {},
} = {}) => {
  const requests = []
  const entries = [jsonResponse(identityResponse()), ...responses]
  const fetch = queueFetch(entries, requests)
  const client = createLifecycleE4Client({
    baseUrl,
    expectedSessionContract,
    fetch,
    ...config,
  })
  const bound = await client.handshake()
  return { bound, client, entries, fetch, requests }
}

const failureOf = async (operation) => {
  try {
    await operation()
    assert.fail("expected LifecycleE4ClientError")
  } catch (error) {
    assert.ok(error instanceof LifecycleE4ClientError)
    return { error, failure: error.failure }
  }
}

const assertNoSecrets = (value, ...secrets) => {
  const serialized = JSON.stringify(value)
  for (const secret of secrets) assert.equal(serialized.includes(secret), false)
}

const ownerLeaseInput = (overrides = {}) => ({
  ownerGeneration: 1,
  ownerCredential: OWNER_CREDENTIAL,
  ...overrides,
})

const clientLeaseInput = (overrides = {}) => ({
  registrationId: REGISTRATION_ID,
  registrationGeneration: 1,
  clientInstanceId: CLIENT_INSTANCE_ID,
  registrationCredential: REGISTRATION_CREDENTIAL,
  ...overrides,
})

const beginDrainInput = (overrides = {}) => ({
  ownerGeneration: 1,
  ownerCredential: OWNER_CREDENTIAL,
  registrationId: REGISTRATION_ID,
  requesterRegistrationGeneration: 1,
  requesterClientInstanceId: CLIENT_INSTANCE_ID,
  expectedAdmissionEpoch: 4,
  registrationCredential: REGISTRATION_CREDENTIAL,
  ...overrides,
})

const drainControlInput = (overrides = {}) => ({
  ownerGeneration: 1,
  drainGeneration: 2,
  ownerCredential: OWNER_CREDENTIAL,
  ...overrides,
})

const renewOwnerFailure = async (payload) => {
  const { bound } = await bindClient({ responses: [jsonResponse(payload)] })
  return failureOf(() => bound.renewOwner(ownerLeaseInput()))
}

const renewClientFailure = async (payload) => {
  const { bound } = await bindClient({ responses: [jsonResponse(payload)] })
  return failureOf(() => bound.renewClient(clientLeaseInput()))
}

const beginDrainFailure = async (payload) => {
  const { bound } = await bindClient({ responses: [jsonResponse(payload)] })
  return failureOf(() => bound.beginControlDrain(beginDrainInput()))
}

test("exports the fixed lifecycle schemas without changing the canonical E4 session port", () => {
  assert.equal(ENGINE_IDENTITY_SCHEMA_VERSION, "bb.engine_identity.v1")
  assert.equal(ENGINE_OWNER_SCHEMA_VERSION, "bb.engine_owner.v1")
  assert.equal(ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION, "bb.engine_client_registration.v1")
  assert.equal(ENGINE_DRAIN_CONTROL_SCHEMA_VERSION, "bb.engine_drain_control.v1")
  assert.equal(P30_SESSION_CONTRACT_ID, "p30-e4-session-v1")
  assert.equal(P30_SESSION_SCHEMA_SHA256, "sha256:5757652c22d6aa2eb7a1cc8be1a40021d3f6a15df18d69ca22dc1916a400dbd4")
  assert.strictEqual(sdk.createLifecycleE4Client, createLifecycleE4Client)

  for (const [name, value] of Object.entries(sessionRuntime)) {
    assert.strictEqual(sdk[name], value, `index changed session-runtime export ${name}`)
  }
  const canonical = sessionRuntime.createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => assert.fail("canonical port should remain lazy"),
  })
  assert.deepEqual(Object.keys(canonical).sort(), ["attach", "create"])
})

test("configuration requires the exact fixed session expectation and one bearer source", async () => {
  let fetchCalls = 0
  const fetch = async () => {
    fetchCalls += 1
    return jsonResponse(identityResponse())
  }
  const mismatches = [
    { contractId: "p30-e4-session-v2", schemaSha256: P30_SESSION_SCHEMA_SHA256 },
    { contractId: P30_SESSION_CONTRACT_ID, schemaSha256: `sha256:${"0".repeat(64)}` },
  ]
  for (const expected of mismatches) {
    const { failure } = await failureOf(() => createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract: expected,
      fetch,
    }))
    assert.deepEqual(failure, {
      kind: "session-schema-mismatch",
      status: 0,
      code: "expected_session_contract_mismatch",
      correlation: {},
      body: REDACTED_VALUE,
    })
  }

  const multipleBearerSources = await failureOf(() => createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    bearerToken: BEARER_TOKEN,
    bearerTokenResolver: () => BEARER_TOKEN,
    fetch,
  }))
  assert.deepEqual(multipleBearerSources.failure, { kind: "protocol", code: "multiple_bearer_sources" })
  assert.equal(fetchCalls, 0)
})

test("handshake uses the exact prefixed identity route and returns an immutable normalized binding", async () => {
  const resolvedTokens = []
  const bearerTokenResolver = async () => {
    const token = `resolved-bearer-${resolvedTokens.length + 1}`
    resolvedTokens.push(token)
    return token
  }
  const { bound, client, requests } = await bindClient({ config: { bearerTokenResolver } })

  assert.deepEqual(bound.binding, {
    endpoint: "https://breadboard.test/control",
    engineInstanceId: INSTANCE_ID,
    engineBootId: BOOT_ID,
    launchId: LAUNCH_ID,
    protocolVersion: "1.0",
    sessionContractId: P30_SESSION_CONTRACT_ID,
    sessionSchemaSha256: P30_SESSION_SCHEMA_SHA256,
  })
  assert.equal(Object.isFrozen(bound.binding), true)
  assert.equal(Object.isFrozen(bound), true)
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, "https://breadboard.test/control/v1/engine/identity")
  assert.equal(requests[0].method, "GET")
  assert.equal(requests[0].body, undefined)
  assert.equal(requests[0].redirect, "manual")
  assert.equal(requests[0].headers.get("authorization"), "Bearer resolved-bearer-1")
  assert.deepEqual(resolvedTokens, ["resolved-bearer-1"])

  const forbidden = [
    "spawn", "signal", "sendSignal", "recordHardSignalDecision", "pid", "processId",
    "createSession", "attachSession", "deleteSession", "deleteOwner", "deleteClient", "delete",
  ]
  for (const name of forbidden) {
    assert.equal(name in client, false, `unbound lifecycle client exposed ${name}`)
    assert.equal(name in bound, false, `bound lifecycle client exposed ${name}`)
  }
  assert.deepEqual(
    [
      "acquireOwner", "renewOwner", "releaseOwner", "registerClient", "renewClient",
      "detachClient", "beginControlDrain", "recordGracefulControl", "rollbackDrain",
    ].filter((name) => typeof bound[name] === "function").sort(),
    [
      "acquireOwner", "beginControlDrain", "detachClient", "recordGracefulControl", "registerClient",
      "releaseOwner", "renewClient", "renewOwner", "rollbackDrain",
    ],
  )
})

test("owner calls use exact routes, binding bodies, and generation-zero bootstrap headers only", async () => {
  const responses = [
    jsonResponse(ownerResponse("acquired")),
    jsonResponse(ownerResponse("acquired", { owner_generation: 2 })),
    jsonResponse(ownerResponse("renewed", { owner_generation: 2 })),
    jsonResponse(ownerResponse("released", { owner_generation: 2 })),
  ]
  const { bound, requests } = await bindClient({ responses, config: { bearerToken: BEARER_TOKEN } })

  const first = await bound.acquireOwner({
    expectedOwnerGeneration: 0,
    ownerCredential: OWNER_CREDENTIAL,
    bootstrapCredential: BOOTSTRAP_CREDENTIAL,
    ignoredCredential: "must-not-leak",
  })
  const reacquired = await bound.acquireOwner({
    expectedOwnerGeneration: 1,
    ownerCredential: OWNER_CREDENTIAL,
  })
  const renewed = await bound.renewOwner({ ownerGeneration: 2, ownerCredential: OWNER_CREDENTIAL })
  const released = await bound.releaseOwner({ ownerGeneration: 2, ownerCredential: OWNER_CREDENTIAL })

  assert.equal(first.result, "acquired")
  assert.deepEqual(first, {
    schemaVersion: ENGINE_OWNER_SCHEMA_VERSION,
    engineInstanceId: INSTANCE_ID,
    engineBootId: BOOT_ID,
    launchId: LAUNCH_ID,
    ownerGeneration: 1,
    leaseTtlSeconds: 30,
    renewalIntervalSeconds: 10,
    result: "acquired",
    expiresAtUnix: 1_768_521_630,
  })
  assert.equal(reacquired.ownerGeneration, 2)
  assert.equal(renewed.result, "renewed")
  assert.equal(released.result, "released")
  assert.deepEqual(requests.slice(1).map(({ method, url }) => [method, url]), [
    ["POST", "https://breadboard.test/control/v1/engine/owner/acquire"],
    ["POST", "https://breadboard.test/control/v1/engine/owner/acquire"],
    ["POST", "https://breadboard.test/control/v1/engine/owner/renew"],
    ["POST", "https://breadboard.test/control/v1/engine/owner/release"],
  ])
  assert.deepEqual(requests.slice(1).map(({ body }) => JSON.parse(body)), [
    { engine_instance_id: INSTANCE_ID, engine_boot_id: BOOT_ID, launch_id: LAUNCH_ID, expected_owner_generation: 0 },
    { engine_instance_id: INSTANCE_ID, engine_boot_id: BOOT_ID, launch_id: LAUNCH_ID, expected_owner_generation: 1 },
    { engine_instance_id: INSTANCE_ID, engine_boot_id: BOOT_ID, launch_id: LAUNCH_ID, owner_generation: 2 },
    { engine_instance_id: INSTANCE_ID, engine_boot_id: BOOT_ID, launch_id: LAUNCH_ID, owner_generation: 2 },
  ])
  assert.equal(requests[1].headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
  assert.equal(requests[1].headers.get("x-breadboard-bootstrap-credential"), BOOTSTRAP_CREDENTIAL)
  assert.equal(requests[2].headers.get("x-breadboard-bootstrap-credential"), null)
  assert.equal(requests[3].headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
  assert.equal(requests[4].headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
  assert.equal(requests.slice(1).every(({ headers }) => headers.get("authorization") === `Bearer ${BEARER_TOKEN}`), true)
  assertNoSecrets(requests.slice(1).map(({ body }) => body), OWNER_CREDENTIAL, BOOTSTRAP_CREDENTIAL, "must-not-leak")
  assertNoSecrets([first, reacquired, renewed, released], OWNER_CREDENTIAL, BOOTSTRAP_CREDENTIAL)
})

test("owner acquisition rejects missing generation-zero bootstrap and bootstrap reuse after generation zero without a request", async () => {
  const { bound, requests } = await bindClient()
  const missing = await failureOf(() => bound.acquireOwner({
    expectedOwnerGeneration: 0,
    ownerCredential: OWNER_CREDENTIAL,
  }))
  const stale = await failureOf(() => bound.acquireOwner({
    expectedOwnerGeneration: 1,
    ownerCredential: OWNER_CREDENTIAL,
    bootstrapCredential: BOOTSTRAP_CREDENTIAL,
  }))

  assert.equal(missing.failure.kind, "protocol")
  assert.equal(stale.failure.kind, "protocol")
  assert.equal(requests.length, 1)
  assertNoSecrets([missing.error, stale.error], OWNER_CREDENTIAL, BOOTSTRAP_CREDENTIAL)
})

test("malformed local authority credentials are sanitized auth failures before fetch", async () => {
  const { bound, requests } = await bindClient()
  const cases = [
    ["invalid_owner_credential", "short-owner", () => bound.renewOwner({
      ownerGeneration: 1,
      ownerCredential: "short-owner",
    })],
    ["invalid_bootstrap_credential", "short-bootstrap", () => bound.acquireOwner({
      expectedOwnerGeneration: 0,
      ownerCredential: OWNER_CREDENTIAL,
      bootstrapCredential: "short-bootstrap",
    })],
    ["invalid_registration_credential", "short-registration", () => bound.registerClient({
      clientInstanceId: CLIENT_INSTANCE_ID,
      workspaceId: WORKSPACE_ID,
      lifecycleMode: "local-owned",
      registrationCredential: "short-registration",
    })],
  ]
  for (const [code, secret, operation] of cases) {
    const { error, failure } = await failureOf(operation)
    assert.deepEqual(failure, {
      kind: "auth",
      status: 0,
      code,
      correlation: {},
      body: REDACTED_VALUE,
    })
    assertNoSecrets([error, failure], secret)
  }
  assert.equal(requests.length, 1)
})

test("client registration calls use exact routes, snake-case bodies, and registration credential headers", async () => {
  const responses = [
    jsonResponse(registrationResponse("registered")),
    jsonResponse(registrationResponse("renewed")),
    jsonResponse(registrationResponse("detached")),
  ]
  const { bound, requests } = await bindClient({ responses })
  const registered = await bound.registerClient({
    clientInstanceId: CLIENT_INSTANCE_ID,
    workspaceId: WORKSPACE_ID,
    lifecycleMode: "local-owned",
    registrationCredential: REGISTRATION_CREDENTIAL,
    accidentalSecret: "must-not-leak",
  })
  const renewed = await bound.renewClient(clientLeaseInput())
  const detached = await bound.detachClient(clientLeaseInput())

  assert.equal(registered.registrationId, REGISTRATION_ID)
  assert.deepEqual(registered, {
    schemaVersion: ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION,
    engineInstanceId: INSTANCE_ID,
    registrationId: REGISTRATION_ID,
    registrationGeneration: 1,
    clientInstanceId: CLIENT_INSTANCE_ID,
    workspaceId: WORKSPACE_ID,
    lifecycleMode: "local-owned",
    firstSliceContractId: P30_SESSION_CONTRACT_ID,
    firstSliceSchemaSha256: P30_SESSION_SCHEMA_SHA256,
    registeredAtUnix: 1_768_521_600,
    admissionEpoch: 4,
    leaseTtlSeconds: 30,
    renewalIntervalSeconds: 10,
    result: "registered",
    expiresAtUnix: 1_768_521_630,
  })
  assert.equal(renewed.result, "renewed")
  assert.equal(detached.result, "detached")
  assert.deepEqual(requests.slice(1).map(({ method, url }) => [method, url]), [
    ["POST", "https://breadboard.test/control/v1/engine/clients/register"],
    ["POST", "https://breadboard.test/control/v1/engine/clients/renew"],
    ["POST", "https://breadboard.test/control/v1/engine/clients/detach"],
  ])
  assert.deepEqual(JSON.parse(requests[1].body), {
    engine_instance_id: INSTANCE_ID,
    client_instance_id: CLIENT_INSTANCE_ID,
    workspace_id: WORKSPACE_ID,
    lifecycle_mode: "local-owned",
    first_slice_contract_id: P30_SESSION_CONTRACT_ID,
    first_slice_schema_sha256: P30_SESSION_SCHEMA_SHA256,
  })
  assert.deepEqual(JSON.parse(requests[2].body), {
    engine_instance_id: INSTANCE_ID,
    registration_id: REGISTRATION_ID,
    registration_generation: 1,
    client_instance_id: CLIENT_INSTANCE_ID,
  })
  assert.deepEqual(JSON.parse(requests[3].body), JSON.parse(requests[2].body))
  for (const request of requests.slice(1)) {
    assert.equal(request.headers.get("x-breadboard-registration-credential"), REGISTRATION_CREDENTIAL)
    assert.equal(request.headers.get("x-breadboard-owner-credential"), null)
  }
  assertNoSecrets(requests.slice(1).map(({ body }) => body), REGISTRATION_CREDENTIAL, "must-not-leak")
  assertNoSecrets([registered, renewed, detached], REGISTRATION_CREDENTIAL)
})

test("drain calls use only the canonical control routes and enforce graceful outcome transitions", async () => {
  const responses = [
    jsonResponse(drainResponse("draining")),
    jsonResponse(drainResponse("shutdown_started")),
    jsonResponse(drainResponse("rollback_permitted")),
    jsonResponse(drainResponse("hard_signal_decision_pending")),
    jsonResponse(drainResponse("hard_signal_decision_pending")),
    jsonResponse(drainResponse("rolled_back")),
  ]
  const { bound, requests } = await bindClient({ responses })

  const draining = await bound.beginControlDrain(beginDrainInput())
  assert.deepEqual(draining, {
    schemaVersion: ENGINE_DRAIN_CONTROL_SCHEMA_VERSION,
    engineInstanceId: INSTANCE_ID,
    engineBootId: BOOT_ID,
    launchId: LAUNCH_ID,
    result: "draining",
    drainGeneration: 2,
    admissionEpoch: 5,
    sessionAdmissionOpen: false,
    turnAdmissionOpen: false,
    registrationsOpen: false,
    signalPermitted: false,
  })
  assert.equal((await bound.recordGracefulControl({ ...drainControlInput(), outcome: "accepted" })).result, "shutdown_started")
  assert.equal((await bound.recordGracefulControl({ ...drainControlInput(), outcome: "definitive_rejection" })).result, "rollback_permitted")
  assert.equal((await bound.recordGracefulControl({ ...drainControlInput(), outcome: "timeout" })).result, "hard_signal_decision_pending")
  assert.equal((await bound.recordGracefulControl({ ...drainControlInput(), outcome: "uncertain" })).result, "hard_signal_decision_pending")
  assert.equal((await bound.rollbackDrain(drainControlInput())).result, "rolled_back")

  assert.deepEqual(requests.slice(1).map(({ method, url }) => [method, url]), [
    ["POST", "https://breadboard.test/control/v1/engine/control/drain"],
    ["POST", "https://breadboard.test/control/v1/engine/control/graceful-result"],
    ["POST", "https://breadboard.test/control/v1/engine/control/graceful-result"],
    ["POST", "https://breadboard.test/control/v1/engine/control/graceful-result"],
    ["POST", "https://breadboard.test/control/v1/engine/control/graceful-result"],
    ["POST", "https://breadboard.test/control/v1/engine/control/drain-rollback"],
  ])
  assert.deepEqual(JSON.parse(requests[1].body), {
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    launch_id: LAUNCH_ID,
    owner_generation: 1,
    registration_id: REGISTRATION_ID,
    requester_registration_generation: 1,
    requester_client_instance_id: CLIENT_INSTANCE_ID,
    expected_admission_epoch: 4,
  })
  const gracefulBody = {
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    launch_id: LAUNCH_ID,
    owner_generation: 1,
    drain_generation: 2,
  }
  assert.deepEqual(requests.slice(2, 6).map(({ body }) => JSON.parse(body)), [
    { ...gracefulBody, outcome: "accepted" },
    { ...gracefulBody, outcome: "definitive_rejection" },
    { ...gracefulBody, outcome: "timeout" },
    { ...gracefulBody, outcome: "uncertain" },
  ])
  assert.deepEqual(JSON.parse(requests[6].body), {
    engine_instance_id: INSTANCE_ID,
    engine_boot_id: BOOT_ID,
    launch_id: LAUNCH_ID,
    owner_generation: 1,
    drain_generation: 2,
  })
  assert.equal(requests[1].headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
  assert.equal(requests[1].headers.get("x-breadboard-registration-credential"), REGISTRATION_CREDENTIAL)
  for (const request of requests.slice(2)) {
    assert.equal(request.headers.get("x-breadboard-owner-credential"), OWNER_CREDENTIAL)
    assert.equal(request.headers.get("x-breadboard-registration-credential"), null)
  }
  assert.equal(requests.some(({ url }) => url.includes("signal-decision")), false)
  assertNoSecrets(requests.slice(1).map(({ body }) => body), OWNER_CREDENTIAL, REGISTRATION_CREDENTIAL)
})

test("identity handshake rejects closed-schema violations and incompatible readiness", async () => {
  const cases = [
    ["extra identity field", (value) => { value.extra = true }, "protocol"],
    ["missing identity field", (value) => { delete value.process.engine_boot_id }, "protocol"],
    ["unknown liveness enum", (value) => { value.liveness.status = "starting" }, "protocol"],
    ["contradictory readiness", (value) => { value.session_readiness = { ready: true, reason: "session_contract_mismatch" } }, "protocol"],
    ["identity schema substitution", (value) => { value.schema_version = ENGINE_OWNER_SCHEMA_VERSION }, "protocol"],
    ["session contract id mismatch", (value) => { value.session_contract.contract_id = "p30-e4-session-v2" }, "session-schema-mismatch"],
    ["session schema digest mismatch", (value) => { value.session_contract.schema_sha256 = `sha256:${"0".repeat(64)}` }, "session-schema-mismatch"],
    ["incompatible session", (value) => {
      value.session_contract.compatibility = "incompatible"
      value.session_readiness = { ready: false, reason: "session_contract_mismatch" }
    }, "session-schema-mismatch"],
  ]

  for (const [label, mutate, kind] of cases) {
    const payload = structuredClone(identityResponse())
    mutate(payload)
    const fetch = queueFetch([jsonResponse(payload)])
    const client = createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract,
      fetch,
    })
    const { failure } = await failureOf(() => client.handshake())
    assert.equal(failure.kind, kind, label)
    if (kind === "session-schema-mismatch") {
      assert.equal(failure.status, 200, label)
      assert.equal(failure.body, REDACTED_VALUE, label)
      assert.deepEqual(failure.correlation, {}, label)
    } else {
      assert.equal(typeof failure.code, "string", label)
    }
  }
})

test("bound responses reject instance, boot, launch, and registration contract echo mismatches", async () => {
  for (const field of ["engine_instance_id", "engine_boot_id", "launch_id"]) {
    const { failure } = await renewOwnerFailure(ownerResponse("renewed", { [field]: OTHER_ID }))
    assert.equal(failure.kind, "identity-changed", field)
    assert.equal(failure.status, 200, field)
    assert.equal(failure.code, "authority_binding_echo_mismatch", field)
    assert.deepEqual(failure.correlation, {}, field)
    assert.equal(failure.body, REDACTED_VALUE, field)
  }

  const registrationCases = [
    ["engine_instance_id", OTHER_ID, "identity-changed"],
    ["first_slice_contract_id", "p30-e4-session-v2", "session-schema-mismatch"],
    ["first_slice_schema_sha256", `sha256:${"0".repeat(64)}`, "session-schema-mismatch"],
    ["registration_id", OTHER_ID, "protocol"],
    ["client_instance_id", "different-client-01", "protocol"],
  ]
  for (const [field, value, kind] of registrationCases) {
    const { failure } = await renewClientFailure(registrationResponse("renewed", { [field]: value }))
    assert.equal(failure.kind, kind, field)
    if (kind === "session-schema-mismatch" || kind === "identity-changed") {
      assert.equal(failure.status, 200, field)
      assert.deepEqual(failure.correlation, {}, field)
      assert.equal(failure.body, REDACTED_VALUE, field)
    }
  }

  for (const field of ["engine_instance_id", "engine_boot_id", "launch_id"]) {
    const { failure } = await beginDrainFailure(drainResponse("draining", { [field]: OTHER_ID }))
    assert.equal(failure.kind, "identity-changed", field)
    assert.equal(failure.status, 200, field)
    assert.equal(failure.code, "authority_binding_echo_mismatch", field)
    assert.deepEqual(failure.correlation, {}, field)
    assert.equal(failure.body, REDACTED_VALUE, field)
  }
})

test("owner, registration, and control schema versions cannot substitute for one another", async () => {
  const owner = await renewOwnerFailure(ownerResponse("renewed", {
    schema_version: ENGINE_CLIENT_REGISTRATION_SCHEMA_VERSION,
  }))
  assert.deepEqual(owner.failure, {
    kind: "protocol",
    code: "owner_schema_mismatch",
    status: 200,
    correlation: {},
    body: REDACTED_VALUE,
  })

  const registration = await renewClientFailure(registrationResponse("renewed", {
    schema_version: ENGINE_OWNER_SCHEMA_VERSION,
  }))
  assert.deepEqual(registration.failure, {
    kind: "registration-schema-mismatch",
    status: 200,
    code: "registration_schema_mismatch",
    correlation: {},
    body: REDACTED_VALUE,
  })

  const control = await beginDrainFailure(drainResponse("draining", {
    schema_version: ENGINE_OWNER_SCHEMA_VERSION,
  }))
  assert.deepEqual(control.failure, {
    kind: "control-schema-mismatch",
    status: 200,
    code: "control_schema_mismatch",
    correlation: {},
    body: REDACTED_VALUE,
  })
})

test("owner and registration decoders reject extra, missing, unknown, and contradictory lease states", async () => {
  const ownerCases = [
    ["extra", () => ownerResponse("renewed", { extra: true })],
    ["missing", () => { const value = ownerResponse("renewed"); delete value.lease_ttl_seconds; return value }],
    ["unknown result", () => ownerResponse("replaced")],
    ["released with expiry", () => ownerResponse("released", { expires_at_unix: 1_768_521_630 })],
    ["renewed without expiry", () => ownerResponse("renewed", { expires_at_unix: null })],
    ["wrong operation result", () => ownerResponse("acquired")],
  ]
  for (const [label, make] of ownerCases) {
    const { failure } = await renewOwnerFailure(make())
    assert.equal(failure.kind, "protocol", label)
  }

  const registrationCases = [
    ["extra", () => registrationResponse("renewed", { extra: true })],
    ["missing", () => { const value = registrationResponse("renewed"); delete value.workspace_id; return value }],
    ["unknown result", () => registrationResponse("replaced")],
    ["unknown mode", () => registrationResponse("renewed", { lifecycle_mode: "off" })],
    ["detached with expiry", () => registrationResponse("detached", { expires_at_unix: 1_768_521_630 })],
    ["renewed without expiry", () => registrationResponse("renewed", { expires_at_unix: null })],
    ["wrong operation result", () => registrationResponse("registered")],
  ]
  for (const [label, make] of registrationCases) {
    const { failure } = await renewClientFailure(make())
    assert.equal(failure.kind, "protocol", label)
  }
})

test("control decoder rejects unknown, incomplete, extra, and contradictory drain states", async () => {
  const cases = [
    ["extra", () => drainResponse("draining", { extra: true })],
    ["missing", () => { const value = drainResponse("draining"); delete value.turn_admission_open; return value }],
    ["unknown result", () => drainResponse("paused")],
    ["closed state reports admission open", () => drainResponse("draining", { session_admission_open: true })],
    ["rolled back reports admission closed", () => drainResponse("rolled_back", { registrations_open: false })],
    ["draining permits signal", () => drainResponse("draining", { signal_permitted: true })],
    ["hard signal pending denies signal", () => drainResponse("hard_signal_decision_pending", { signal_permitted: false })],
  ]
  for (const [label, make] of cases) {
    const { failure } = await beginDrainFailure(make())
    assert.equal(failure.kind, "protocol", label)
  }

  const { bound } = await bindClient({ responses: [jsonResponse(drainResponse("hard_signal_decision_pending"))] })
  const mapping = await failureOf(() => bound.recordGracefulControl({
    ...drainControlInput(),
    outcome: "definitive_rejection",
  }))
  assert.equal(mapping.failure.kind, "protocol")
})

test("authority conflict and expiry codes map to their typed lifecycle failures", async () => {
  const cases = [
    ["owner_conflict", 409, "owner-conflict", "renewOwner", ownerLeaseInput()],
    ["owner_generation_conflict", 409, "owner-conflict", "renewOwner", ownerLeaseInput()],
    ["owner_expired", 410, "owner-expired", "renewOwner", ownerLeaseInput()],
    ["registration_conflict", 409, "registration-conflict", "renewClient", clientLeaseInput()],
    ["registration_generation_conflict", 409, "registration-conflict", "renewClient", clientLeaseInput()],
    ["registration_expired", 410, "registration-expired", "renewClient", clientLeaseInput()],
    ["drain_conflict", 409, "drain-conflict", "beginControlDrain", beginDrainInput()],
    ["admission_epoch_conflict", 409, "drain-conflict", "beginControlDrain", beginDrainInput()],
    ["drain_recovery_failed", 409, "recovery-failed", "rollbackDrain", drainControlInput()],
  ]

  for (const [code, status, kind, method, input] of cases) {
    const secret = `secret-${code}`
    const { bound } = await bindClient({
      responses: [jsonResponse({ error: code, detail: secret, path: null }, status)],
    })
    const { error, failure } = await failureOf(() => bound[method](input))
    assert.equal(failure.kind, kind, code)
    assert.equal(failure.status, status, code)
    assert.equal(failure.code, code, code)
    assert.equal(failure.body, REDACTED_VALUE, code)
    assertNoSecrets([error, failure], secret, OWNER_CREDENTIAL, REGISTRATION_CREDENTIAL)
  }
})

test("401 and 403 are auth failures while other remote HTTP failures retain only safe code and correlation", async () => {
  for (const status of [401, 403]) {
    const secret = `auth-secret-${status}`
    const { bound } = await bindClient({
      responses: [jsonResponse({ error: "unauthorized", detail: secret, path: "/private" }, status)],
      config: { bearerToken: BEARER_TOKEN },
    })
    const { error, failure } = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
    assert.equal(failure.kind, "auth")
    assert.equal(failure.status, status)
    assert.equal(failure.code, "unauthorized")
    assert.equal(failure.body, REDACTED_VALUE)
    assertNoSecrets([error, failure], secret, BEARER_TOKEN, OWNER_CREDENTIAL)
  }

  for (const [status, code] of [[401, "owner_expired"], [403, "drain_recovery_failed"]]) {
    const { bound } = await bindClient({
      responses: [jsonResponse({ error: code, detail: "must-be-redacted", path: null }, status)],
    })
    const { failure } = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
    assert.deepEqual(failure, {
      kind: "auth",
      status,
      code,
      correlation: {},
      body: REDACTED_VALUE,
    })
  }

  const secret = "remote-body-private-value"
  const { bound } = await bindClient({ responses: [jsonResponse({
    error: "remote_internal",
    detail: { secret, registration_id: REGISTRATION_ID, arbitrary: "do-not-retain" },
    path: `/private/${secret}`,
  }, 500, {
    "x-request-id": "request-safe-123",
    "x-correlation-id": "correlation-safe-456",
    "x-secret-correlation": secret,
  })] })
  const { error, failure } = await failureOf(() => bound.renewClient(clientLeaseInput()))
  assert.equal(failure.kind, "http")
  assert.equal(failure.status, 500)
  assert.equal(failure.code, "remote_internal")
  assert.equal(failure.body, REDACTED_VALUE)
  assertNoSecrets([error, failure], secret, REGISTRATION_CREDENTIAL)
  assert.equal(JSON.stringify(failure).includes("do-not-retain"), false)
  assert.deepEqual(failure.correlation, {
    requestId: "request-safe-123",
    correlationId: "correlation-safe-456",
  })

  const unsafe = await bindClient({ responses: [jsonResponse({
    error: `UNSAFE CODE ${secret}`,
    detail: secret,
    path: null,
  }, 502, {
    "x-request-id": "q".repeat(129),
    "x-correlation-id": "contains a space",
  })] })
  const unsafeFailure = await failureOf(() => unsafe.bound.releaseOwner(ownerLeaseInput()))
  assert.deepEqual(unsafeFailure.failure, {
    kind: "http",
    status: 502,
    code: null,
    correlation: {},
    body: REDACTED_VALUE,
  })
  assertNoSecrets([unsafeFailure.error, unsafeFailure.failure], secret, OWNER_CREDENTIAL)
})

test("redirect responses are terminal and never forward bearer or authority credentials cross-origin", async () => {
  const requests = []
  const fetch = queueFetch([
    jsonResponse(identityResponse()),
    new Response(null, { status: 307, headers: { location: "https://attacker.test/v1/engine/owner/renew" } }),
    () => assert.fail("redirect must not be followed"),
  ], requests)
  const client = createLifecycleE4Client({
    baseUrl: "https://breadboard.test/control",
    expectedSessionContract,
    bearerToken: BEARER_TOKEN,
    fetch,
  })
  const bound = await client.handshake()
  const { error, failure } = await failureOf(() => bound.renewOwner(ownerLeaseInput()))

  assert.equal(failure.kind, "redirect")
  assert.equal(failure.status, 307)
  assert.equal(requests.length, 2)
  assert.equal(requests.every(({ redirect }) => redirect === "manual"), true)
  assert.equal(requests.some(({ url }) => url.startsWith("https://attacker.test")), false)
  assertNoSecrets([error, failure], BEARER_TOKEN, OWNER_CREDENTIAL)
})

test("a canonical-route rejection does not trigger legacy or unprefixed route fallback", async () => {
  const { bound, requests } = await bindClient({
    baseUrl: "https://breadboard.test/prefix",
    responses: [jsonResponse({ error: "not_found", detail: "missing", path: null }, 404)],
  })
  const { failure } = await failureOf(() => bound.detachClient(clientLeaseInput()))

  assert.equal(failure.kind, "http")
  assert.equal(failure.status, 404)
  assert.equal(requests.length, 2)
  assert.equal(requests[1].url, "https://breadboard.test/prefix/v1/engine/clients/detach")
})

test("TLS protocol, certificate, hostname, handshake, and pin failures are typed and redacted", async () => {
  const tlsCodes = [
    "EPROTO",
    "ERR_SSL_WRONG_VERSION_NUMBER",
    "CERT_HAS_EXPIRED",
    "ERR_TLS_CERT_ALTNAME_INVALID",
    "ERR_SSL_SSLV3_ALERT_HANDSHAKE_FAILURE",
    "ERR_SSL_PINNED_KEY_NOT_IN_CERT_CHAIN",
    "ERR_TLS_CERT_ALTNAME_FORMAT",
    "ERR_TLS_DH_PARAM_SIZE",
  ]
  for (const code of tlsCodes) {
    const secret = `tls-private-${code}`
    const tlsError = new TypeError(`TLS failure for ${secret}`, {
      cause: Object.assign(new Error("TLS negotiation failed"), { code }),
    })
    const { bound } = await bindClient({ responses: [tlsError] })
    const { error, failure } = await failureOf(() => bound.releaseOwner(ownerLeaseInput()))
    assert.deepEqual(failure, { kind: "tls", code: "tls_transport_error" }, code)
    assertNoSecrets([error, failure], secret, OWNER_CREDENTIAL)
  }
})

test("request timeout and caller abort remain distinct typed failures", async () => {
  const timeoutRequests = []
  const timeoutFetch = queueFetch([
    jsonResponse(identityResponse()),
    (_input, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true })
    }),
  ], timeoutRequests)
  const timeoutClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    timeoutMs: 5,
    fetch: timeoutFetch,
  })
  const timeoutBound = await timeoutClient.handshake()
  const timeout = await failureOf(() => timeoutBound.renewOwner(ownerLeaseInput()))
  assert.equal(timeout.failure.kind, "timeout")
  assert.equal(timeoutRequests.length, 2)

  const callerRequests = []
  const callerFetch = queueFetch([jsonResponse(identityResponse())], callerRequests)
  const callerClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    timeoutMs: 5,
    fetch: callerFetch,
  })
  const callerBound = await callerClient.handshake()
  const controller = new AbortController()
  controller.abort("caller-owned-reason-must-not-leak")
  const caller = await failureOf(() => callerBound.renewOwner(ownerLeaseInput({ signal: controller.signal })))
  assert.equal(caller.failure.kind, "caller-abort")
  assert.equal(callerRequests.length, 1)
  assertNoSecrets(caller.error, "caller-owned-reason-must-not-leak", OWNER_CREDENTIAL)
})

test("malformed success and transport exceptions never expose response bodies or credentials", async () => {
  const bodySecret = "malformed-success-private-value"
  const malformed = new Response(`{"schema_version":"${ENGINE_OWNER_SCHEMA_VERSION}","secret":"${bodySecret}"`, {
    status: 200,
    headers: {
      "content-type": "application/json",
      "x-request-id": "malformed-request",
      "x-correlation-id": "malformed-correlation",
    },
  })
  const { bound } = await bindClient({ responses: [malformed] })
  const malformedFailure = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
  assert.deepEqual(malformedFailure.failure, {
    kind: "protocol",
    code: "invalid_json_response",
    status: 200,
    correlation: {
      requestId: "malformed-request",
      correlationId: "malformed-correlation",
    },
    body: REDACTED_VALUE,
  })
  assertNoSecrets([malformedFailure.error, malformedFailure.failure], bodySecret, OWNER_CREDENTIAL)

  const transportSecret = "transport-private-value"
  const thrown = new Error(`socket failed with ${transportSecret} and ${OWNER_CREDENTIAL}`)
  const second = await bindClient({ responses: [thrown] })
  const transportFailure = await failureOf(() => second.bound.renewOwner(ownerLeaseInput()))
  assert.deepEqual(transportFailure.failure, {
    kind: "http",
    status: 0,
    code: null,
    correlation: {},
    body: REDACTED_VALUE,
  })
  assertNoSecrets([transportFailure.error, transportFailure.failure], transportSecret, OWNER_CREDENTIAL)
})

test("timeouts and caller aborts remain distinct while reading delayed success and error bodies", async () => {
  for (const status of [200, 409]) {
    const delayed = delayedJsonResponse(status)
    const { bound } = await bindClient({
      responses: [delayed.response],
      config: { timeoutMs: 20 },
    })
    const timedOut = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
    assert.deepEqual(timedOut.failure, { kind: "timeout" }, `status ${status}`)
    await delayed.bodyStarted
  }

  for (const status of [200, 409]) {
    const delayed = delayedJsonResponse(status)
    const { bound } = await bindClient({
      responses: [delayed.response],
      config: { timeoutMs: 1_000 },
    })
    const controller = new AbortController()
    const pending = failureOf(() => bound.renewOwner(ownerLeaseInput({ signal: controller.signal })))
    await delayed.bodyStarted
    controller.abort("body-phase-caller-secret")
    const aborted = await pending
    assert.deepEqual(aborted.failure, { kind: "caller-abort" }, `status ${status}`)
    assertNoSecrets(aborted.error, "body-phase-caller-secret", OWNER_CREDENTIAL)
  }
})

test("engine started_at accepts strict RFC3339 instants and rejects permissive Date.parse forms", async () => {
  const withStartedAt = (startedAt) => {
    const payload = identityResponse()
    payload.process.started_at = startedAt
    return payload
  }
  for (const startedAt of [
    "2026-07-17T00:00:00Z",
    "2026-07-17T23:59:59.123456+05:30",
  ]) {
    const client = createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract,
      fetch: queueFetch([jsonResponse(withStartedAt(startedAt))]),
    })
    assert.equal((await client.handshake()).binding.engineInstanceId, INSTANCE_ID)
  }

  const invalid = [
    "July 17, 2026 00:00:00 GMT",
    "2026-07-17",
    "2026-07-17T00:00:00",
    "2026-02-30T00:00:00Z",
    1_768_521_600,
  ]
  for (const [index, startedAt] of invalid.entries()) {
    const requestId = `datetime-request-${index}`
    const client = createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract,
      fetch: queueFetch([jsonResponse(withStartedAt(startedAt), 200, {
        "x-request-id": requestId,
      })]),
    })
    const { failure } = await failureOf(() => client.handshake())
    assert.deepEqual(failure, {
      kind: "protocol",
      code: "engine_started_at_invalid",
      status: 200,
      correlation: { requestId },
      body: REDACTED_VALUE,
    })
  }
})

test("malformed 2xx, schema mismatch, and identity echo failures retain only response context", async () => {
  const cases = [
    {
      kind: "protocol",
      code: "owner_response_shape_mismatch",
      payload: ownerResponse("renewed", { private_response_value: "owner-response-secret" }),
      invoke: (bound) => bound.renewOwner(ownerLeaseInput()),
      secret: "owner-response-secret",
    },
    {
      kind: "registration-schema-mismatch",
      code: "registration_schema_mismatch",
      payload: registrationResponse("renewed", { schema_version: "private-registration-schema" }),
      invoke: (bound) => bound.renewClient(clientLeaseInput()),
      secret: "private-registration-schema",
    },
    {
      kind: "identity-changed",
      code: "authority_binding_echo_mismatch",
      payload: drainResponse("draining", { engine_instance_id: "s".repeat(43) }),
      invoke: (bound) => bound.beginControlDrain(beginDrainInput()),
      secret: "s".repeat(43),
    },
  ]
  for (const [index, item] of cases.entries()) {
    const requestId = `response-request-${index}`
    const correlationId = `response-correlation-${index}`
    const { bound } = await bindClient({ responses: [jsonResponse(item.payload, 200, {
      "x-request-id": requestId,
      "x-correlation-id": correlationId,
      "x-private-context": item.secret,
    })] })
    const { error, failure } = await failureOf(() => item.invoke(bound))
    assert.deepEqual(failure, {
      kind: item.kind,
      code: item.code,
      status: 200,
      correlation: { requestId, correlationId },
      body: REDACTED_VALUE,
    })
    assertNoSecrets([error, failure], item.secret, OWNER_CREDENTIAL, REGISTRATION_CREDENTIAL)
  }
})

test("bearer resolution and validation failures are sanitized before any request", async () => {
  const resolverSecret = "resolver-private-failure"
  const invalidSources = [
    {
      code: "bearer_resolution_failed",
      secret: resolverSecret,
      config: {
        bearerTokenResolver: () => {
          throw new LifecycleE4ClientError({ kind: "protocol", code: resolverSecret })
        },
      },
    },
    {
      code: "bearer_resolution_failed",
      secret: "resolver-abort-private",
      config: {
        bearerTokenResolver: () => {
          throw new DOMException("resolver-abort-private", "AbortError")
        },
      },
    },
    {
      code: "bearer_token_invalid",
      secret: "resolver-nonstring-secret",
      config: { bearerTokenResolver: () => ({ secret: "resolver-nonstring-secret" }) },
    },
    {
      code: "bearer_token_invalid",
      secret: "1",
      config: { bearerToken: "1" },
    },
    {
      code: "bearer_token_invalid",
      secret: "abcdefghijklmno",
      config: { bearerToken: "abcdefghijklmno" },
    },
    {
      code: "bearer_token_invalid",
      secret: "bearer contains whitespace",
      config: { bearerToken: "bearer contains whitespace" },
    },
    {
      code: "bearer_token_invalid",
      secret: "bearer-control\nvalue",
      config: { bearerToken: "bearer-control\nvalue" },
    },
  ]
  for (const item of invalidSources) {
    let fetchCalls = 0
    const client = createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract,
      fetch: async () => {
        fetchCalls += 1
        return jsonResponse(identityResponse())
      },
      ...item.config,
    })
    const { error, failure } = await failureOf(() => client.handshake())
    assert.deepEqual(failure, {
      kind: "auth",
      status: 0,
      code: item.code,
      correlation: {},
      body: REDACTED_VALUE,
    })
    assert.equal(fetchCalls, 0)
    assertNoSecrets([error, failure], item.secret)
  }
})

test("success payloads cannot reflect bearer or authority credentials into typed results", async () => {
  const reflectedBearer = "h".repeat(43)
  const reflectedIdentity = identityResponse()
  reflectedIdentity.process.engine_instance_id = reflectedBearer
  const identityClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    bearerToken: reflectedBearer,
    fetch: queueFetch([jsonResponse(reflectedIdentity, 200, {
      "x-request-id": "reflection-handshake",
    })]),
  })
  const identityFailure = await failureOf(() => identityClient.handshake())
  assert.deepEqual(identityFailure.failure, {
    kind: "protocol",
    code: "sensitive_response_reflection",
    status: 200,
    correlation: { requestId: "reflection-handshake" },
    body: REDACTED_VALUE,
  })
  assertNoSecrets([identityFailure.error, identityFailure.failure], reflectedBearer)

  const reflectedRegistration = "g".repeat(43)
  const { bound } = await bindClient({ responses: [jsonResponse(
    registrationResponse("registered", { registration_id: reflectedRegistration }),
    200,
    { "x-correlation-id": "reflection-registration" },
  )] })
  const registrationFailure = await failureOf(() => bound.registerClient({
    clientInstanceId: CLIENT_INSTANCE_ID,
    workspaceId: WORKSPACE_ID,
    lifecycleMode: "local-owned",
    registrationCredential: reflectedRegistration,
  }))
  assert.deepEqual(registrationFailure.failure, {
    kind: "protocol",
    code: "sensitive_response_reflection",
    status: 200,
    correlation: { correlationId: "reflection-registration" },
    body: REDACTED_VALUE,
  })
  assertNoSecrets([registrationFailure.error, registrationFailure.failure], reflectedRegistration)

  const minimumBearer = "abcdefghijklmnop"
  const minimumBearerClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    bearerToken: minimumBearer,
    fetch: queueFetch([jsonResponse(identityResponse())]),
  })
  assert.equal((await minimumBearerClient.handshake()).binding.protocolVersion, "1.0")

  const embeddedMinimumReflection = identityResponse()
  embeddedMinimumReflection.process.engine_instance_id = `${minimumBearer}${"x".repeat(27)}`
  const embeddedMinimumClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    bearerToken: minimumBearer,
    fetch: queueFetch([jsonResponse(embeddedMinimumReflection)]),
  })
  const embeddedMinimumFailure = await failureOf(() => embeddedMinimumClient.handshake())
  assert.deepEqual(embeddedMinimumFailure.failure, {
    kind: "protocol",
    code: "sensitive_response_reflection",
    status: 200,
    correlation: {},
    body: REDACTED_VALUE,
  })
  assertNoSecrets([embeddedMinimumFailure.error, embeddedMinimumFailure.failure], minimumBearer)

  const longBearer = "long-sensitive-bearer-value-0123456789"
  const embeddedLongReflection = identityResponse()
  embeddedLongReflection.artifact_revision.engine_artifact_sha256 = `prefix-${longBearer}-suffix`
  const embeddedLongClient = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    bearerToken: longBearer,
    fetch: queueFetch([jsonResponse(embeddedLongReflection)]),
  })
  const embeddedLongFailure = await failureOf(() => embeddedLongClient.handshake())
  assert.deepEqual(embeddedLongFailure.failure, {
    kind: "protocol",
    code: "sensitive_response_reflection",
    status: 200,
    correlation: {},
    body: REDACTED_VALUE,
  })
  assertNoSecrets([embeddedLongFailure.error, embeddedLongFailure.failure], longBearer)
})

test("generation and epoch values enforce safe-integer boundaries on inputs and responses", async () => {
  const { bound, requests } = await bindClient()
  const unsafeInputs = [
    ["owner_generation_invalid", () => bound.renewOwner(ownerLeaseInput({
      ownerGeneration: Number.MAX_SAFE_INTEGER + 1,
    }))],
    ["registration_generation_invalid", () => bound.renewClient(clientLeaseInput({
      registrationGeneration: 1.5,
    }))],
    ["expected_admission_epoch_invalid", () => bound.beginControlDrain(beginDrainInput({
      expectedAdmissionEpoch: Number.POSITIVE_INFINITY,
    }))],
  ]
  for (const [code, invoke] of unsafeInputs) {
    const { failure } = await failureOf(invoke)
    assert.deepEqual(failure, { kind: "protocol", code })
  }
  assert.equal(requests.length, 1)

  const unsafeResponses = [
    {
      code: "owner_generation_invalid",
      payload: ownerResponse("renewed", { owner_generation: Number.MAX_SAFE_INTEGER + 1 }),
      invoke: (client) => client.renewOwner(ownerLeaseInput()),
    },
    {
      code: "registration_admission_epoch_invalid",
      payload: registrationResponse("renewed", { admission_epoch: Number.MAX_SAFE_INTEGER + 1 }),
      invoke: (client) => client.renewClient(clientLeaseInput()),
    },
    {
      code: "drain_generation_invalid",
      payload: drainResponse("draining", { drain_generation: Number.MAX_SAFE_INTEGER + 1 }),
      invoke: (client) => client.beginControlDrain(beginDrainInput()),
    },
  ]
  for (const [index, item] of unsafeResponses.entries()) {
    const requestId = `unsafe-integer-${index}`
    const opened = await bindClient({ responses: [jsonResponse(item.payload, 200, {
      "x-request-id": requestId,
    })] })
    const { failure } = await failureOf(() => item.invoke(opened.bound))
    assert.deepEqual(failure, {
      kind: "protocol",
      code: item.code,
      status: 200,
      correlation: { requestId },
      body: REDACTED_VALUE,
    })
  }

  const maximum = Number.MAX_SAFE_INTEGER
  const accepted = await bindClient({
    responses: [jsonResponse(ownerResponse("renewed", { owner_generation: maximum }))],
  })
  const lease = await accepted.bound.renewOwner(ownerLeaseInput({ ownerGeneration: maximum }))
  assert.equal(lease.ownerGeneration, maximum)
  assert.equal(JSON.parse(accepted.requests[1].body).owner_generation, maximum)
})

test("timeoutMs accepts its safe upper bound and rejects zero, fractions, and timer overflow", async () => {
  let fetchCalls = 0
  const fetch = async () => {
    fetchCalls += 1
    return jsonResponse(identityResponse())
  }
  for (const timeoutMs of [0, 1.5, 2_147_483_648, Number.POSITIVE_INFINITY]) {
    const { failure } = await failureOf(() => createLifecycleE4Client({
      baseUrl: "https://breadboard.test",
      expectedSessionContract,
      timeoutMs,
      fetch,
    }))
    assert.deepEqual(failure, { kind: "protocol", code: "invalid_timeout_ms" })
  }
  const minimum = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    timeoutMs: 1,
    fetch,
  })
  const maximum = createLifecycleE4Client({
    baseUrl: "https://breadboard.test",
    expectedSessionContract,
    timeoutMs: 2_147_483_647,
    fetch,
  })
  assert.equal(typeof minimum.handshake, "function")
  assert.equal(typeof maximum.handshake, "function")
  assert.equal(fetchCalls, 0)
})

test("credential reflection filtering applies to stable error codes and allowlisted correlation", async () => {
  const cases = [
    {
      bearer: "abcdefghijklmnop",
      reflected: "remote-abcdefghijklmnop-suffix",
    },
    {
      bearer: "long-sensitive-bearer-value-0123456789",
      reflected: "remote-long-sensitive-bearer-value-0123456789-suffix",
    },
  ]
  for (const item of cases) {
    const { bound } = await bindClient({
      config: { bearerToken: item.bearer },
      responses: [jsonResponse({
        error: item.reflected,
        detail: item.reflected,
        path: null,
      }, 500, {
        "x-request-id": item.reflected,
        "x-correlation-id": item.reflected,
      })],
    })
    const { error, failure } = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
    assert.deepEqual(failure, {
      kind: "http",
      status: 500,
      code: null,
      correlation: {},
      body: REDACTED_VALUE,
    })
    assertNoSecrets([error, failure], item.bearer, OWNER_CREDENTIAL)
  }
})

test("redacted stable-code collisions retain private lifecycle classification", async () => {
  const cases = [
    {
      bearer: "registration_exp",
      code: "registration_expired",
      status: 410,
      kind: "registration-expired",
    },
    {
      bearer: "engine_identity_",
      code: "engine_identity_mismatch",
      status: 409,
      kind: "identity-changed",
    },
    {
      bearer: "drain_recovery_f",
      code: "drain_recovery_failed",
      status: 409,
      kind: "recovery-failed",
    },
  ]
  for (const item of cases) {
    const reflectedCorrelation = `request-${item.bearer}-suffix`
    const { bound } = await bindClient({
      config: { bearerToken: item.bearer },
      responses: [jsonResponse({
        error: item.code,
        detail: reflectedCorrelation,
        path: null,
      }, item.status, {
        "x-request-id": reflectedCorrelation,
        "x-correlation-id": reflectedCorrelation,
      })],
    })
    const { error, failure } = await failureOf(() => bound.renewOwner(ownerLeaseInput()))
    assert.deepEqual(failure, {
      kind: item.kind,
      status: item.status,
      code: null,
      correlation: {},
      body: REDACTED_VALUE,
    })
    assertNoSecrets([error, failure], item.bearer, OWNER_CREDENTIAL)
  }
})
