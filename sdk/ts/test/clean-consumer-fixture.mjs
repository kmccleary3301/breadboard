import assert from "node:assert/strict"
import { execFileSync, spawnSync } from "node:child_process"
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

const root = resolve(new URL("..", import.meta.url).pathname)
const temp = mkdtempSync(join(tmpdir(), "breadboard-sdk-consumer-"))
const artifact = join(temp, "artifact")
try {
  execFileSync("node", [join(root, "scripts/pack-canonical.mjs"), artifact], { cwd: root, stdio: "inherit" })
  const tarball = join(artifact, "breadboard-sdk-0.3.0.tgz")
  const repeatArtifact = join(temp, "artifact-repeat")
  execFileSync("node", [join(root, "scripts/pack-canonical.mjs"), repeatArtifact], { cwd: root, stdio: "inherit" })
  const repeatTarball = join(repeatArtifact, "breadboard-sdk-0.3.0.tgz")
  for (const suffix of ["", ".sha256", ".installed-files.json", ".engine-api-range"]) {
    assert.deepEqual(
      readFileSync(`${tarball}${suffix}`),
      readFileSync(`${repeatTarball}${suffix}`),
      `canonical package drift for ${suffix || "tarball"}`,
    )
  }
  const inventory = JSON.parse(readFileSync(`${tarball}.installed-files.json`, "utf8"))
  assert.equal(inventory.package, "@breadboard/sdk")
  assert.equal(inventory.version, "0.3.0")
  const installedPaths = inventory.files.map((file) => file.path)
  assert.equal(installedPaths.length, 28)
  assert.ok(installedPaths.includes("dist/public-client.js"))
  assert.ok(installedPaths.includes("dist/transport-security.js"))
  assert.equal(readFileSync(`${tarball}.engine-api-range`, "utf8"), ">=0.1.0 <0.4.0\n")
  writeFileSync(join(temp, "package.json"), JSON.stringify({ type: "module", dependencies: { "@breadboard/sdk": `file:${tarball}` }, devDependencies: { typescript: "^5.5.4" }, scripts: { test: "node consumer.mjs" } }, null, 2))
  writeFileSync(join(temp, "consumer.ts"), `
import {
  ApiError,
  createBreadboardClient,
  streamSessionEvents,
  type BreadboardClient,
  type Problem,
  type PublicHarnessCreateRequest,
  type PublicHarnessUpdateRequest,
  type PublicResult,
  type PublicSessionApprovalRequest,
  type PublicSessionCancelRequest,
  type PublicSessionDecision,
  type PublicSessionInputRequest,
  type PublicSessionStartRequest,
  type SessionEvent,
  type StageOutcome,
} from "@breadboard/sdk"
import {
  type AcquireOwnerInput,
  type AuthCredentialView,
  createInternalBreadboardClient,
  type ReadSessionFileOptions,
  type SessionSummary,
} from "@breadboard/sdk/internal"
const input: AcquireOwnerInput = { ownerCredential: "fixture", expectedOwnerGeneration: 1 }
void input
const readOptions: ReadSessionFileOptions = { mode: "snippet", headLines: 1 }
void readOptions
const client = createBreadboardClient({ baseUrl: "http://fixture.test" })
const internalClient = createInternalBreadboardClient({ baseUrl: "http://fixture.test" })
void internalClient.resolveModelRoles
void streamSessionEvents
void ApiError
const problem: Problem = { error_code: "fixture", message: "fixture" }
const stage: StageOutcome = { stage: "fixture", status: "ok" }
const create: PublicHarnessCreateRequest = {}
const update: PublicHarnessUpdateRequest = { definition: {} }
const start: PublicSessionStartRequest = { lock_id: "lock", task: "task" }
const sessionInput: PublicSessionInputRequest = { content: "continue" }
const approval: PublicSessionApprovalRequest = { request_id: "request", decision: "allow" }
const cancel: PublicSessionCancelRequest = {}
const decision: PublicSessionDecision = "allow"
const result: PublicResult = {
  schema_version: "bb.cli.result.v1",
  ok: true,
  status: "ok",
  command: [],
  record_refs: [],
  hashes: {},
  stage_outcomes: [stage],
  warnings: [],
  next_actions: [],
  error: problem,
  exit_code: 0,
  data: {},
}
type CatalogMethod =
  | "describeSystem" | "healthSystem" | "schemasSystem"
  | "createHarness" | "listHarness" | "getHarness" | "updateHarness"
  | "validateHarness" | "explainHarness" | "lockHarness" | "getHarnessLock"
  | "listIntegration" | "getIntegration" | "probeIntegration"
  | "listArtifact" | "getArtifact" | "verifyArtifact"
  | "startSession" | "listSession" | "getSessionResult" | "sendInputSession"
  | "approveSession" | "resumeSession" | "cancelSession" | "eventsSession" | "artifactsSession"
const catalog: Pick<BreadboardClient, CatalogMethod> = client
const started: Promise<PublicResult> = client.startSession(start)
const updated: Promise<PublicResult> = client.updateHarness("harness", update.definition)
const sent: Promise<PublicResult> = client.sendInputSession("session", sessionInput.content)
const approved: Promise<PublicResult> = client.approveSession("session", approval.request_id, approval.decision)
const canceled: Promise<PublicResult> = client.cancelSession("session", cancel.reason)
const listed: Promise<PublicResult> = client.listArtifact()
const read: Promise<PublicResult> = client.getSessionResult("session")
const summary: Promise<SessionSummary> = internalClient.getSession("session")
const events: AsyncGenerator<SessionEvent, void, void> = client.eventsSession("session")
void create
void catalog
void started
void updated
void sent
void approved
void canceled
void decision
void result
void listed
void read
void summary
void events
const credential: AuthCredentialView = {
  account_id: "a",
  credential_id: "c",
  provider_id: "p",
  auth_scheme_id: "oauth2",
  label: "fixture",
  status: "active",
  secret_version: 1,
  created_at_ms: 1,
  updated_at_ms: 1,
  refresh_state: { status: "failed", retry_not_before_ms: null },
}
const refreshStatus: string = credential.refresh_state?.status ?? "idle"
void refreshStatus
const event: SessionEvent = {
  schema_version: "bb.public_session_event.v1",
  event_id: "session:s:1",
  seq: 1,
  timestamp: "2026-08-31T10:00:01Z",
  work_item_id: null,
  parent_work_item_id: null,
  attempt_id: null,
  session_id: "s",
  span_id: null,
  visibility: {
    model_visible: true,
    provider_visible: true,
    host_visible: true,
    redaction_state: "none",
  },
  kind: "session.completed",
  payload: { outcome: "completed", summary: "consumer fixture" },
  payload_schema_version: "bb.payload.product_session.lifecycle.v1",
}
void event
`)
  writeFileSync(join(temp, "tsconfig.json"), JSON.stringify({ compilerOptions: { target: "ES2022", module: "NodeNext", moduleResolution: "NodeNext", strict: true, noEmit: true }, files: ["consumer.ts"] }, null, 2))
  writeFileSync(join(temp, "consumer.mjs"), `
import assert from "node:assert/strict"
import { createBreadboardClient, streamSessionEvents } from "@breadboard/sdk"
import { createInternalBreadboardClient } from "@breadboard/sdk/internal"

let calls = []
globalThis.fetch = async (input, init) => {
  calls.push([String(input), init])
  return new Response(JSON.stringify({ ok: true, role: "builder" }), {
    headers: { "content-type": "application/json" },
  })
}
const client = createBreadboardClient({
  baseUrl: "https://fixture.test",
  authToken: "fixture-token",
})
const internalClient = createInternalBreadboardClient({
  baseUrl: "https://fixture.test",
  authToken: "fixture-token",
})
await internalClient.resolveModelRoles({ model_roles: { builder: "fixture" } })
assert.equal(calls[0][1].headers.Authorization, "Bearer fixture-token")

const expected = {
  schema_version: "bb.public_session_event.v1",
  event_id: "session:s:1",
  seq: 1,
  timestamp: "2026-08-31T10:00:01Z",
  work_item_id: null,
  parent_work_item_id: null,
  attempt_id: null,
  session_id: "s",
  span_id: null,
  visibility: {
    model_visible: true,
    provider_visible: true,
    host_visible: true,
    redaction_state: "none",
  },
  kind: "session.completed",
  payload: { outcome: "completed", summary: "consumer fixture" },
  payload_schema_version: "bb.payload.product_session.lifecycle.v1",
}
calls = []
globalThis.fetch = async (_input, init) => {
  assert.equal(init.headers.Authorization, "Bearer fixture-token")
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("id: 1\\ndata: " + JSON.stringify(expected) + "\\n\\n"))
      controller.close()
    },
  }), { headers: { "content-type": "text/event-stream" } })
}
const events = []
for await (const event of streamSessionEvents("s", {
  config: { baseUrl: "https://fixture.test", authToken: "fixture-token" },
})) events.push(event)
assert.deepEqual(events, [expected])
console.log("clean consumer fixture passed")
`)
  execFileSync("npm", ["install", "--ignore-scripts", "--no-audit", "--no-fund"], { cwd: temp, stdio: "inherit" })
  const typecheck = spawnSync(join(root, "node_modules/.bin/tsc"), ["-p", join(temp, "tsconfig.json")], { cwd: temp, encoding: "utf8" }); process.stdout.write(typecheck.stdout); process.stderr.write(typecheck.stderr); if (typecheck.status !== 0) process.exit(typecheck.status ?? 1)
  execFileSync("npm", ["test"], { cwd: temp, stdio: "inherit" })
} finally { rmSync(temp, { recursive: true, force: true }) }
