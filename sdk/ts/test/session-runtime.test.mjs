import assert from "node:assert/strict"
import test from "node:test"
import {
  CanonicalE4ClientError,
  REDACTED_VALUE,
  REPLAY_RETENTION_MAX_AGE_MS,
  REPLAY_RETENTION_MAX_EVENTS,
  computeSessionReplayDigest,
  createCanonicalE4Client,
  decodeExactEmptyPayload,
  decodeLoggedSessionEvent,
  deterministicSerialize,
  digestLoggedSessionEvent,
  normalizeSubmitInput,
  replayConfigurationDigest,
  turnFailureFromEvent,
} from "../dist/session-runtime.js"

const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json" },
})

const JSON_RESPONSE_LIMIT_BYTES = 1024 * 1024
const OVERSIZED_JSON_BYTES = new TextEncoder().encode(JSON.stringify({ padding: "x".repeat(2 * 1024 * 1024) }))
const SAFE_SSE_CHUNK_BYTES = 60 * 1024

const jsonBodyAtSize = (targetBytes) => {
  const encoder = new TextEncoder()
  const prefix = '{"padding":"'
  const suffix = '"}'
  const payloadBytes = targetBytes - encoder.encode(`${prefix}${suffix}`).byteLength
  const multibyteCount = Math.floor(payloadBytes / 3)
  const asciiCount = payloadBytes - multibyteCount * 3
  return encoder.encode(`${prefix}${"€".repeat(multibyteCount)}${"x".repeat(asciiCount)}${suffix}`)
}

const stalledByteResponse = (chunks, options = {}) => {
  const state = { cancelled: false }
  const response = new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
    },
    cancel() {
      state.cancelled = true
      if (options.cancelMode === "reject") return Promise.reject(new Error("cancel rejected"))
      if (options.cancelMode === "never") return new Promise(() => {})
    },
  }), {
    status: options.status ?? 200,
    headers: (() => {
      const headers = new Headers(options.headers ?? { "content-type": options.contentType ?? "application/json" })
      for (const value of options.contentLengths ?? (options.contentLength === undefined ? [] : [options.contentLength])) {
        headers.append("content-length", String(value))
      }
      return headers
    })(),
  })
  return { response, state }
}

const closedByteResponse = (chunks, contentType = "text/event-stream") => new Response(new ReadableStream({
  start(controller) {
    for (const chunk of chunks) controller.enqueue(chunk)
    controller.close()
  },
}), { headers: { "content-type": contentType } })

const oneByteResponse = (bytes, contentType = "application/json") => {
  let offset = 0
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset === bytes.byteLength) {
        controller.close()
        return
      }
      controller.enqueue(bytes.subarray(offset, offset + 1))
      offset += 1
    },
  }), { headers: { "content-type": contentType } })
}

const splitSseBytes = (value, chunkBytes = SAFE_SSE_CHUNK_BYTES) => {
  const bytes = new TextEncoder().encode(value)
  const chunks = []
  for (let offset = 0; offset < bytes.byteLength; offset += chunkBytes) {
    chunks.push(bytes.subarray(offset, offset + chunkBytes))
  }
  return chunks
}

const replayFacts = async (overrides = {}) => {
  const withoutDigest = {
    replayRetention: {
      maxEvents: REPLAY_RETENTION_MAX_EVENTS,
      maxAgeMs: REPLAY_RETENTION_MAX_AGE_MS,
      configurationDigest: replayConfigurationDigest,
    },
    earliestRetainedSequence: null,
    earliestRetainedEventId: null,
    headSequence: 0,
    headEventId: null,
    retainedHistory: "complete",
    ...overrides,
  }
  return {
    ...withoutDigest,
    sessionReplayContractDigest: await computeSessionReplayDigest(withoutDigest),
  }
}

const snapshot = async (sessionId = "session-1", overrides = {}) => ({
  session_id: sessionId,
  status: "running",
  created_at: "2026-07-17T00:00:00Z",
  last_activity_at: "2026-07-17T00:00:01Z",
  model: "openai/gpt-5",
  mode: "interactive",
  turn_admission: "idle",
  active_turn_id: null,
  queued_turn_count: 0,
  terminalTurns: [],
  ...(await replayFacts()),
  ...overrides,
})

const logged = (sequence, type, payload, overrides = {}) => ({
  stable_cursor: true,
  id: `event-${sequence}`,
  seq: sequence,
  type,
  session_id: "session-1",
  timestamp_ms: 1_752_710_400_000 + sequence,
  input_id: "input-1",
  turn_id: "turn-1",
  payload,
  ...overrides,
})

const sseResponse = (envelopes) => {
  const body = envelopes.map((envelope) => {
    const id = envelope.stable_cursor ? `id: ${envelope.seq}\n` : ""
    return `${id}data: ${JSON.stringify(envelope)}\n\n`
  }).join("")
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } })
}

const openEnvelope = async (overrides = {}) => ({
  stable_cursor: false,
  type: "stream.open",
  session_id: "session-1",
  timestamp_ms: 1_752_710_400_000,
  payload: await replayFacts(overrides),
})

const failureOf = async (operation) => {
  try {
    await operation()
    assert.fail("expected CanonicalE4ClientError")
  } catch (error) {
    assert.ok(error instanceof CanonicalE4ClientError)
    return error.failure
  }
}

test("normalization preserves structured identity and exact-empty is strict", () => {
  assert.deepEqual(normalizeSubmitInput("hello"), { text: "hello" })
  const structured = Object.freeze({
    text: "hello",
    attachments: Object.freeze(["attachment-1"]),
    clientMessageId: "message-1",
    extraIdentity: Object.freeze({ stable: true }),
  })
  assert.equal(normalizeSubmitInput(structured), structured)
  assert.deepEqual(structured, {
    text: "hello",
    attachments: ["attachment-1"],
    clientMessageId: "message-1",
    extraIdentity: { stable: true },
  })

  assert.equal(decodeExactEmptyPayload({}), decodeExactEmptyPayload(Object.create(null)))
  for (const invalid of [null, [], "", 0, { value: 1 }, Object.create({ inherited: true })]) {
    assert.throws(() => decodeExactEmptyPayload(invalid), CanonicalE4ClientError)
  }
  const hidden = {}
  Object.defineProperty(hidden, "hidden", { value: true })
  assert.throws(() => decodeExactEmptyPayload(hidden), CanonicalE4ClientError)
  assert.equal(REDACTED_VALUE, "[redacted]")
})

