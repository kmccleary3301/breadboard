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
  assert.deepEqual(Object.keys(client).sort(), [
    "approveSession", "artifactsSession", "cancelSession", "createHarness", "describeSystem",
    "eventsSession", "explainHarness", "getArtifact", "getHarness", "getHarnessLock",
    "getIntegration", "getSession", "healthSystem", "listArtifact", "listHarness",
    "listIntegration", "listSession", "lockHarness", "probeIntegration", "resumeSession",
    "schemasSystem", "sendInputSession", "startSession", "updateHarness", "validateHarness",
    "verifyArtifact",
  ].sort())
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
    () => client.getSession("session-1"), () => client.sendInputSession("session-1", "continue", "input-key"),
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
