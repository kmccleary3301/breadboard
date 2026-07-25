import assert from "node:assert/strict"
import test from "node:test"

import { createBreadboardClient } from "../dist/client.js"

test("session, file, catalog, and health calls use their exact public URLs", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const requests = []
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), method: init?.method })
    if (init?.method === "DELETE") {
      return new Response(null, { status: 204 })
    }
    return new Response(JSON.stringify({}), {
      headers: { "content-type": "application/json" },
    })
  }

  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  await client.health()
  await client.createSession({ config_path: "configs/agent.yaml", task: "repair" })
  await client.listSessions()
  await client.getSession("session-123")
  await client.postInput("session-123", { content: "continue", client_message_id: "message-123" })
  await client.postCommand("session-123", { command: "stop" })
  await client.deleteSession("session-123")
  await client.readSessionRecords("session-123")
  await client.listSessionFiles("session-123", "logs")
  await client.readSessionFile("session-123", "logs/run.txt")
  await client.getModelCatalog("configs/team model.yaml")
  await client.getSkillsCatalog("session-123")
  await client.getCtreeSnapshot("session-123")

  assert.deepEqual(
    requests.map(({ url, method }) => [method, url]),
    [
      ["GET", "http://breadboard.test:9099/health"],
      ["POST", "http://breadboard.test:9099/v1/sessions"],
      ["GET", "http://breadboard.test:9099/v1/sessions"],
      ["GET", "http://breadboard.test:9099/v1/sessions/session-123"],
      ["POST", "http://breadboard.test:9099/v1/sessions/session-123/input"],
      ["POST", "http://breadboard.test:9099/v1/sessions/session-123/command"],
      ["DELETE", "http://breadboard.test:9099/v1/sessions/session-123"],
      ["GET", "http://breadboard.test:9099/v1/sessions/session-123/records"],
      ["GET", "http://breadboard.test:9099/v1/sessions/session-123/files?path=logs"],
      [
        "GET",
        "http://breadboard.test:9099/v1/sessions/session-123/files/content?path=logs%2Frun.txt&mode=cat",
      ],
      ["GET", "http://breadboard.test:9099/v1/models?config_path=configs%2Fteam+model.yaml"],
      ["GET", "http://breadboard.test:9099/v1/sessions/session-123/skills"],
      ["GET", "http://breadboard.test:9099/v1/sessions/session-123/ctrees"],
    ],
  )
})

test("features and provider auth calls use an injected fetch with typed JSON results", async () => {
  const requests = []
  const featuresResponse = {
    status: "ok",
    extensions: {
      atp: { enabled: true, mounted: true },
      evolake: { enabled: false, mounted: false },
    },
    atp: {
      enabled: true,
      service_initialized: false,
      runtime_capabilities: { probe: "available" },
    },
    metadata: { mounted_extensions: ["atp"] },
  }
  const statusResponse = {
    attached: [
      {
        provider_id: "openai",
        alias: "primary",
        has_api_key: true,
        header_keys: ["Authorization"],
        base_url: null,
        routing_keys: ["region"],
        issued_at_ms: 1,
        expires_at_ms: 2_000,
        expires_in_ms: 1_999,
        is_subscription_plan: false,
        required_profile: {
          profile_id: "profile-v1",
          conformance_hash: "hash-v1",
          locked_json_pointers: [],
        },
      },
    ],
  }
  const attachPayload = {
    material: {
      provider_id: "openai",
      alias: "primary",
      headers: { "X-Request-Mode": "test" },
      base_url: "https://provider.test",
      routing: { region: "us" },
      ttl_seconds: 60,
      is_subscription_plan: false,
    },
    required_profile: {
      profile_id: "profile-v1",
      conformance_hash: "hash-v1",
      locked_json_pointers: ["/providers/openai"],
    },
    config_path: "configs/agent.yaml",
    overrides: { "providers.openai.model": "gpt-test" },
  }
  const detachPayload = { provider_id: "openai", alias: "primary" }
  const injectedFetch = async (input, init) => {
    const url = new URL(String(input))
    requests.push({ method: init?.method, url: url.toString(), body: init?.body })
    let payload
    if (url.pathname === "/v1/features") {
      payload = featuresResponse
    } else if (url.pathname === "/v1/provider-auth/status") {
      payload = statusResponse
    } else if (url.pathname === "/v1/provider-auth/attach") {
      payload = { ok: true, detail: { attached: true } }
    } else if (url.pathname === "/v1/provider-auth/detach") {
      payload = { ok: true }
    } else {
      throw new Error(`Unexpected request path: ${url.pathname}`)
    }
    return new Response(JSON.stringify(payload), {
      headers: { "content-type": "application/json" },
    })
  }

  const client = createBreadboardClient({
    baseUrl: "http://breadboard.test:9099",
    fetch: injectedFetch,
  })
  const features = await client.getFeatures()
  const status = await client.getProviderAuthStatus()
  const attached = await client.attachProviderAuth(attachPayload)
  const detached = await client.detachProviderAuth(detachPayload)

  assert.deepEqual(features, featuresResponse)
  assert.deepEqual(status, statusResponse)
  assert.deepEqual(attached, { ok: true, detail: { attached: true } })
  assert.deepEqual(detached, { ok: true })
  assert.deepEqual(
    requests.map(({ method, url }) => [method, url]),
    [
      ["GET", "http://breadboard.test:9099/v1/features"],
      ["GET", "http://breadboard.test:9099/v1/provider-auth/status"],
      ["POST", "http://breadboard.test:9099/v1/provider-auth/attach"],
      ["POST", "http://breadboard.test:9099/v1/provider-auth/detach"],
    ],
  )
  assert.deepEqual(JSON.parse(requests[2].body), attachPayload)
  assert.deepEqual(JSON.parse(requests[3].body), detachPayload)
})