test("closed decoder covers all canonical logged families and rejects unsupported values", () => {
  const cases = [
    ["user_message", { text: "question", message: { role: "user" } }, "input_observed"],
    ["turn_start", {}, "turn_started"],
    ["conversation.compaction.start", { reason: "budget" }, "conversation_compaction_started"],
    ["conversation.compaction.end", { tokens: 42 }, "conversation_compaction_completed"],
    ["assistant.message.start", { item_id: "message-1" }, "assistant_message_started"],
    ["assistant.message.delta", { delta: "ans" }, "assistant_text_delta"],
    ["assistant.message.end", { item_id: "message-1" }, "assistant_text_completed"],
    ["assistant.reasoning.delta", { delta: "reason" }, "assistant_reasoning_delta"],
    ["assistant.thought_summary.delta", { text: "summary" }, "assistant_thought_summary_delta"],
    ["tool.exec.start", { call_id: "call-1" }, "tool_execution_started"],
    ["tool.exec.stdout.delta", { delta: "out" }, "tool_execution_stdout_delta"],
    ["tool.exec.stderr.delta", { delta: "err" }, "tool_execution_stderr_delta"],
    ["tool.exec.end", { exit_code: 0 }, "tool_execution_completed"],
    ["assistant_message", { text: "answer" }, "assistant_text_completed"],
    ["assistant_delta", { text: "partial" }, "assistant_text_delta"],
    ["tool_call", { call_id: "call-1", name: "read" }, "tool_called"],
    ["tool.result", { call_id: "call-1", output: "ok", status: "ok", error: false }, "tool_result_observed"],
    ["tool_result", { call_id: "call-1", output: "ok", status: "ok", error: false }, "tool_result_observed"],
    ["todo_event", { todo: { op: "snapshot", items: [] } }, "todo_updated"],
    ["permission_request", { request_id: "permission-1", tool: "read", kind: "read", rewindable: false }, "permission_requested"],
    ["permission_response", { request_id: "permission-1", decision: "allow" }, "permission_responded"],
    ["checkpoint_list", { checkpoints: [] }, "checkpoint_list_observed"],
    ["checkpoint_restored", { checkpoint_id: "checkpoint-1" }, "checkpoint_restored"],
    ["skills_catalog", { catalog: { skills: [] } }, "skills_catalog_observed"],
    ["skills_selection", { selection: {} }, "skills_selection_observed"],
    ["ctree_node", { id: "node-1" }, "ctree_node_observed"],
    ["ctree_snapshot", { root: "node-1" }, "ctree_snapshot_observed"],
    ["task_event", { kind: "child_started", task_id: "task-1" }, "task_event_observed"],
    ["warning", { code: "slow_provider" }, "warning_observed"],
    ["reward_update", { reward: 1 }, "reward_updated"],
    ["limits_update", { used: 1 }, "limits_updated"],
    ["completion", { summary: { completed: true } }, "completion_observed"],
    ["log_link", { path: "records/run.log" }, "log_linked"],
    ["error", { code: "provider_timeout", unsafe: "secret" }, "runtime_error_observed"],
    ["run_finished", { status: "completed" }, "run_finished"],
    ["turn_completed", {}, "turn_completed"],
    ["turn_failed", { error: { code: "provider_timeout", unsafe: "secret" } }, "turn_failed"],
    ["turn_cancelled", { reason: "superseded" }, "turn_cancelled"],
  ]
  for (const [type, payload, kind] of cases) {
    const decoded = decodeLoggedSessionEvent(logged(1, type, payload))
    assert.equal(decoded.kind, kind)
    assert.equal(decoded.eventId, "event-1")
    assert.equal(decoded.sequence, 1)
    assert.equal(decoded.sessionId, "session-1")
    assert.equal(decoded.inputId, "input-1")
    assert.equal(decoded.turnId, "turn-1")
  }
  const failed = decodeLoggedSessionEvent(logged(1, "turn_failed", { error: { code: "provider_timeout", message: "raw secret" } }))
  assert.deepEqual(failed.payload.error, { code: "provider_timeout", message: "[redacted]" })
  const runtimeError = decodeLoggedSessionEvent(logged(1, "error", { code: "provider_timeout", message: "raw secret" }))
  assert.deepEqual(runtimeError.payload.error, { code: "provider_timeout", message: "[redacted]" })
  assert.equal(runtimeError.scope, "turn")
  assert.equal(decodeLoggedSessionEvent(logged(1, "turn_start", {})).payload, decodeExactEmptyPayload({}))
  assert.throws(
    () => decodeLoggedSessionEvent(logged(1, "turn_start", { mode: "interactive" })),
    /invalid_exact_empty_payload/,
  )
  const { input_id: _inputId, turn_id: _turnId, ...sessionOnlyTool } = logged(1, "tool_call", { call_id: "call-1" })
  assert.throws(() => decodeLoggedSessionEvent(sessionOnlyTool), /missing_turn_correlation/)
  const { input_id: _todoInputId, turn_id: _todoTurnId, ...sessionTodo } = logged(
    1,
    "tool_result",
    { call_id: "todo:snapshot:init", todo: { op: "snapshot", items: [] } },
  )
  assert.equal(decodeLoggedSessionEvent(sessionTodo).kind, "todo_updated")
  assert.equal(
    decodeLoggedSessionEvent(logged(1, "tool_result", { todo: { op: "snapshot", items: [] } })).kind,
    "todo_updated",
  )
  assert.throws(() => decodeLoggedSessionEvent(logged(1, "not_a_canonical_event", {})), /unsupported_event_family/)
  assert.throws(
    () => decodeLoggedSessionEvent(logged(1, "tool_call", { name: "read" })),
    /invalid_tool_called_call_id/,
  )
  assert.throws(() => decodeLoggedSessionEvent({ ...logged(1, "turn_completed", {}), stable_cursor: false }), /event_not_stable_cursor/)
})

test("tool-call-only assistant messages are not decoded as empty text completions", () => {
  const decoded = decodeLoggedSessionEvent(
    logged(1, "assistant_message", {
      text: "",
      message: {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "call-1",
            type: "function",
            function: { name: "list_dir", arguments: "{\"path\":\".\"}" },
          },
        ],
      },
    }),
  )
  assert.equal(decoded.kind, "assistant_message_started")
})

test("deterministic bytes and digest do not depend on object insertion order", async () => {
  const first = deterministicSerialize({ z: [3, { b: 2, a: 1 }], a: true })
  const second = deterministicSerialize({ a: true, z: [3, { a: 1, b: 2 }] })
  assert.deepEqual(first, second)
  assert.equal(new TextDecoder().decode(first), '{"a":true,"z":[3,{"a":1,"b":2}]}')
  const event = decodeLoggedSessionEvent(logged(1, "assistant.message.delta", { text: "hello" }))
  assert.equal(await digestLoggedSessionEvent(event), await digestLoggedSessionEvent(event))
  assert.equal(
    await computeSessionReplayDigest(await replayFacts().then(({ sessionReplayContractDigest: _, ...facts }) => facts)),
    "sha256:b47abfd2a1beb67f7e6a3f1d5fa2503147cc3f09b76d3c1696121e139225c2f6",
  )
  const cyclic = []
  cyclic.push(cyclic)
  assert.throws(() => deterministicSerialize(cyclic), /cyclic_value/)
})

