import {
  ApiError,
  createBreadboardClient,
  openEventStream,
  streamSessionEvents,
} from "@breadboard/sdk"
import { createInternalBreadboardClient } from "@breadboard/sdk/internal"

let input = ""
for await (const chunk of process.stdin) input += chunk
const request = JSON.parse(input)
const client = createBreadboardClient({
  baseUrl: request.base_url,
  authToken: request.auth_token,
})

if (request.action_id === "__surface.audit") {
  const internal = createInternalBreadboardClient({ baseUrl: request.base_url })
  let terminalStreamRequests = 0
  let terminalCallbackErrorObserved = false
  let resumeHandle
  const resumeRequests = []
  await new Promise((resolve, reject) => {
    resumeHandle = openEventStream(
      "i6-resume-audit",
      {
        onEvent() {
          if (resumeRequests.length === 2) {
            resumeHandle.close()
            resolve()
          }
        },
        onError: reject,
      },
      {
        config: {
          baseUrl: request.base_url,
          fetch: async (url, init) => {
            const seq = resumeRequests.length + 1
            resumeRequests.push({
              url: String(url),
              last_event_id: new Headers(init?.headers).get("Last-Event-ID"),
            })
            const event = {
              schema_version: "bb.public_session_event.v1",
              event_id: `session:i6-resume-audit:${seq}`,
              seq,
              timestamp: `2026-08-31T10:00:0${seq}Z`,
              work_item_id: null,
              parent_work_item_id: null,
              attempt_id: null,
              session_id: "i6-resume-audit",
              span_id: null,
              visibility: {
                model_visible: true,
                provider_visible: true,
                host_visible: true,
                redaction_state: "none",
              },
              kind: "assistant_message",
              payload: { metadata: { has_content: true } },
              payload_schema_version: "bb.payload.message.assistant.v1",
            }
            return new Response(
              new ReadableStream({
                start(controller) {
                  controller.enqueue(
                    new TextEncoder().encode(
                      `id: ${seq}\ndata: ${JSON.stringify(event)}\n\n`,
                    ),
                  )
                  controller.close()
                },
              }),
              { headers: { "content-type": "text/event-stream" } },
            )
          },
        },
        initialRetryMs: 0,
        query: { resume_token: 0 },
      },
    )
  })
  if (
    !resumeRequests[0].url.endsWith("?resume_token=0")
    || !resumeRequests[1].url.endsWith("?resume_token=1")
    || resumeRequests[1].last_event_id !== "1"
  ) {
    throw new Error("installed stream retained a stale resume token")
  }

  let mismatchedLifecycleRejected = false
  const forgedTerminal = {
    schema_version: "bb.public_session_event.v1",
    event_id: "session:i6-forged-audit:1",
    seq: 1,
    timestamp: "2026-08-31T10:00:01Z",
    work_item_id: null,
    parent_work_item_id: null,
    attempt_id: null,
    session_id: "i6-forged-audit",
    span_id: null,
    visibility: {
      model_visible: true,
      provider_visible: true,
      host_visible: true,
      redaction_state: "none",
    },
    kind: "session.completed",
    payload: {},
    payload_schema_version: "bb.payload.product_session.lifecycle.v1",
  }
  try {
    for await (const _event of streamSessionEvents("i6-forged-audit", {
      config: {
        baseUrl: request.base_url,
        fetch: async () => new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  `id: 1\ndata: ${JSON.stringify(forgedTerminal)}\n\n`,
                ),
              )
              controller.close()
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        ),
      },
    })) {
      throw new Error(`accepted forged lifecycle event: ${_event.kind}`)
    }
  } catch (error) {
    mismatchedLifecycleRejected =
      error.message === "Invalid session event lifecycle payload fields"
  }
  if (!mismatchedLifecycleRejected) {
    throw new Error("installed stream accepted a mismatched lifecycle payload")
  }
  const terminalEvent = {
    schema_version: "bb.public_session_event.v1",
    event_id: "session:i6-surface-audit:1",
    seq: 1,
    timestamp: "2026-08-31T10:00:01Z",
    work_item_id: null,
    parent_work_item_id: null,
    attempt_id: null,
    session_id: "i6-surface-audit",
    span_id: null,
    visibility: {
      model_visible: true,
      provider_visible: true,
      host_visible: true,
      redaction_state: "none",
    },
    kind: "session.completed",
    payload: { outcome: "completed", summary: "surface audit" },
    payload_schema_version: "bb.payload.product_session.lifecycle.v1",
  }
  await new Promise((resolve, reject) => {
    openEventStream(
      "i6-surface-audit",
      {
        onEvent() {
          throw new Error("installed terminal callback failure")
        },
        onError(error) {
          terminalCallbackErrorObserved =
            error.message === "installed terminal callback failure"
          if (terminalCallbackErrorObserved) resolve()
          else reject(error)
        },
      },
      {
        config: {
          baseUrl: request.base_url,
          fetch: async () => {
            terminalStreamRequests += 1
            return new Response(
              new ReadableStream({
                start(controller) {
                  controller.enqueue(
                    new TextEncoder().encode(
                      `id: 1\ndata: ${JSON.stringify(terminalEvent)}\n\n`,
                    ),
                  )
                  controller.close()
                },
              }),
              { headers: { "content-type": "text/event-stream" } },
            )
          },
        },
        initialRetryMs: 0,
      },
    )
  })
  await new Promise((resolve) => setTimeout(resolve, 20))
  process.stdout.write(JSON.stringify({
    ok: true,
    result: {
      public_methods: Object.keys(client).sort(),
      internal_e4_available: typeof internal.getE4Health === "function",
      terminal_stream_requests: terminalStreamRequests,
      terminal_callback_error_observed: terminalCallbackErrorObserved,
      resume_query_advanced: true,
      mismatched_lifecycle_rejected: mismatchedLifecycleRejected,
    },
  }))
} else {
  try {
    const result = await client.invokePublicAction(request.action_id, request.input ?? {})
    if (request.action_id === "public.session.events") {
      const events = []
      for await (const event of result) events.push(event)
      process.stdout.write(JSON.stringify({ ok: true, result: events }))
    } else {
      process.stdout.write(JSON.stringify({ ok: true, result }))
    }
  } catch (error) {
    if (error instanceof ApiError) {
      process.stdout.write(JSON.stringify({
        ok: false,
        error: { status: error.status, body: error.body },
      }))
      process.exitCode = 2
    } else {
      throw error
    }
  }
}
