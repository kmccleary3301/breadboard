import assert from "node:assert/strict"
import test from "node:test"

import { openEventStream, streamSessionEvents } from "../dist/stream.js"
import { ApiError } from "../dist/client.js"
const payloadSchemaVersion = (kind) => ({
  assistant_message: "bb.payload.message.assistant.v1",
  tool_call: "bb.payload.tool.called.v1",
  tool_result: "bb.payload.tool.completed.v1",
})[kind] ?? "bb.payload.product_session.lifecycle.v1"

const eventEnvelope = (sessionId, seq, kind, payload) => ({
  schema_version: "bb.public_session_event.v1",
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
  payload_schema_version: payloadSchemaVersion(kind),
})


test("streamSessionEvents uses the public endpoint and parses the SSE envelope", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestedUrl
  const expected = eventEnvelope(
    "session-123",
    1,
    "assistant_message",
    { metadata: { has_content: true } },
  )
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
    query: { limit: 1 },
  })) {
    events.push(event)
  }

  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-123/events?limit=1",
  )
  assert.deepEqual(events, [expected])
})
test("streamSessionEvents rejects lifecycle kinds with another lifecycle payload shape", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope("session-forged", 1, "session.completed", {})
  const encoded = new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(forged)}\n\n`)
  globalThis.fetch = async () => new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoded)
        controller.close()
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )

  await assert.rejects(
    async () => {
      for await (const _event of streamSessionEvents("session-forged", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected forged lifecycle event")
      }
    },
    /Invalid session event lifecycle payload fields/,
  )
})



test("streamSessionEvents preserves JSON error envelopes", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const expected = {
    schema_version: "bb.cli.result.v1",
    ok: false,
    error: {
      schema_version: "bb.problem.v1",
      error_code: "path_unavailable",
      message: "session is unavailable",
    },
  }
  globalThis.fetch = async () => new Response(JSON.stringify(expected), {
    status: 404,
    headers: { "content-type": "application/json" },
  })

  await assert.rejects(
    async () => {
      for await (const _event of streamSessionEvents("missing", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected stream event")
      }
    },
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 404)
      assert.deepEqual(error.body, expected)
      return true
    },
  )
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
    const value = eventEnvelope(
      "session-secured",
      1,
      "assistant_message",
      { metadata: { has_content: true } },
    )
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
          baseUrl: "https://breadboard.test:9099",
          authToken: async () => "fixture-token",
          fetch: configuredFetch,
        },
      },
    )
  })

  assert.equal(
    requestedUrl,
    "https://breadboard.test:9099/v1/sessions/session-secured/events",
  )
  assert.equal(requestedHeaders.get("Authorization"), "Bearer fixture-token")
  assert.equal(event.event_id, "session:session-secured:1")
  assert.deepEqual(event.payload, { metadata: { has_content: true } })
})

test("authenticated event streams reject remote plaintext before resolving tokens", async () => {
  let tokenResolutions = 0
  let fetches = 0
  const stream = streamSessionEvents("session-secured", {
    config: {
      baseUrl: "http://breadboard.test:9099",
      authToken: async () => {
        tokenResolutions += 1
        return "fixture-token"
      },
      fetch: async () => {
        fetches += 1
        throw new Error("fetch must not run")
      },
    },
  })

  await assert.rejects(() => stream.next(), /requires HTTPS/)
  assert.equal(tokenResolutions, 0)
  assert.equal(fetches, 0)
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
      "assistant_message",
      { metadata: { has_content: true } },
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
        query: { resume_token: 0 },
      },
    )
  })

  assert.deepEqual(
    events.map((event) => event.event_id),
    ["session:session-resume:1", "session:session-resume:2"],
  )
  assert.equal(requestedHeaders[1].get("Last-Event-ID"), "1")
  assert.equal(
    requestedUrls[0],
    "http://breadboard.test:9099/v1/sessions/session-resume/events?resume_token=0",
  )
  assert.equal(
    requestedUrls[1],
    "http://breadboard.test:9099/v1/sessions/session-resume/events?resume_token=1",
  )
})

test("openEventStream stops reconnecting after a terminal event", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestCount = 0
  let addedAbortListeners = 0
  let removedAbortListeners = 0
  const signal = {
    aborted: false,
    addEventListener(type) {
      assert.equal(type, "abort")
      addedAbortListeners += 1
    },
    removeEventListener(type) {
      assert.equal(type, "abort")
      removedAbortListeners += 1
    },
  }
  globalThis.fetch = async () => {
    requestCount += 1
    const value = eventEnvelope(
      "session-terminal",
      1,
      "session.completed",
      { outcome: "completed", summary: "fixture" },
    )
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(value)}\n\n`),
          )
          controller.close()
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    )
  }

  const terminal = await new Promise((resolve, reject) => {
    openEventStream(
      "session-terminal",
      {
        onEvent: resolve,
        onError: reject,
      },
      {
        config: { baseUrl: "http://breadboard.test:9099" },
        initialRetryMs: 0,
        signal,
      },
    )
  })
  assert.equal(terminal.kind, "session.completed")
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(requestCount, 1)
  assert.equal(addedAbortListeners, 1)
  assert.equal(removedAbortListeners, 1)
})

test("openEventStream does not reconnect when a terminal callback throws", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requestCount = 0
  globalThis.fetch = async () => {
    requestCount += 1
    const value = eventEnvelope("session-terminal-error", 1, "session.failed", {
      outcome: "failed",
      error: "expected",
      detail: "callback test",
    })
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(value)}\n\n`),
          )
          controller.close()
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    )
  }

  const observedError = await new Promise((resolve) => {
    openEventStream(
      "session-terminal-error",
      {
        onEvent() {
          throw new Error("consumer callback failed")
        },
        onError: resolve,
      },
      {
        config: { baseUrl: "http://breadboard.test:9099" },
        initialRetryMs: 0,
      },
    )
  })
  assert.equal(observedError.message, "consumer callback failed")
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(requestCount, 1)
})