test("create and attach use only canonical URLs; submit, cancel, and local close preserve contracts", async () => {
  const requests = []
  const responses = [
    jsonResponse({ session_id: "session-1", status: "starting", created_at: "2026-07-17T00:00:00Z" }),
    jsonResponse(await snapshot()),
    jsonResponse({
      status: "accepted",
      client_message_id: "message-1",
      input_id: "input-1",
      turn_id: "turn-1",
      disposition: "started",
      original_disposition: "started",
    }, 202),
    jsonResponse({
      status: "accepted",
      cancellation_request_id: "cancel-1",
      cancellation_request_key: "cancel-key-1",
      input_id: "input-1",
      turn_id: "turn-1",
      disposition: "cancellation_requested",
      original_disposition: "cancellation_requested",
    }, 202),
    jsonResponse(await snapshot("session-2", {
      turn_admission: "active",
      active_turn_id: "turn-2",
      queued_turn_count: 2,
    })),
    jsonResponse(await snapshot("session-2", {
      turn_admission: "active",
      active_turn_id: "turn-2",
      queued_turn_count: 2,
    })),
  ]
  const fetch = async (input, init = {}) => {
    requests.push({ url: String(input), method: init.method, body: init.body, headers: new Headers(init.headers) })
    const response = responses.shift()
    assert.ok(response)
    return response
  }
  const client = createCanonicalE4Client({ baseUrl: "https://breadboard.test/root", authToken: "private-token", fetch })
  const runtime = await client.create({ configPath: "agent.yaml", task: "", maxSteps: 4, permissionMode: "ask" })
  assert.equal(runtime.sessionId, "session-1")
  assert.equal(requests[0].url, "https://breadboard.test/root/v1/sessions")
  assert.equal(requests[0].method, "POST")
  assert.deepEqual(JSON.parse(requests[0].body), {
    config_path: "agent.yaml",
    task: "",
    max_steps: 4,
    permission_mode: "ask",
  })
  assert.equal(requests[1].url, "https://breadboard.test/root/v1/sessions/session-1")
  assert.equal(requests[1].method, "GET")

  const structured = Object.freeze({ text: "body", attachments: Object.freeze(["attachment-1"]), clientMessageId: "message-1" })
  const receipt = await runtime.submit(structured)
  assert.equal(receipt.turnId, "turn-1")
  assert.equal(normalizeSubmitInput(structured), structured)
  assert.deepEqual(JSON.parse(requests[2].body), {
    content: "body",
    client_message_id: "message-1",
    attachments: ["attachment-1"],
  })
  assert.equal(requests[2].url, "https://breadboard.test/root/v1/sessions/session-1/input")

  const cancellation = await runtime.cancel({ turnId: "turn-1", cancellationRequestKey: "cancel-key-1", reason: "timeout" })
  assert.equal(cancellation.cancellationRequestId, "cancel-1")
  assert.deepEqual(JSON.parse(requests[3].body), { cancellation_request_key: "cancel-key-1", reason: "timeout" })
  assert.equal(requests[3].url, "https://breadboard.test/root/v1/sessions/session-1/turns/turn-1/cancel")

  await runtime.close()
  await runtime.close()
  assert.equal(requests.some((request) => request.method === "DELETE"), false)
  assert.equal(requests.some((request) => new URL(request.url).pathname.startsWith("/sessions")), false)

  const attached = await client.attach({ sessionId: "session-2" })
  const attachedSnapshot = await attached.snapshot()
  assert.equal(attachedSnapshot.turnAdmission, "active")
  assert.equal(attachedSnapshot.activeTurnId, "turn-2")
  assert.equal(attachedSnapshot.queuedTurnCount, 2)
  assert.equal(requests[4].method, "GET")
  assert.equal(requests[5].method, "GET")
  assert.equal(requests.slice(4).every((request) => request.url === "https://breadboard.test/root/v1/sessions/session-2"), true)
})

test("bounded JSON readers reject oversized and stalled success and error bodies", async () => {
  let createSignal
  const createBody = stalledByteResponse([], { contentLength: OVERSIZED_JSON_BYTES.byteLength })
  const createFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (_input, init = {}) => {
      createSignal = init.signal
      return createBody.response
    },
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(createFailure, { kind: "protocol", code: "response_body_too_large" })
  assert.equal(createSignal.aborted, true)
  assert.equal(createBody.state.cancelled, true)

  let snapshotSignal
  const snapshotBody = stalledByteResponse([OVERSIZED_JSON_BYTES])
  const snapshotFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (_input, init = {}) => {
      snapshotSignal = init.signal
      return snapshotBody.response
    },
  }).attach({ sessionId: "session-1" }))
  assert.deepEqual(snapshotFailure, { kind: "protocol", code: "response_body_too_large" })
  assert.equal(snapshotSignal.aborted, true)
  assert.equal(snapshotBody.state.cancelled, true)

  let submitSignal
  const submitBody = stalledByteResponse([OVERSIZED_JSON_BYTES])
  const submitResponses = [jsonResponse(await snapshot()), submitBody.response]
  const submitRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (_input, init = {}) => {
      submitSignal = init.signal
      return submitResponses.shift()
    },
  }).attach({ sessionId: "session-1" })
  const submitFailure = await failureOf(() => submitRuntime.submit({ text: "body", clientMessageId: "message-oversized" }))
  assert.deepEqual(submitFailure, { kind: "protocol", code: "response_body_too_large" })
  assert.equal(submitSignal.aborted, true)
  assert.equal(submitBody.state.cancelled, true)

  const errorBody = stalledByteResponse([OVERSIZED_JSON_BYTES], { status: 500 })
  const errorResponses = [jsonResponse(await snapshot()), errorBody.response]
  const errorRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => errorResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const errorFailure = await failureOf(() => errorRuntime.submit({ text: "body", clientMessageId: "message-error" }))
  assert.deepEqual(errorFailure, { kind: "http", status: 500, code: null, body: "[redacted]" })
  assert.equal(errorBody.state.cancelled, true)

  let streamErrorSignal
  const streamErrorBody = stalledByteResponse([], {
    status: 503,
    contentLength: OVERSIZED_JSON_BYTES.byteLength,
  })
  const streamErrorResponses = [jsonResponse(await snapshot()), streamErrorBody.response]
  const streamErrorRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (_input, init = {}) => {
      streamErrorSignal = init.signal
      return streamErrorResponses.shift()
    },
  }).attach({ sessionId: "session-1" })
  const streamErrorFailure = await failureOf(() => streamErrorRuntime.events().next())
  assert.deepEqual(streamErrorFailure, { kind: "http", status: 503, code: null, body: "[redacted]" })
  assert.equal(streamErrorSignal.aborted, true)
  assert.equal(streamErrorBody.state.cancelled, true)
})


