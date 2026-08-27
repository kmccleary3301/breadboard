import assert from "node:assert/strict"
import test from "node:test"

import { openEventStream, streamSessionEvents } from "../dist/stream.js"

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
    "http://breadboard.test:9099/v1/sessions/session-123/events?replay=true&limit=1&schema=2&include_legacy=false",
  )
  assert.equal(events.length, 1)
  assert.equal(events[0].id, "wire-42")
  assert.equal(events[0].type, "assistant_message")
  assert.equal(events[0].session_id, "session-123")
  assert.deepEqual(events[0].payload, { text: "hello" })
})

test("openEventStream reuses the authenticated fetch transport", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })
  globalThis.fetch = async () => { throw new Error("global fetch used") }

  let requestedUrl
  let requestedHeaders
  const configuredFetch = async (input, init) => {
    requestedUrl = String(input)
    requestedHeaders = new Headers(init?.headers)
    const encoded = new TextEncoder().encode(
      'id: wire-43\ndata: {"type":"assistant.message.delta","payload":{"text":"secured"}}\n\n',
    )
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

  let handle
  const event = await new Promise((resolve, reject) => {
    handle = openEventStream(
      "session-secured",
      {
        onEvent(value) {
          handle.close()
          resolve(value)
        },
        onError: reject,
      },
      {
        config: {
          baseUrl: "http://breadboard.test:9099",
          authToken: async () => "fixture-token",
          fetch: configuredFetch,
        },
      },
    )
  })

  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-secured/events?schema=2&include_legacy=false&replay=true",
  )
  assert.equal(requestedHeaders.get("Authorization"), "Bearer fixture-token")
  assert.equal(event.id, "wire-43")
  assert.deepEqual(event.payload, { text: "secured" })
})

test("openEventStream resumes reconnects from the last delivered event", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const requestedUrls = []
  let requestCount = 0
  globalThis.fetch = async (input) => {
    requestedUrls.push(String(input))
    requestCount += 1
    const encoded = new TextEncoder().encode(
      `id: wire-${requestCount}\ndata: {"type":"assistant.message.delta","payload":{"index":${requestCount}}}\n\n`,
    )
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

  let handle
  const events = await new Promise((resolve, reject) => {
    const received = []
    handle = openEventStream(
      "session-resume",
      {
        onEvent(event) {
          received.push(event)
          if (received.length === 2) {
            handle.close()
            resolve(received)
          }
        },
        onError: reject,
      },
      {
        config: { baseUrl: "http://breadboard.test:9099" },
        initialRetryMs: 0,
      },
    )
  })

  assert.deepEqual(
    events.map((event) => event.id),
    ["wire-1", "wire-2"],
  )
  assert.match(requestedUrls[1], /[?&]from_id=wire-1(?:&|$)/)
  assert.doesNotMatch(requestedUrls[1], /[?&]replay=true(?:&|$)/)
})
