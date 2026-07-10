import assert from "node:assert/strict"
import test from "node:test"

import { createBreadboardClient } from "../dist/client.js"

test("readSessionFile requests the v1 file-content endpoint", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestedUrl
  globalThis.fetch = async (input) => {
    requestedUrl = String(input)
    return new Response(
      JSON.stringify({ path: "logs/run.txt", content: "ok", truncated: false }),
      { headers: { "content-type": "application/json" } },
    )
  }

  const client = createBreadboardClient({ baseUrl: "http://breadboard.test:9099" })
  await client.readSessionFile("session-123", "logs/run.txt")

  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-123/files/content?path=logs%2Frun.txt&mode=cat",
  )
})