test("bounded JSON readers enforce exact bytes, delivered bytes, malformed lengths, and abort liveness", async () => {
  const exact = jsonBodyAtSize(JSON_RESPONSE_LIMIT_BYTES)
  const over = jsonBodyAtSize(JSON_RESPONSE_LIMIT_BYTES + 1)
  assert.equal(exact.byteLength, JSON_RESPONSE_LIMIT_BYTES)
  assert.equal(over.byteLength, JSON_RESPONSE_LIMIT_BYTES + 1)

  const exactFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => new Response(exact, {
      headers: { "content-type": "application/json", "content-length": String(exact.byteLength) },
    }),
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(exactFailure, { kind: "protocol", code: "invalid_session_id" })

  const overLengthFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => stalledByteResponse([], { contentLength: over.byteLength }).response,
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(overLengthFailure, { kind: "protocol", code: "response_body_too_large" })

  const cumulativeBody = stalledByteResponse([
    over.subarray(0, JSON_RESPONSE_LIMIT_BYTES / 2),
    over.subarray(JSON_RESPONSE_LIMIT_BYTES / 2, JSON_RESPONSE_LIMIT_BYTES),
    over.subarray(JSON_RESPONSE_LIMIT_BYTES),
  ])
  const cumulativeFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => cumulativeBody.response,
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(cumulativeFailure, { kind: "protocol", code: "response_body_too_large" })
  assert.equal(cumulativeBody.state.cancelled, true)

  for (const contentLengths of [["invalid"], ["1", "2"]]) {
    const malformedLengthBody = stalledByteResponse([over], { contentLengths })
    const malformedLengthFailure = await failureOf(() => createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      fetch: async () => malformedLengthBody.response,
    }).create({ configPath: "agent.yaml" }))
    assert.deepEqual(malformedLengthFailure, { kind: "protocol", code: "response_body_too_large" })
    assert.equal(malformedLengthBody.state.cancelled, true)
  }

  const deliveredBody = stalledByteResponse([over], {
    headers: {
      "content-type": "application/json",
      "content-encoding": "gzip",
      "content-length": "128",
    },
  })
  const deliveredFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => deliveredBody.response,
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(deliveredFailure, { kind: "protocol", code: "response_body_too_large" })
  assert.equal(deliveredBody.state.cancelled, true)

  const zeroChunkBody = stalledByteResponse([new Uint8Array(), new Uint8Array()])
  const zeroChunkFailure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => zeroChunkBody.response,
  }).create({ configPath: "agent.yaml" }))
  assert.deepEqual(zeroChunkFailure, { kind: "protocol", code: "response_body_no_progress" })
  assert.equal(zeroChunkBody.state.cancelled, true)

  const oneByteSnapshot = new TextEncoder().encode(JSON.stringify(await snapshot()))
  const oneByteRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => oneByteResponse(oneByteSnapshot),
  }).attach({ sessionId: "session-1" })
  assert.equal(oneByteRuntime.sessionId, "session-1")

  for (const options of [{ cancelMode: "never" }, { cancelMode: "reject", contentLengths: ["invalid"] }]) {
    const stalled = stalledByteResponse([Uint8Array.of(0x7b)], options)
    const stalledFailure = await failureOf(() => createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      requestTimeoutMs: 20,
      fetch: async () => stalled.response,
    }).create({ configPath: "agent.yaml" }))
    assert.deepEqual(stalledFailure, { kind: "timeout" })
    assert.equal(stalled.state.cancelled, true)
  }
})

test("attachment uploads resolve to handles without mutation or silent dropping", async () => {
  const requests = []
  const responses = [
    jsonResponse(await snapshot()),
    jsonResponse({
      attachments: [
        { id: "attachment-uploaded", filename: "note.txt", content_type: "text/plain", size: 4 },
      ],
    }),
    jsonResponse({
      status: "accepted",
      client_message_id: "message-upload",
      input_id: "input-upload",
      turn_id: "turn-upload",
      disposition: "started",
      original_disposition: "started",
    }, 202),
  ]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (input, init = {}) => {
      requests.push({ url: String(input), method: init.method, body: init.body })
      return responses.shift()
    },
  }).attach({ sessionId: "session-1" })
  const upload = Object.freeze({ kind: "upload", filename: "note.txt", data: new Blob(["note"], { type: "text/plain" }) })
  const handle = Object.freeze({ kind: "handle", id: "attachment-existing" })
  const structured = Object.freeze({
    text: "inspect both",
    attachments: Object.freeze([handle, upload, "attachment-last"]),
    clientMessageId: "message-upload",
  })
  assert.equal(normalizeSubmitInput(structured), structured)
  await runtime.submit(structured)
  assert.equal(requests[1].url, "https://breadboard.test/v1/sessions/session-1/attachments")
  assert.ok(requests[1].body instanceof FormData)
  assert.equal(await requests[1].body.get("files").text(), "note")
  assert.deepEqual(JSON.parse(requests[2].body), {
    content: "inspect both",
    client_message_id: "message-upload",
    attachments: ["attachment-existing", "attachment-uploaded", "attachment-last"],
  })
  assert.equal(structured.attachments[1], upload)

  const unsupported = await failureOf(() => runtime.submit({
    text: "bad",
    attachments: [{ kind: "upload", filename: "bad.bin", data: new Uint8Array([1]) }],
    clientMessageId: "message-bad",
  }))
  assert.equal(unsupported.code, "unsupported_attachment_upload_data")
  const extraField = await failureOf(() => runtime.submit({ text: "bad", clientMessageId: "message-extra", identity: "dropped" }))
  assert.equal(extraField.code, "unsupported_submit_field")
  assert.equal(requests.length, 3)
})

test("attachment handles remain stable across lost submit responses and same-key retries", async () => {
  let snapshotPending = true
  let uploadCount = 0
  const inputBodies = []
  const requests = []
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (input, init = {}) => {
      const url = new URL(String(input))
      requests.push(url.pathname)
      if (snapshotPending) {
        snapshotPending = false
        return jsonResponse(await snapshot())
      }
      if (url.pathname.endsWith("/attachments")) {
        uploadCount += 1
        return jsonResponse({
          attachments: [{ id: `attachment-${uploadCount}`, filename: "note.txt", content_type: "text/plain", size: 4 }],
        })
      }
      if (url.pathname.endsWith("/input")) {
        inputBodies.push(JSON.parse(init.body))
        if (inputBodies.length === 1) throw new TypeError("response lost after acceptance")
        return jsonResponse({
          status: "accepted",
          client_message_id: "message-retry",
          input_id: "input-retry",
          turn_id: "turn-retry",
          disposition: "deduplicated",
          original_disposition: "started",
        }, 202)
      }
      assert.fail(`unexpected request ${url.pathname}`)
    },
  }).attach({ sessionId: "session-1" })
  const submission = Object.freeze({
    text: "retry me",
    attachments: Object.freeze([
      Object.freeze({ kind: "upload", filename: "note.txt", data: new Blob(["note"], { type: "text/plain" }) }),
    ]),
    clientMessageId: "message-retry",
  })
  const lost = await failureOf(() => runtime.submit(submission))
  assert.equal(lost.kind, "http")
  const receipt = await runtime.submit(submission)
  assert.equal(receipt.disposition, "deduplicated")
  assert.equal(uploadCount, 1)
  assert.deepEqual(inputBodies.map((body) => body.attachments), [["attachment-1"], ["attachment-1"]])

  const mismatch = await failureOf(() => runtime.submit({ ...submission, text: "different" }))
  assert.equal(mismatch.code, "client_message_id_body_mismatch")
  assert.equal(requests.filter((path) => path.endsWith("/attachments")).length, 1)
  assert.equal(requests.filter((path) => path.endsWith("/input")).length, 2)
})

