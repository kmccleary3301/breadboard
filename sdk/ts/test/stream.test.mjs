import assert from "node:assert/strict"
import test from "node:test"

import { streamSessionEvents } from "../dist/stream.js"

test("streamSessionEvents uses the v1 endpoint and parses the SSE envelope", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestedUrl
  const encoded = new TextEncoder().encode(
    'id: wire-42\ndata: {"event":"assistant_message","sessionId":"session-123","data":{"text":"hello"},"timestamp":12}\n\n',
  )
  globalThis.fetch = async (input) => {
    requestedUrl = String(input)
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoded)
          controller.close()
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    )
  }

  const events = []
  for await (const event of streamSessionEvents("session-123", {
    config: { baseUrl: "http://breadboard.test:9099" },
    query: { replay: true, limit: 1 },
  })) {
    events.push(event)
  }

  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-123/events?replay=true&limit=1",
  )
  assert.equal(events.length, 1)
  assert.equal(events[0].id, "wire-42")
  assert.equal(events[0].type, "assistant_message")
  assert.equal(events[0].session_id, "session-123")
  assert.deepEqual(events[0].payload, { text: "hello" })
})
