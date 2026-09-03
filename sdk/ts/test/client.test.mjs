import assert from "node:assert/strict"
import test from "node:test"

import { ApiError, createBreadboardClient } from "../dist/client.js"


test("candidate product methods preserve canonical result envelopes and routes", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const requests = []
  const result = { schema_version: "bb.cli.result.v1", ok: true, status: "ok", command: [], record_refs: [], hashes: {}, stage_outcomes: [], warnings: [], next_actions: [], error: null, exit_code: 0, data: {} }
  globalThis.fetch = async (input, init) => {
    requests.push([init?.method, new URL(String(input)).pathname, init?.headers?.["Idempotency-Key"]])
    return new Response(JSON.stringify(result), { headers: { "content-type": "application/json" } })
  }
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  for (const name of ["describeSystem", "healthSystem", "schemasSystem", "createHarness", "listHarness", "getHarness", "updateHarness", "validateHarness", "explainHarness", "lockHarness", "getHarnessLock", "listIntegration", "getIntegration", "probeIntegration", "listArtifact", "getArtifact", "verifyArtifact", "startSession", "listSession", "listSessions", "getSession", "getSessionResult", "sendInputSession", "approveSession", "resumeSession", "cancelSession", "artifactsSession", "eventsSession"]) assert.equal(typeof client[name], "function")
  assert.equal(typeof client.eventsSession, "function")
  const calls = [
    () => client.describeSystem(), () => client.healthSystem(), () => client.schemasSystem(),
    () => client.createHarness(), () => client.listHarness(), () => client.getHarness("bundles/main.yaml"),
    () => client.updateHarness("bundles/main.yaml", {}), () => client.validateHarness("bundles/main.yaml"),
    () => client.explainHarness("bundles/main.yaml"), () => client.lockHarness("bundles/main.yaml"),
    () => client.getHarnessLock("locks/main.json"), () => client.listIntegration(), () => client.getIntegration("fixture.provider"),
    () => client.probeIntegration("fixture.provider", "probe-key"), () => client.listArtifact(),
    () => client.getArtifact("sha256:abc"), () => client.verifyArtifact("sha256:abc"),
    () => client.startSession({ lock_id: "locks/main.json", task: "run" }, "start-key"), () => client.listSession(),
    () => client.sendInputSession("session-1", "continue", "input-key"),
    () => client.approveSession("session-1", "approval-1", "allow", "approval-key"),
    () => client.resumeSession("session-1", "resume-key"), () => client.cancelSession("session-1", "done", "cancel-key"),
    () => client.artifactsSession("session-1"),
  ]
  for (const call of calls) assert.deepEqual(await call(), result)
  assert.deepEqual(requests.map(([method, path]) => [method, path]), [
    ["GET", "/v1/system"], ["GET", "/v1/health"], ["GET", "/v1/schemas"],
    ["POST", "/v1/harnesses"], ["GET", "/v1/harnesses"], ["GET", "/v1/harnesses/bundles/main.yaml"],
    ["PUT", "/v1/harnesses/bundles/main.yaml"], ["POST", "/v1/harnesses/bundles/main.yaml/validate"],
    ["POST", "/v1/harnesses/bundles/main.yaml/explain"], ["POST", "/v1/harnesses/bundles/main.yaml/lock"],
    ["GET", "/v1/harness-locks/locks/main.json"], ["GET", "/v1/integrations"], ["GET", "/v1/integrations/fixture.provider"],
    ["POST", "/v1/integrations/fixture.provider/probe"], ["GET", "/v1/artifacts"],
    ["GET", "/v1/artifacts/sha256%3Aabc"], ["POST", "/v1/artifacts/sha256%3Aabc/verify"],
    ["POST", "/v1/sessions"], ["GET", "/v1/sessions"],
    ["POST", "/v1/sessions/session-1/input"], ["POST", "/v1/sessions/session-1/approve"],
    ["POST", "/v1/sessions/session-1/resume"], ["POST", "/v1/sessions/session-1/cancel"],
    ["GET", "/v1/sessions/session-1/artifacts"],
  ])
  assert.deepEqual(requests.filter((row) => row[2]).map((row) => row[2]), ["probe-key", "start-key", "input-key", "approval-key", "resume-key", "cancel-key"])
})

test("invokePublicAction forwards the session event snapshot query", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  let requestedUrl
  globalThis.fetch = async (input) => {
    requestedUrl = String(input)
    return new Response("", { headers: { "content-type": "text/event-stream" } })
  }
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  const events = await client.invokePublicAction("public.session.events", {
    session_id: "session-snapshot",
    follow: false,
  })
  for await (const _event of events) assert.fail("unexpected event")
  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-snapshot/events?follow=false",
  )
})