test("submit and cancellation receipts must echo the transmitted identities", async () => {
  const responses = [
    jsonResponse(await snapshot()),
    jsonResponse({
      status: "accepted",
      client_message_id: "message-other",
      input_id: "input-1",
      turn_id: "turn-1",
      disposition: "started",
      original_disposition: "started",
    }, 202),
    jsonResponse({
      status: "accepted",
      cancellation_request_id: "cancel-1",
      cancellation_request_key: "cancel-key-other",
      input_id: "input-1",
      turn_id: "turn-other",
      disposition: "cancellation_requested",
      original_disposition: "cancellation_requested",
    }, 202),
  ]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => responses.shift(),
  }).attach({ sessionId: "session-1" })
  const submitFailure = await failureOf(() => runtime.submit({ text: "body", clientMessageId: "message-1" }))
  assert.equal(submitFailure.code, "submit_receipt_identity_mismatch")
  const cancelFailure = await failureOf(() => runtime.cancel({ turnId: "turn-1", cancellationRequestKey: "cancel-key-1" }))
  assert.equal(cancelFailure.code, "cancellation_receipt_identity_mismatch")
})

test("event cursor commits only after yield; reconnect is exclusive and dedupes before sequence validation", async () => {
  const factsAtTwo = { earliestRetainedSequence: 1, earliestRetainedEventId: "event-1", headSequence: 2, headEventId: "event-2" }
  const open = await openEnvelope(factsAtTwo)
  const event1 = logged(1, "user_message", { text: "question" })
  const event2 = logged(2, "turn_start", {})
  const requests = []
  const responses = [
    jsonResponse(await snapshot("session-1", await replayFacts(factsAtTwo))),
    sseResponse([open, event1, event2]),
    sseResponse([open, event1, event1, event2]),
    sseResponse([open]),
  ]
  const fetch = async (input, init = {}) => {
    requests.push({ url: String(input), headers: new Headers(init.headers) })
    const response = responses.shift()
    assert.ok(response)
    return response
  }
  const runtime = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch }).attach({ sessionId: "session-1" })

  const first = runtime.events()
  assert.equal((await first.next()).value.kind, "input_observed")
  await first.return()

  const second = runtime.events()
  assert.equal((await second.next()).value.eventId, "event-1")
  assert.equal((await second.next()).value.eventId, "event-2")
  assert.equal((await second.next()).done, true)

  const third = runtime.events()
  assert.equal((await third.next()).done, true)
  const streamRequests = requests.slice(1)
  assert.equal(new URL(streamRequests[0].url).searchParams.has("from_id"), false)
  assert.equal(new URL(streamRequests[1].url).searchParams.has("from_id"), false)
  assert.equal(new URL(streamRequests[2].url).searchParams.get("from_id"), "event-2")
  assert.equal(new URL(streamRequests[2].url).searchParams.get("limit"), null)
  assert.equal(streamRequests[2].headers.get("last-event-id"), "event-2")
})

test("local close terminates a stream suspended at delivery without committing it", async () => {
  const open = await openEnvelope({ earliestRetainedSequence: 1, earliestRetainedEventId: "event-1", headSequence: 1, headEventId: "event-1" })
  const requests = []
  const responses = [jsonResponse(await snapshot()), sseResponse([open, logged(1, "user_message", { text: "question" })])]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (input, init = {}) => {
      requests.push({ url: String(input), method: init.method })
      return responses.shift()
    },
  }).attach({ sessionId: "session-1" })
  const events = runtime.events()
  assert.equal((await events.next()).value.eventId, "event-1")
  const closing = runtime.close()
  assert.equal(runtime.close(), closing)
  await Promise.race([
    closing,
    new Promise((_, reject) => setTimeout(() => reject(new Error("local close timed out")), 250)),
  ])
  await runtime.close()
  assert.equal((await events.next()).done, true)
  assert.equal(requests.some((request) => request.method === "DELETE"), false)
})

test("local close settles stalled SSE reads when cancellation rejects or never settles", async () => {
  for (const cancelMode of ["reject", "never"]) {
    const body = stalledByteResponse([], { contentType: "text/event-stream", cancelMode })
    let resolveStreamStarted
    const streamStarted = new Promise((resolve) => { resolveStreamStarted = resolve })
    const responses = [jsonResponse(await snapshot()), body.response]
    const runtime = await createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      fetch: async () => {
        const response = responses.shift()
        if (responses.length === 0) resolveStreamStarted()
        return response
      },
    }).attach({ sessionId: "session-1" })
    const events = runtime.events()
    const next = events.next()
    await streamStarted
    const closing = runtime.close()
    await Promise.race([
      closing,
      new Promise((_, reject) => setTimeout(() => reject(new Error(`local close stalled after ${cancelMode} cancellation`)), 250)),
    ])
    assert.equal((await next).done, true)
    assert.equal(body.state.cancelled, true)
  }
})

test("zero-byte SSE chunks fail closed and caller abort settles after one-byte progress", async () => {
  const zeroBody = stalledByteResponse([new Uint8Array(), new Uint8Array()], { contentType: "text/event-stream" })
  const zeroResponses = [jsonResponse(await snapshot()), zeroBody.response]
  const zeroRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => zeroResponses.shift(),
  }).attach({ sessionId: "session-1" })
  assert.deepEqual(await failureOf(() => zeroRuntime.events().next()), { kind: "protocol", code: "sse_chunk_no_progress" })
  assert.equal(zeroBody.state.cancelled, true)

  let resolveDelivered
  const delivered = new Promise((resolve) => { resolveDelivered = resolve })
  let sent = false
  let cancelled = false
  const stalledBody = new Response(new ReadableStream({
    pull(controller) {
      if (sent) return
      sent = true
      controller.enqueue(Uint8Array.of(0x64))
      resolveDelivered()
    },
    cancel() {
      cancelled = true
      return new Promise(() => {})
    },
  }), { headers: { "content-type": "text/event-stream" } })
  const stalledResponses = [jsonResponse(await snapshot()), stalledBody]
  const stalledRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => stalledResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const controller = new AbortController()
  const pending = stalledRuntime.events({ signal: controller.signal }).next()
  await delivered
  controller.abort()
  assert.deepEqual(await failureOf(() => pending), { kind: "caller-abort" })
  assert.equal(cancelled, true)
})

test("attach rejects cross-session snapshots and concurrent observation", async () => {
  const wrongSession = [jsonResponse(await snapshot("session-other"))]
  const failure = await failureOf(() => createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => wrongSession.shift(),
  }).attach({ sessionId: "session-1" }))
  assert.equal(failure.kind, "protocol")
  assert.equal(failure.code, "cross_session_snapshot")

  const open = await openEnvelope({ earliestRetainedSequence: 1, earliestRetainedEventId: "event-1", headSequence: 1, headEventId: "event-1" })
  const responses = [jsonResponse(await snapshot()), sseResponse([open, logged(1, "turn_start", {})])]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => responses.shift(),
  }).attach({ sessionId: "session-1" })
  const first = runtime.events()
  assert.equal((await first.next()).value.eventId, "event-1")
  assert.throws(() => runtime.events(), /observation_already_active/)
  await first.return()
})

