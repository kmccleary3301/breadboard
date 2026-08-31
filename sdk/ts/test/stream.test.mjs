import assert from "node:assert/strict"
import test from "node:test"

import { openEventStream, streamSessionEvents } from "../dist/stream.js"
const eventEnvelope = (sessionId, seq, kind, payload) => ({
  schema_version: "bb.kernel_event.v2",
  event_id: `session:${sessionId}:${seq}`,
  seq,
  timestamp: `2026-08-31T10:00:0${seq}Z`,
  work_item_id: null,
  parent_work_item_id: null,
  attempt_id: null,
  session_id: sessionId,
  span_id: null,
  visibility: {
    model_visible: true,
    provider_visible: true,
    host_visible: true,
    redaction_state: "none",
  },
  kind,
  payload,
  payload_schema_version: `bb.payload.${kind}.v1`,
})


test("streamSessionEvents uses the v1 endpoint and parses the SSE envelope", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestedUrl
  const expected = eventEnvelope("session-123", 1, "assistant.message", { text: "hello" })
  const encoded = new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(expected)}\n\n`)
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
  assert.deepEqual(events, [expected])
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
    const value = eventEnvelope("session-secured", 1, "assistant.message.delta", { text: "secured" })
    const encoded = new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(value)}\n\n`)
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
  assert.equal(event.event_id, "session:session-secured:1")
  assert.deepEqual(event.payload, { text: "secured" })
})

test("openEventStream resumes reconnects from the last delivered event", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const requestedUrls = []
  const requestedHeaders = []
  let requestCount = 0
  globalThis.fetch = async (input, init) => {
    requestedUrls.push(String(input))
    requestedHeaders.push(new Headers(init?.headers))
    requestCount += 1
    if (requestCount > 1 && requestedHeaders.at(-1).get("Last-Event-ID") !== String(requestCount - 1)) {
      return new Response("", { status: 422 })
    }
    const value = eventEnvelope(
      "session-resume",
      requestCount,
      "assistant.message.delta",
      { index: requestCount },
    )
    const encoded = new TextEncoder().encode(`id: ${requestCount}\ndata: ${JSON.stringify(value)}\n\n`)
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
    events.map((event) => event.event_id),
    ["session:session-resume:1", "session:session-resume:2"],
  )
  assert.equal(requestedHeaders[1].get("Last-Event-ID"), "1")
  assert.match(requestedUrls[1], /[?&]from_id=1(?:&|$)/)
  assert.doesNotMatch(requestedUrls[1], /[?&]replay=true(?:&|$)/)
})
