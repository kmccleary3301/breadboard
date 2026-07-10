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
  await client.postInput("session-123", { content: "continue" })
  await client.postCommand("session-123", { command: "stop" })
  await client.deleteSession("session-123")
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