test("configured fetch transport handles JSON and attachment requests", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async () => { throw new Error("global fetch used") }
  const requests = []
  const configuredFetch = async (input, init) => {
    const path = new URL(String(input)).pathname
    requests.push({ path, init })
    const payload = path.endsWith("/attachments")
      ? { attachments: [] }
      : { session_id: "session-configured" }
    return new Response(JSON.stringify(payload), {
      headers: { "content-type": "application/json" },
    })
  }
  const client = createBreadboardClient({
    baseUrl: "http://breadboard.test:9099",
    fetch: configuredFetch,
  })

  assert.deepEqual(await client.createSession({}), {
    session_id: "session-configured",
  })
  assert.deepEqual(
    await client.uploadAttachments("session-configured", [{
      base64: btoa("configured"),
      filename: "configured.txt",
      mime: "text/plain",
    }]),
    [],
  )
  assert.deepEqual(
    requests.map(({ path, init }) => [path, init?.method]),
    [
      ["/v1/internal/sessions", "POST"],
      ["/v1/internal/sessions/session-configured/attachments", "POST"],
    ],
  )
  assert.ok(requests[1].init?.body instanceof FormData)
})

test("bearer tokens resolve before rejecting remote plaintext JSON and attachment requests", async () => {
  let tokenResolutions = 0
  let fetches = 0
  const client = createBreadboardClient({
    baseUrl: "http://breadboard.test:9099",
    authToken: async () => {
      tokenResolutions += 1
      return "fixture-token"
    },
    fetch: async () => {
      fetches += 1
      throw new Error("fetch must not run")
    },
  })

  await assert.rejects(() => client.listSession(), /requires HTTPS/)
  await assert.rejects(
    () => client.uploadAttachments("session-id", [{
      base64: btoa("protected"),
      filename: "protected.txt",
      mime: "text/plain",
    }]),
    /requires HTTPS/,
  )
  assert.equal(tokenResolutions, 2)
  assert.equal(fetches, 0)
})

test("an async empty token allows unauthenticated remote plaintext requests", async () => {
  let tokenResolutions = 0
  let requestedHeaders
  const client = createBreadboardClient({
    baseUrl: "http://breadboard.test:9099",
    authToken: async () => {
      tokenResolutions += 1
      return undefined
    },
    fetch: async (_input, init) => {
      requestedHeaders = new Headers(init?.headers)
      return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json" },
      })
    },
  })

  assert.deepEqual(await client.listSession(), { ok: true })
  assert.equal(tokenResolutions, 1)
  assert.equal(requestedHeaders.get("Authorization"), null)
})


test("bearer tokens allow HTTPS and literal loopback HTTP", async () => {
  for (const baseUrl of [
    "https://breadboard.test:9099",
    "http://localhost:9099",
    "http://127.0.0.2:9099",
    "http://[::1]:9099",
  ]) {
    const requests = []
    const client = createBreadboardClient({
      baseUrl,
      authToken: "fixture-token",
      fetch: async (_input, init) => {
        requests.push(init)
        return new Response(JSON.stringify({ ok: true }), {
          headers: { "content-type": "application/json" },
        })
      },
    })

    assert.deepEqual(await client.listSession(), { ok: true })
    assert.equal(requests[0]?.headers?.Authorization, "Bearer fixture-token")
  }
})

test("session readers preserve the established summary and expose an explicit raw envelope", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const summary = { session_id: "session-1", status: "completed", event_count: 2 }
  const envelope = (data) => ({ schema_version: "bb.cli.result.v1", ok: true, status: "ok", command: [], record_refs: [], hashes: {}, stage_outcomes: [], warnings: [], next_actions: [], error: null, exit_code: 0, data })
  globalThis.fetch = async (input) => {
    const path = new URL(String(input)).pathname
    const data = path.endsWith("/session-1") ? { session: summary } : { sessions: [summary], count: 1 }
    return new Response(JSON.stringify(envelope(data)), { headers: { "content-type": "application/json" } })
  }
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  assert.deepEqual(await client.listSessions(), [summary])
  assert.deepEqual(await client.getSession("session-1"), summary)
  assert.deepEqual(await client.getSessionResult("session-1"), envelope({ session: summary }))
})

test("summary session reader rejects canonical envelopes missing the key", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, data: {} }), { headers: { "content-type": "application/json" } })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  await assert.rejects(() => client.getSession("session-1"), /Public result missing data\.session/)
})