test("lazy observation remains exclusive, pre-abort is typed, and open-head truncation is a gap", async () => {
  const openAtOne = await openEnvelope({ earliestRetainedSequence: 1, earliestRetainedEventId: "event-1", headSequence: 1, headEventId: "event-1" })
  const responses = [jsonResponse(await snapshot()), sseResponse([openAtOne, logged(1, "turn_start", {})])]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => responses.shift(),
  }).attach({ sessionId: "session-1" })
  const first = runtime.events()
  assert.throws(() => runtime.events(), /observation_already_active/)
  assert.equal((await first.next()).value.eventId, "event-1")
  await first.return()

  const abortedResponses = [jsonResponse(await snapshot())]
  const abortedRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => abortedResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const controller = new AbortController()
  controller.abort()
  const aborted = await failureOf(() => abortedRuntime.events({ signal: controller.signal }).next())
  assert.deepEqual(aborted, { kind: "caller-abort" })

  const truncatedResponses = [jsonResponse(await snapshot()), sseResponse([openAtOne])]
  const truncatedRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => truncatedResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const truncated = await failureOf(() => truncatedRuntime.events().next())
  assert.equal(truncated.kind, "resume-gap")
  assert.equal(truncated.code, "stream_truncated_before_open_head")
})

test("digest collisions and sequence discontinuities fail closed", async () => {
  const open = await openEnvelope({ earliestRetainedSequence: 1, earliestRetainedEventId: "event-1", headSequence: 2, headEventId: "event-2" })
  const responses = [
    jsonResponse(await snapshot()),
    sseResponse([
      open,
      logged(1, "user_message", { text: "first" }),
      logged(1, "user_message", { text: "changed" }),
    ]),
  ]
  const runtime = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch: async () => responses.shift() }).attach({ sessionId: "session-1" })
  const events = runtime.events()
  assert.equal((await events.next()).value.payload.text, "first")
  const collision = await failureOf(() => events.next())
  assert.deepEqual(collision, { kind: "protocol", code: "event_id_digest_collision", eventId: "event-1", sequence: 1 })

  const responses2 = [jsonResponse(await snapshot()), sseResponse([open, logged(2, "turn_start", {})])]
  const runtime2 = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch: async () => responses2.shift() }).attach({ sessionId: "session-1" })
  const discontinuity = await failureOf(() => runtime2.events().next())
  assert.equal(discontinuity.kind, "resume-gap")
  assert.equal(discontinuity.code, "sequence_discontinuity")
})

test("partial history, stream gaps, and preheader 409 are typed resume gaps", async () => {
  const partialOpen = await openEnvelope({ retainedHistory: "partial", headSequence: 1 })
  const gapEnvelope = {
    stable_cursor: false,
    type: "stream.gap",
    session_id: "session-1",
    timestamp_ms: 1,
    payload: { code: "subscriber_overflow" },
  }
  const responseSets = [
    [jsonResponse(await snapshot()), sseResponse([partialOpen])],
    [jsonResponse(await snapshot()), sseResponse([await openEnvelope(), gapEnvelope])],
    [jsonResponse(await snapshot()), jsonResponse({
      error: "resume_window_exceeded",
      detail: {
        last_event_id: "event-9",
        recovery: { action: "fetch_session_snapshot_then_reconnect_without_cursor" },
        credential: "must-not-escape",
      },
      path: null,
    }, 409)],
  ]
  for (const [index, responses] of responseSets.entries()) {
    const runtime = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch: async () => responses.shift() }).attach({ sessionId: "session-1" })
    const failure = await failureOf(() => runtime.events().next())
    assert.equal(failure.kind, "resume-gap")
    assert.equal(failure.code, ["partial_retained_history", "subscriber_overflow", "resume_window_exceeded"][index])
    assert.equal(JSON.stringify(failure).includes("credential"), false)
  }
})

test("an accepted retained cursor may resume across globally partial history", async () => {
  const completeOpen = await openEnvelope({
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-1",
    headSequence: 1,
    headEventId: "event-1",
  })
  const partialOpen = await openEnvelope({
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-1",
    headSequence: 1,
    headEventId: "event-1",
    retainedHistory: "partial",
  })
  const requests = []
  const responses = [
    jsonResponse(await snapshot()),
    sseResponse([completeOpen, logged(1, "turn_start", {})]),
    sseResponse([partialOpen]),
  ]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (input) => {
      requests.push(String(input))
      return responses.shift()
    },
  }).attach({ sessionId: "session-1" })
  const observed = []
  for await (const event of runtime.events()) observed.push(event.eventId)
  assert.deepEqual(observed, ["event-1"])
  assert.equal((await runtime.events().next()).done, true)
  assert.equal(new URL(requests.at(-1)).searchParams.get("from_id"), "event-1")
})

test("terminal transitions are unique per turn and failures stay redacted", async () => {
  const open = await openEnvelope({
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-1",
    headSequence: 2,
    headEventId: "event-2",
  })
  const responses = [
    jsonResponse(await snapshot()),
    sseResponse([
      open,
      logged(1, "turn_completed", {}),
      logged(2, "turn_failed", { error: { code: "provider_timeout", message: "secret" } }),
    ]),
  ]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => responses.shift(),
  }).attach({ sessionId: "session-1" })
  const events = runtime.events()
  assert.equal((await events.next()).value.kind, "turn_completed")
  const duplicate = await failureOf(() => events.next())
  assert.equal(duplicate.code, "duplicate_terminal_transition")

  const failed = decodeLoggedSessionEvent(logged(1, "turn_failed", { error: { code: "provider_timeout", message: "secret" } }))
  assert.deepEqual(turnFailureFromEvent(failed), {
    kind: "turn-failed",
    sessionId: "session-1",
    inputId: "input-1",
    turnId: "turn-1",
    error: { code: "provider_timeout", message: "[redacted]" },
  })
})

test("truncated and invalid-UTF8 SSE frames fail as protocol errors", async () => {
  const open = await openEnvelope()
  const incomplete = new Response(`data: ${JSON.stringify(open)}\n`, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  })
  const incompleteResponses = [jsonResponse(await snapshot()), incomplete]
  const incompleteRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => incompleteResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const truncated = await failureOf(() => incompleteRuntime.events().next())
  assert.equal(truncated.kind, "protocol")
  assert.equal(truncated.code, "truncated_sse_frame")

  const invalidResponses = [
    jsonResponse(await snapshot()),
    new Response(new Uint8Array([0xff]), { status: 200, headers: { "content-type": "text/event-stream" } }),
  ]
  const invalidRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => invalidResponses.shift(),
  }).attach({ sessionId: "session-1" })
  const invalid = await failureOf(() => invalidRuntime.events().next())
  assert.equal(invalid.kind, "protocol")
  assert.equal(invalid.code, "invalid_sse_utf8")
})

