import assert from "node:assert/strict"
import test from "node:test"

import { openEventStream, streamSessionEvents } from "../dist/stream.js"
import { ApiError } from "../dist/client.js"
const payloadSchemaVersion = (kind) => ({
  annotation: "bb.payload.product_session.annotation.v1",
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
  expected.timestamp = "2026-08-31T10:00:01.123456789Z"
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

test("streamSessionEvents accepts RFC3339 case and leap-second variants", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const timestamps = [
    "2016-12-31T23:59:60Z",
    "2016-12-31T15:59:60-08:00",
    "2026-08-31t10:00:01.123z",
  ]
  const encoded = new TextEncoder().encode(
    timestamps.map((timestamp, index) => {
      const sequence = index + 1
      const event = eventEnvelope(
        "session-leap-second",
        sequence,
        "assistant_message",
        { metadata: { has_content: true } },
      )
      event.timestamp = timestamp
      return `id: ${sequence}\ndata: ${JSON.stringify(event)}\n\n`
    }).join(""),
  )
  globalThis.fetch = async () => new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoded)
        controller.close()
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )

  const events = []
  for await (const event of streamSessionEvents("session-leap-second", {
    config: { baseUrl: "http://breadboard.test:9099" },
  })) {
    events.push(event)
  }
  assert.deepEqual(events.map((event) => event.timestamp), timestamps)
})

test("streamSessionEvents rejects impossible leap seconds", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope(
    "session-invalid-leap-second",
    1,
    "assistant_message",
    { metadata: { has_content: true } },
  )
  forged.timestamp = "2016-11-30T23:59:60Z"
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
      for await (const _event of streamSessionEvents("session-invalid-leap-second", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected invalid leap-second event")
      }
    },
    /Invalid session event timestamp/,
  )
})


test("streamSessionEvents rejects a non-RFC3339 timestamp", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope("session-timestamp", 1, "session.started", {
    effective_lock_hash: "sha256:" + "a".repeat(64),
    task_hash: "sha256:" + "b".repeat(64),
  })
  forged.timestamp = "not-a-time"
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
      for await (const _event of streamSessionEvents("session-timestamp", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected invalid timestamp event")
      }
    },
    /Invalid session event timestamp/,
  )
})

test("streamSessionEvents preserves snapshot and compatibility query keys", async (t) => {
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
    query: {
      limit: 1,
      follow: false,
      schema: "legacy",
      include_legacy: true,
      replay: 2,
    },
  })) {
    events.push(event)
  }

  assert.equal(
    requestedUrl,
    "http://breadboard.test:9099/v1/sessions/session-123/events?limit=1&follow=false&schema=legacy&include_legacy=true&replay=2",
  )
  assert.deepEqual(events, [expected])
})

test("openEventStream snapshot retains annotations after settlement", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const completed = eventEnvelope("archive", 1, "session.completed", {
    outcome: "completed", summary: "done",
  })
  const annotation = eventEnvelope("archive", 2, "annotation", {
    annotation_id: "label-a", message_id: "message-a", trajectory_id: "trajectory-a",
    label: "preferred", author: "reviewer", generation: "sha256:" + "a".repeat(64),
  })
  annotation.visibility = {
    host_visible: true, model_visible: false, provider_visible: false, redaction_state: "none",
  }
  const body = [completed, annotation]
    .map((event) => `id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`).join("")
  globalThis.fetch = async () => new Response(body, {
    headers: { "content-type": "text/event-stream" },
  })
  const observed = []
  await new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error("snapshot lost its post-run label")), 1000)
    const handle = openEventStream("archive", {
      onEvent(event) {
        observed.push(event)
        if (event.kind === "annotation") resolve()
      },
      onError: reject,
    }, {
      config: { baseUrl: "http://127.0.0.1" },
      query: { follow: false },
    })
    t.after(() => { clearTimeout(deadline); handle.close() })
  })
  assert.deepEqual(observed.map((event) => event.kind), ["session.completed", "annotation"])
  assert.equal(observed[1].payload.annotation_id, "label-a")
})

