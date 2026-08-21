import assert from "node:assert/strict"
import test from "node:test"

import { createBreadboardClient } from "../dist/client.js"


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
  for (const name of ["describeSystem", "healthSystem", "schemasSystem", "createHarness", "listHarness", "getHarness", "updateHarness", "validateHarness", "explainHarness", "lockHarness", "getHarnessLock", "listIntegration", "getIntegration", "probeIntegration", "listArtifact", "getArtifact", "verifyArtifact", "startSession", "listSession", "getSession", "sendInputSession", "approveSession", "resumeSession", "cancelSession", "artifactsSession", "eventsSession"]) assert.equal(typeof client[name], "function")
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
    () => client.invokePublicAction("public.session.get", { session_id: "session-1" }), () => client.sendInputSession("session-1", "continue", "input-key"),
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
    ["POST", "/v1/sessions"], ["GET", "/v1/sessions"], ["GET", "/v1/sessions/session-1"],
    ["POST", "/v1/sessions/session-1/input"], ["POST", "/v1/sessions/session-1/approve"],
    ["POST", "/v1/sessions/session-1/resume"], ["POST", "/v1/sessions/session-1/cancel"],
    ["GET", "/v1/sessions/session-1/artifacts"],
  ])
  assert.deepEqual(requests.filter((row) => row[2]).map((row) => row[2]), ["probe-key", "start-key", "input-key", "approval-key", "resume-key", "cancel-key"])
})

test("session readers unwrap the canonical public result", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const summary = { session_id: "session-1", status: "completed", event_count: 2 }
  globalThis.fetch = async (input) => {
    const path = new URL(String(input)).pathname
    const data = path.endsWith("/session-1") ? { session: summary } : { sessions: [summary], count: 1 }
    return new Response(JSON.stringify({ schema_version: "bb.cli.result.v1", ok: true, status: "ok", command: [], record_refs: [], hashes: {}, stage_outcomes: [], warnings: [], next_actions: [], error: null, exit_code: 0, data }), { headers: { "content-type": "application/json" } })
  }
  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  assert.deepEqual(await client.listSessions(), [summary])
  assert.deepEqual(await client.getSession("session-1"), summary)
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