test("incremental SSE scanning preserves CRLF, fields, multibyte data, and one-event cursor semantics", async () => {
  const open = await openEnvelope({
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-1",
    headSequence: 1,
    headEventId: "event-1",
  })
  const event = logged(1, "user_message", { text: "€" })
  const eventJson = JSON.stringify(event)
  const splitAt = eventJson.indexOf(",") + 1
  const wire = [
    ": open comment\r\n",
    "unknown: ignored\r\n",
    `data: ${JSON.stringify(open)}\r\n\r\n`,
    ": event comment\r\n",
    "event: canonical\r\n",
    "id: 1\r\n",
    `data: ${eventJson.slice(0, splitAt)}\r\n`,
    `data: ${eventJson.slice(splitAt)}\r\n\r\n`,
  ].join("")
  const bytes = new TextEncoder().encode(wire)
  const oneByteChunks = Array.from(bytes, (_value, index) => bytes.subarray(index, index + 1))
  const responses = [jsonResponse(await snapshot()), closedByteResponse(oneByteChunks)]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => responses.shift(),
  }).attach({ sessionId: "session-1" })
  const events = runtime.events()
  const observed = await events.next()
  assert.equal(observed.value.eventId, "event-1")
  assert.equal(observed.value.payload.text, "€")
  assert.equal((await events.next()).done, true)

  const unterminated = new TextEncoder().encode('data: {"stable_cursor":\r\ndata: false}\r\n')
  const incompleteResponses = [jsonResponse(await snapshot()), closedByteResponse(splitSseBytes(new TextDecoder().decode(unterminated), 1))]
  const incompleteRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => incompleteResponses.shift(),
  }).attach({ sessionId: "session-1" })
  assert.deepEqual(await failureOf(() => incompleteRuntime.events().next()), { kind: "protocol", code: "truncated_sse_frame" })
})

test("SSE frame byte accounting accepts an exact multibyte boundary and rejects boundary plus one", async () => {
  const frameLimit = 512 * 1024
  const open = await openEnvelope()
  const openFrame = `data: ${JSON.stringify(open)}\r\n\r\n`
  const payloadAt = (bytes) => `${"€".repeat(Math.floor(bytes / 3))}${"x".repeat(bytes % 3)}`
  const exactFrame = `:${payloadAt(frameLimit - 3)}\r\n\r\n`
  const exactResponses = [
    jsonResponse(await snapshot()),
    closedByteResponse([new TextEncoder().encode(`${openFrame}${exactFrame}`)]),
  ]
  const exactRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => exactResponses.shift(),
  }).attach({ sessionId: "session-1" })
  assert.equal((await exactRuntime.events().next()).done, true)

  const overFrame = new TextEncoder().encode(`${openFrame}:${payloadAt(frameLimit - 2)}\r\n\r\n`)
  const overBody = stalledByteResponse([overFrame], { contentType: "text/event-stream" })
  const overResponses = [jsonResponse(await snapshot()), overBody.response]
  const overRuntime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async () => overResponses.shift(),
  }).attach({ sessionId: "session-1" })
  assert.deepEqual(await failureOf(() => overRuntime.events().next()), { kind: "protocol", code: "sse_frame_too_large" })
  assert.equal(overBody.state.cancelled, true)
})

test("SSE resource limits reject oversized chunks, tails, frames, data, and pending queues", async () => {
  const streamFailure = async (chunks) => {
    const body = stalledByteResponse(chunks, { contentType: "text/event-stream" })
    const responses = [jsonResponse(await snapshot()), body.response]
    const runtime = await createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      fetch: async () => responses.shift(),
    }).attach({ sessionId: "session-1" })
    const failure = await failureOf(() => runtime.events().next())
    assert.equal(body.state.cancelled, true)
    return failure
  }

  const oversizedChunk = new Uint8Array(2 * 1024 * 1024)
  oversizedChunk.fill(0x61)
  assert.deepEqual(await streamFailure([oversizedChunk]), { kind: "protocol", code: "sse_chunk_too_large" })

  const unfinishedTailChunks = Array.from({ length: 13 }, () => {
    const chunk = new Uint8Array(SAFE_SSE_CHUNK_BYTES)
    chunk.fill(0x61)
    return chunk
  })
  assert.deepEqual(await streamFailure(unfinishedTailChunks), { kind: "protocol", code: "sse_frame_too_large" })

  const oversizedFrame = `x: ${"a".repeat(600 * 1024)}\n\n`
  assert.deepEqual(await streamFailure(splitSseBytes(oversizedFrame)), { kind: "protocol", code: "sse_frame_too_large" })

  const oversizedEventData = `data: ${"a".repeat(300 * 1024)}\n\n`
  assert.deepEqual(await streamFailure(splitSseBytes(oversizedEventData)), { kind: "protocol", code: "sse_event_data_too_large" })

  const oversizedEventId = new TextEncoder().encode(`id: ${"x".repeat(1025)}\ndata: {}\n\n`)
  assert.deepEqual(await streamFailure([oversizedEventId]), { kind: "protocol", code: "sse_event_id_too_large" })

  const pendingCount = new TextEncoder().encode(Array.from({ length: 2049 }, () => "data: {}\n\n").join(""))
  assert.deepEqual(await streamFailure([pendingCount]), { kind: "protocol", code: "sse_pending_event_count_exceeded" })

  const pendingBytes = new TextEncoder().encode(`data: ${"a".repeat(240 * 1024)}\n\ndata: ${"b".repeat(240 * 1024)}\n\ndata: ${"c".repeat(64 * 1024)}\n\n`)
  assert.deepEqual(await streamFailure([pendingBytes]), { kind: "protocol", code: "sse_pending_event_bytes_exceeded" })
})