test("openEventStream does not reconnect a follow=false snapshot", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  let requests = 0
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
    requests += 1
    return new Response("", { headers: { "content-type": "text/event-stream" } })
  }

  let handle
  const opened = new Promise((resolve) => {
    handle = openEventStream(
      "session-snapshot",
      {
        onEvent() {
          assert.fail("unexpected event")
        },
        onOpen: resolve,
      },
      {
        config: { baseUrl: "http://breadboard.test:9099" },
        query: { follow: false },
        signal,
        initialRetryMs: 1,
        maxRetryMs: 1,
      },
    )
  })
  t.after(() => handle?.close())

  await opened
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(requests, 1)
  assert.equal(addedAbortListeners, 1)
  assert.equal(removedAbortListeners, 1)
})
test("openEventStream closes a failed follow=false handle", async () => {
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

  await new Promise((resolve) => {
    openEventStream(
      "session-snapshot",
      {
        onEvent() {
          assert.fail("unexpected event")
        },
        onError: resolve,
      },
      {
        config: {
          baseUrl: "http://breadboard.test:9099",
          fetch: async () => new Response("", { status: 500 }),
        },
        query: { follow: false },
        signal,
      },
    )
  })

  assert.equal(addedAbortListeners, 1)
  assert.equal(removedAbortListeners, 1)
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
test("streamSessionEvents decodes annotations, lineage, and paired assistant identities", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const annotation = eventEnvelope("session-identities", 1, "annotation", {
    annotation_id: "annotation-1",
    message_id: "message-1",
    trajectory_id: "trajectory-1",
    label: "verified",
    author: "operator",
    generation: "sha256:" + "a".repeat(64),
  })
  annotation.visibility = {
    model_visible: false,
    provider_visible: false,
    host_visible: true,
    redaction_state: "none",
  }
  const started = eventEnvelope("session-identities", 2, "session.started", {
    effective_lock_hash: "sha256:" + "b".repeat(64),
    task_hash: "sha256:" + "c".repeat(64),
    lineage: {
      parent_session_id: "parent-session",
      root_session_id: "root-session",
      parent_work_item_id: "parent-work-item",
      child_work_item_id: "child-work-item",
    },
  })
  started.work_item_id = "child-work-item"
  started.parent_work_item_id = "parent-work-item"
  const assistant = eventEnvelope("session-identities", 3, "assistant_message", {
    metadata: { has_content: true },
    message_id: "message-1",
    trajectory_id: "trajectory-1",
  })
  const encoded = new TextEncoder().encode([
    `id: 1\ndata: ${JSON.stringify(annotation)}\n\n`,
    `id: 2\ndata: ${JSON.stringify(started)}\n\n`,
    `id: 3\ndata: ${JSON.stringify(assistant)}\n\n`,
  ].join(""))
  globalThis.fetch = async () => new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoded)
        controller.close()
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  )

  const events = []
  for await (const event of streamSessionEvents("session-identities", {
    config: { baseUrl: "http://breadboard.test:9099" },
  })) {
    events.push(event)
  }

  assert.equal(events[0].kind, "annotation")
  assert.deepEqual(events[0].visibility, {
    model_visible: false,
    provider_visible: false,
    host_visible: true,
    redaction_state: "none",
  })
  assert.deepEqual(events[0].payload, annotation.payload)
  assert.deepEqual(events[1].payload.lineage, started.payload.lineage)
  assert.deepEqual(events[2].payload, assistant.payload)
})

test("streamSessionEvents rejects partial assistant identities", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope("session-partial-identity", 1, "assistant_message", {
    metadata: { has_content: true },
    message_id: "message-1",
  })
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
      for await (const _event of streamSessionEvents("session-partial-identity", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected partial assistant identity")
      }
    },
    /Invalid session event assistant identity fields/,
  )
})

test("streamSessionEvents rejects non-host annotation visibility", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope("session-annotation-visibility", 1, "annotation", {
    annotation_id: "annotation-1",
    message_id: "message-1",
    trajectory_id: "trajectory-1",
    label: "verified",
    author: "operator",
    generation: "sha256:" + "a".repeat(64),
  })
  forged.visibility.provider_visible = true
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
      for await (const _event of streamSessionEvents("session-annotation-visibility", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected provider-visible annotation")
      }
    },
    /Invalid session event annotation visibility/,
  )
})
test("streamSessionEvents rejects contradictory lineage correlations", async (t) => {
  const originalFetch = globalThis.fetch
  t.after(() => {
    globalThis.fetch = originalFetch
  })

  const forged = eventEnvelope("session-lineage-mismatch", 1, "session.started", {
    effective_lock_hash: "sha256:" + "a".repeat(64),
    task_hash: "sha256:" + "b".repeat(64),
    lineage: {
      parent_session_id: "parent-session",
      root_session_id: "root-session",
      parent_work_item_id: "parent-work-item",
      child_work_item_id: "child-work-item",
    },
  })
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
      for await (const _event of streamSessionEvents("session-lineage-mismatch", {
        config: { baseUrl: "http://breadboard.test:9099" },
      })) {
        assert.fail("unexpected lineage mismatch")
      }
    },
    /Invalid session event lineage correlations/,
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

test("authenticated event streams reject remote plaintext after resolving tokens", async () => {
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
  assert.equal(tokenResolutions, 1)
  assert.equal(fetches, 0)
})

test("an async empty token allows an unauthenticated remote plaintext event stream", async () => {
  let tokenResolutions = 0
  let requestedHeaders
  const stream = streamSessionEvents("session-public", {
    config: {
      baseUrl: "http://breadboard.test:9099",
      authToken: async () => {
        tokenResolutions += 1
        return undefined
      },
      fetch: async (_input, init) => {
        requestedHeaders = new Headers(init?.headers)
        return new Response("", {
          headers: { "content-type": "text/event-stream" },
        })
      },
    },
  })

  assert.deepEqual(await stream.next(), { value: undefined, done: true })
  assert.equal(tokenResolutions, 1)
  assert.equal(requestedHeaders.get("Authorization"), null)
})


test("aborting pending token resolution ends the stream before transport policy", async () => {
  let releaseToken
  let fetches = 0
  const controller = new AbortController()
  const stream = streamSessionEvents("session-aborted", {
    signal: controller.signal,
    config: {
      baseUrl: "http://breadboard.test:9099",
      authToken: () => new Promise((resolve) => {
        releaseToken = resolve
      }),
      fetch: async () => {
        fetches += 1
        throw new Error("fetch must not run")
      },
    },
  })

  const next = stream.next()
  controller.abort()
  releaseToken("fixture-token")

  assert.deepEqual(await next, { value: undefined, done: true })
  assert.equal(fetches, 0)
})

test("aborting pending token rejection suppresses the late provider error", async () => {
  let rejectToken
  let fetches = 0
  const controller = new AbortController()
  const stream = streamSessionEvents("session-aborted", {
    signal: controller.signal,
    config: {
      baseUrl: "https://breadboard.test:9099",
      authToken: () => new Promise((_resolve, reject) => {
        rejectToken = reject
      }),
      fetch: async () => {
        fetches += 1
        throw new Error("fetch must not run")
      },
    },
  })

  const next = stream.next()
  controller.abort()
  rejectToken(new Error("late auth failure"))

  assert.deepEqual(await next, { value: undefined, done: true })
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
