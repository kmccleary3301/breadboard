import { afterEach, describe, expect, it, vi } from "vitest"
import { createApiClient, type PublicActionId } from "../client.js"

const result = {
  schema_version: "bb.cli.result.v1",
  ok: true,
  status: "ok",
  command: [],
  record_refs: [],
  hashes: {},
  stage_outcomes: [],
  warnings: [],
  next_actions: [],
  error: null,
  exit_code: 0,
  data: {},
}

describe("public candidate actions", () => {
  afterEach(() => vi.restoreAllMocks())

  it("routes every candidate action through the canonical HTTP surface", async () => {
    const requests: Array<[string | undefined, string]> = []
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      requests.push([init?.method, new URL(String(input)).pathname])
      return new Response(JSON.stringify(result), { headers: { "content-type": "application/json" } })
    })
    const client = createApiClient({ baseUrl: "http://breadboard.test:9099" })
    const calls: Array<[PublicActionId, Record<string, unknown>?]> = [
      ["public.system.describe"], ["public.system.health"], ["public.system.schemas"],
      ["public.harness.create"], ["public.harness.list"], ["public.harness.get", { harness_id: "bundles/main.yaml" }],
      ["public.harness.update", { harness_id: "bundles/main.yaml", definition: {} }],
      ["public.harness.validate", { harness_id: "bundles/main.yaml" }], ["public.harness.explain", { harness_id: "bundles/main.yaml" }],
      ["public.harness.lock", { harness_id: "bundles/main.yaml" }], ["public.harness_lock.get", { lock_id: "locks/main.json" }],
      ["public.integration.list"], ["public.integration.get", { integration_id: "fixture.provider" }],
      ["public.integration.probe", { integration_id: "fixture.provider", idempotency_key: "probe" }],
      ["public.artifact.list"], ["public.artifact.get", { artifact_id: "sha256:abc" }],
      ["public.artifact.verify", { artifact_id: "sha256:abc" }],
      ["public.session.start", { lock_id: "locks/main.json", task: "run", idempotency_key: "start" }],
      ["public.session.list"], ["public.session.get", { session_id: "session-1" }],
      ["public.session.send_input", { session_id: "session-1", content: "continue", idempotency_key: "input" }],
      ["public.session.approve", { session_id: "session-1", request_id: "approval-1", decision: "allow", idempotency_key: "approve" }],
      ["public.session.resume", { session_id: "session-1", idempotency_key: "resume" }],
      ["public.session.cancel", { session_id: "session-1", idempotency_key: "cancel" }],
      ["public.session.artifacts", { session_id: "session-1" }],
    ]
    for (const [actionId, input] of calls) await expect(client.invokePublicAction(actionId, input)).resolves.toEqual(result)
    const eventStream = await client.invokePublicAction("public.session.events", { session_id: "session-1", resume_token: 2 })
    expect(typeof (eventStream as AsyncIterable<unknown>)[Symbol.asyncIterator]).toBe("function")
    expect(requests.map((row) => row[1])).toEqual([
      "/v1/system", "/v1/health", "/v1/schemas", "/v1/harnesses", "/v1/harnesses",
      "/v1/harnesses/bundles/main.yaml", "/v1/harnesses/bundles/main.yaml", "/v1/harnesses/bundles/main.yaml/validate",
      "/v1/harnesses/bundles/main.yaml/explain", "/v1/harnesses/bundles/main.yaml/lock", "/v1/harness-locks/locks/main.json",
      "/v1/integrations", "/v1/integrations/fixture.provider", "/v1/integrations/fixture.provider/probe",
      "/v1/artifacts", "/v1/artifacts/sha256%3Aabc", "/v1/artifacts/sha256%3Aabc/verify",
      "/v1/sessions", "/v1/sessions", "/v1/sessions/session-1", "/v1/sessions/session-1/input",
      "/v1/sessions/session-1/approve", "/v1/sessions/session-1/resume", "/v1/sessions/session-1/cancel",
      "/v1/sessions/session-1/artifacts",
    ])
  })

  it("omits absent optional event cursor query parameters", async () => {
    let requestedUrl: URL | undefined
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      requestedUrl = new URL(String(input))
      return new Response("", { headers: { "content-type": "text/event-stream" } })
    })
    const client = createApiClient({ baseUrl: "http://breadboard.test:9099" })
    const stream = await client.invokePublicAction("public.session.events", { session_id: "session-1" })

    await (stream as AsyncGenerator<unknown>).next()

    expect(requestedUrl?.searchParams.has("resume_token")).toBe(false)
    expect(requestedUrl?.searchParams.has("limit")).toBe(false)
  })
})