test("broker and model-role methods use canonical typed routes", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const requests = []
  const response = { provider_id: "openai", account_id: "bbacct_test", credential_id: "bbcred_test", status: "active" }
  globalThis.fetch = async (input, init) => {
    requests.push([init?.method, new URL(String(input)).pathname, JSON.parse(init?.body ?? "null")])
    return new Response(JSON.stringify(response), { headers: { "content-type": "application/json" } })
  }
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  for (const name of ["listProviders", "listCredentials", "beginLogin", "getLogin", "completeLogin", "cancelLogin", "putApiKey", "logout", "revoke", "resolveModelRoles"]) assert.equal(typeof client[name], "function")
  await client.listProviders()
  await client.listCredentials("openai")
  await client.beginLogin({ provider_id: "openai" })
  await client.getLogin("bblogin_test")
  await client.completeLogin({ login_session_id: "bblogin_test", state: "state" })
  await client.cancelLogin("bblogin_test")
  await client.putApiKey("openai", "main", { api_key: "sk-sdk-canary" })
  await client.logout("bbacct_test")
  await client.revoke("bbcred_test")
  await client.resolveModelRoles({ model_roles: { schema_version: "bb.model_roles.v1" } })
  assert.deepEqual(requests.map(([method, path]) => [method, path]), [
    ["GET", "/v1/auth/providers"], ["GET", "/v1/auth/credentials"],
    ["POST", "/v1/auth/login-sessions"], ["GET", "/v1/auth/login-sessions/bblogin_test"],
    ["POST", "/v1/auth/login-sessions/bblogin_test/complete"], ["DELETE", "/v1/auth/login-sessions/bblogin_test"],
    ["PUT", "/v1/auth/credentials/openai/main/api-key"], ["DELETE", "/v1/auth/credentials/bbacct_test"],
    ["POST", "/v1/auth/credentials/bbcred_test/revoke"], ["POST", "/v1/model-roles/resolve"],
  ])
  assert.equal(requests[6][2].api_key, "sk-sdk-canary")
})

test("DELETE actions preserve JSON response bodies", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const response = { ok: false, detail: { code: "already_terminal" } }
  globalThis.fetch = async () =>
    new Response(JSON.stringify(response), { headers: { "content-type": "application/json" } })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })

  assert.deepEqual(await client.cancelLogin("bblogin_test"), response)
  assert.deepEqual(await client.logout("bbacct_test"), response)
})

test("FastAPI detail produces an actionable API error", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: "credential action forbidden" }), {
      status: 403,
      headers: { "content-type": "application/json" },
    })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })

  await assert.rejects(
    () => client.logout("bbacct_test"),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 403)
      assert.equal(error.message, "credential action forbidden")
      assert.deepEqual(error.body, { detail: "credential action forbidden" })
      return true
    },
  )
})

test("structured error envelope detail remains actionable", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const response = {
    error: "model_role_conflict",
    detail: { role: "planner", effective_role: "builder" },
  }
  globalThis.fetch = async () =>
    new Response(JSON.stringify(response), {
      status: 409,
      headers: { "content-type": "application/json" },
    })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })

  await assert.rejects(
    () => client.resolveModelRoles({ model_roles: { schema_version: "bb.model_roles.v1" } }),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(
        error.message,
        'model_role_conflict: {"role":"planner","effective_role":"builder"}',
      )
      assert.deepEqual(error.body, response)
      return true
    },
  )
})

test("structured FastAPI problem detail preserves its code and message", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const problem = {
    schema_version: "bb.problem.v1",
    error_code: "model_role_conflict",
    message: "requested model role conflicts with the effective lock",
    path: "$.model_roles",
    details: { role: "planner" },
  }
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: problem }), {
      status: 409,
      headers: { "content-type": "application/json" },
    })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })

  await assert.rejects(
    () => client.resolveModelRoles({ model_roles: { schema_version: "bb.model_roles.v1" } }),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 409)
      assert.equal(
        error.message,
        "model_role_conflict: requested model role conflicts with the effective lock",
      )
      assert.deepEqual(error.body, { detail: problem })
      return true
    },
  )
})

test("canonical public result problem remains actionable", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const problem = {
    schema_version: "bb.problem.v1",
    error_code: "invalid_state",
    message: "session is already terminal",
    path: null,
    details: null,
  }
  const response = {
    ok: false,
    command: ["session", "cancel"],
    data: null,
    error: problem,
    hashes: {},
    metadata: {},
  }
  globalThis.fetch = async () =>
    new Response(JSON.stringify(response), {
      status: 409,
      headers: { "content-type": "application/json" },
    })
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })

  await assert.rejects(
    () => client.cancelSession("terminal-session"),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 409)
      assert.equal(error.message, "invalid_state: session is already terminal")
      assert.deepEqual(error.body, response)
      return true
    },
  )
})