test("every SSE resource limit accepts its exact boundary and rejects boundary plus one", async () => {
  const encoder = new TextEncoder()
  const streamFailure = async (bytes) => {
    const body = stalledByteResponse([bytes], { contentType: "text/event-stream" })
    const responses = [jsonResponse(await snapshot()), body.response]
    const runtime = await createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      fetch: async () => responses.shift(),
    }).attach({ sessionId: "session-1" })
    const failure = await failureOf(() => runtime.events().next())
    assert.equal(body.state.cancelled, true)
    return failure
  }
  const commentFramesAtSize = (targetBytes) => {
    const frames = []
    let remaining = targetBytes
    while (remaining > 0) {
      const frameBytes = Math.min(512 * 1024, remaining)
      assert.ok(frameBytes >= 3)
      frames.push(`:${"x".repeat(frameBytes - 3)}\n\n`)
      remaining -= frameBytes
    }
    return frames.join("")
  }
  const invalidEnvelope = "data: {}\n\n"
  const chunkAtSize = (targetBytes) => encoder.encode(`${invalidEnvelope}${commentFramesAtSize(targetBytes - invalidEnvelope.length)}`)

  const chunkLimit = 1024 * 1024
  assert.deepEqual(await streamFailure(chunkAtSize(chunkLimit)), { kind: "protocol", code: "invalid_stream_event_type" })
  assert.deepEqual(await streamFailure(chunkAtSize(chunkLimit + 1)), { kind: "protocol", code: "sse_chunk_too_large" })

  const eventDataLimit = 256 * 1024
  assert.deepEqual(
    await streamFailure(encoder.encode(`data: ${"a".repeat(eventDataLimit)}\n\n`)),
    { kind: "protocol", code: "malformed_sse_json" },
  )
  assert.deepEqual(
    await streamFailure(encoder.encode(`data: ${"a".repeat(eventDataLimit + 1)}\n\n`)),
    { kind: "protocol", code: "sse_event_data_too_large" },
  )

  const eventIdLimit = 1024
  assert.deepEqual(
    await streamFailure(encoder.encode(`id: ${"x".repeat(eventIdLimit)}\ndata: {}\n\n`)),
    { kind: "protocol", code: "invalid_stream_event_type" },
  )
  assert.deepEqual(
    await streamFailure(encoder.encode(`id: ${"x".repeat(eventIdLimit + 1)}\ndata: {}\n\n`)),
    { kind: "protocol", code: "sse_event_id_too_large" },
  )

  const pendingEventLimit = 2048
  assert.deepEqual(
    await streamFailure(encoder.encode("data: {}\n\n".repeat(pendingEventLimit))),
    { kind: "protocol", code: "invalid_stream_event_type" },
  )
  assert.deepEqual(
    await streamFailure(encoder.encode("data: {}\n\n".repeat(pendingEventLimit + 1))),
    { kind: "protocol", code: "sse_pending_event_count_exceeded" },
  )

  const pendingByteLimit = 256 * 1024
  const pendingBytesAt = (bytes) => encoder.encode(`data: ${"a".repeat(240 * 1024)}\n\ndata: ${"b".repeat(bytes - 240 * 1024)}\n\n`)
  assert.deepEqual(await streamFailure(pendingBytesAt(pendingByteLimit)), { kind: "protocol", code: "malformed_sse_json" })
  assert.deepEqual(
    await streamFailure(pendingBytesAt(pendingByteLimit + 1)),
    { kind: "protocol", code: "sse_pending_event_bytes_exceeded" },
  )

  const dataLineLimit = 4096
  assert.deepEqual(
    await streamFailure(encoder.encode(`${"data:\n".repeat(dataLineLimit)}\n`)),
    { kind: "protocol", code: "malformed_sse_json" },
  )
  assert.deepEqual(
    await streamFailure(encoder.encode(`${"data:\n".repeat(dataLineLimit + 1)}\n`)),
    { kind: "protocol", code: "sse_data_line_count_exceeded" },
  )
})

test("HTTP failures preserve safe correlation only and redact response bodies", async () => {
  const secret = "sk-private-value"
  const responses = [
    jsonResponse(await snapshot()),
    jsonResponse({ error: "input_idempotency_conflict", detail: { message: secret, token: secret, turn_id: "turn-safe" } }, 409),
  ]
  const runtime = await createCanonicalE4Client({ baseUrl: `https://${secret}@breadboard.test`, authToken: secret, fetch: async () => responses.shift() }).attach({ sessionId: "session-1" })
  const failure = await failureOf(() => runtime.submit({ text: "same", clientMessageId: "message-1" }))
  assert.deepEqual(failure, { kind: "idempotency-conflict", sessionId: "session-1", turnId: "turn-safe" })
  assert.equal(JSON.stringify(failure).includes(secret), false)

  const rawResponses = [jsonResponse(await snapshot()), jsonResponse({ error: "input_idempotency_conflict", detail: { secret, turn_id: secret } }, 500)]
  const runtime2 = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch: async () => rawResponses.shift() }).attach({ sessionId: "session-1" })
  const rawFailure = await failureOf(() => runtime2.submit({ text: "body", clientMessageId: "message-2" }))
  assert.deepEqual(rawFailure, { kind: "http", status: 500, code: null, body: "[redacted]" })
  assert.equal(JSON.stringify(rawFailure).includes(secret), false)

  const secretResponses = [
    jsonResponse(await snapshot()),
    jsonResponse({ error: secret, detail: { turn_id: `${secret}\ncontrol` } }, 409),
  ]
  const secretRuntime = await createCanonicalE4Client({ baseUrl: "https://breadboard.test", fetch: async () => secretResponses.shift() }).attach({ sessionId: "session-1" })
  const secretFailure = await failureOf(() => secretRuntime.submit({ text: "body", clientMessageId: "message-3" }))
  assert.deepEqual(secretFailure, { kind: "admission-conflict", sessionId: "session-1", code: null })
  assert.equal(JSON.stringify(secretFailure).includes(secret), false)

  for (const [index, turnId] of [`turn-safe\n${secret}`, `turn-${"x".repeat(129)}`].entries()) {
    const invalidCorrelationResponses = [
      jsonResponse(await snapshot()),
      jsonResponse({ error: "input_idempotency_conflict", detail: { turn_id: turnId } }, 409),
    ]
    const invalidCorrelationRuntime = await createCanonicalE4Client({
      baseUrl: "https://breadboard.test",
      fetch: async () => invalidCorrelationResponses.shift(),
    }).attach({ sessionId: "session-1" })
    const invalidCorrelation = await failureOf(() => invalidCorrelationRuntime.submit({ text: "body", clientMessageId: `message-invalid-${index}` }))
    assert.deepEqual(invalidCorrelation, { kind: "idempotency-conflict", sessionId: "session-1", turnId: null })
    assert.equal(JSON.stringify(invalidCorrelation).includes(turnId), false)
  }
})

test("events outside the exact retained digest bound fail as unretained replay", async () => {
  const total = REPLAY_RETENTION_MAX_EVENTS + 1
  const factsAtHead = {
    earliestRetainedSequence: 1,
    earliestRetainedEventId: "event-1",
    headSequence: total,
    headEventId: `event-${total}`,
  }
  const open = await openEnvelope(factsAtHead)
  const allEvents = Array.from({ length: total }, (_, index) => logged(index + 1, "assistant.message.delta", { text: String(index + 1) }))
  const requests = []
  const responses = [jsonResponse(await snapshot()), sseResponse([open, ...allEvents]), sseResponse([open, allEvents[0]])]
  const runtime = await createCanonicalE4Client({
    baseUrl: "https://breadboard.test",
    fetch: async (input) => { requests.push(String(input)); return responses.shift() },
  }).attach({ sessionId: "session-1" })
  let count = 0
  for await (const event of runtime.events()) {
    assert.equal(event.sequence, count + 1)
    count += 1
  }
  assert.equal(count, total)
  const failure = await failureOf(() => runtime.events().next())
  assert.equal(failure.kind, "resume-gap")
  assert.equal(failure.code, "unretained_replay")
  assert.equal(new URL(requests.at(-1)).searchParams.get("from_id"), `event-${total}`)
})
