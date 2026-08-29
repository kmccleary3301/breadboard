import assert from "node:assert/strict"
import test from "node:test"

test("internal entrypoint exposes the supported runtime surface", async () => {
  const internal = await import("../dist/internal.js")
  assert.equal(internal.ENGINE_IDENTITY_SCHEMA_VERSION, "bb.engine_identity.v1")
  assert.equal(internal.REDACTED_VALUE, "[redacted]")
  assert.deepEqual([...internal.deterministicSerialize({ b: 2, a: 1 })], [...internal.deterministicSerialize({ b: 2, a: 1 })])
  assert.ok(internal.detectSensitiveValues({ token: "sk-secret-value" }).findings.length > 0)
  assert.equal(typeof internal.createLifecycleE4Client, "function")
  assert.equal(typeof internal.createEndpointScopedE4Client, "function")
  assert.equal(typeof internal.createLocalEndpointScopedTransport, "function")
})

test("internal event decoder accepts session-scoped compaction tree nodes", async () => {
  const internal = await import("../dist/internal.js")
  const event = internal.decodeLoggedSessionEvent({
    stable_cursor: true,
    id: "event-1",
    seq: 1,
    session_id: "session-1",
    input_id: null,
    turn_id: null,
    timestamp_ms: 1,
    type: "ctree_node",
    payload: { node: { id: "bootstrap" } },
  })
  assert.equal(event.kind, "ctree_node_observed")
  assert.equal(event.inputId, null)
  assert.equal(event.turnId, null)
})


test("internal event decoder preserves incremental assistant tool calls", async () => {
  const internal = await import("../dist/internal.js")
  const envelope = (seq, type, payload) => ({
    stable_cursor: true,
    id: `event-${seq}`,
    seq,
    session_id: "session-1",
    input_id: "input-1",
    turn_id: "turn-1",
    timestamp_ms: seq,
    type,
    payload,
  })

  const started = internal.decodeLoggedSessionEvent(
    envelope(1, "assistant.tool_call.start", { index: 0, call_id: "call-1", tool: "read" }),
  )
  const delta = internal.decodeLoggedSessionEvent(
    envelope(2, "assistant.tool_call.delta", {
      index: 0,
      call_id: "call-1",
      tool: "read",
      arguments_delta: '{"path":',
    }),
  )
  const completed = internal.decodeLoggedSessionEvent(
    envelope(3, "assistant.tool_call.end", {
      index: 0,
      call_id: "call-1",
      tool: "read",
      arguments: '{"path":"README.md"}',
    }),
  )
  const emptyTextCompleted = internal.decodeLoggedSessionEvent(
    envelope(4, "assistant.message.end", { message_id: "message-1", text: "" }),
  )

  assert.deepEqual(
    [started.kind, delta.kind, completed.kind],
    ["assistant_tool_call_started", "assistant_tool_call_delta", "assistant_tool_call_completed"],
  )
  assert.equal(delta.payload.argumentsDelta, '{"path":')
  assert.equal(completed.payload.arguments, '{"path":"README.md"}')
  assert.equal(emptyTextCompleted.kind, "assistant_text_completed")
  assert.equal(emptyTextCompleted.payload.text, "")
})

test("internal event decoder accepts explicit session-scoped control and gap events", async () => {
  const internal = await import("../dist/internal.js")
  const envelope = (seq, type, payload) => ({
    stable_cursor: true,
    id: `event-${seq}`,
    seq,
    session_id: "session-1",
    input_id: null,
    turn_id: null,
    timestamp_ms: seq,
    type,
    payload,
  })

  const gap = internal.decodeLoggedSessionEvent(envelope(1, "stream.gap", { reason: "overflow" }))
  const control = internal.decodeLoggedSessionEvent(envelope(2, "session_control", { kind: "stop_requested" }))
  assert.equal(gap.kind, "stream_gap_observed")
  assert.equal(control.kind, "session_control_observed")
  assert.equal(gap.inputId, null)
  assert.equal(control.turnId, null)
  assert.equal(internal.projectEventEvidence(gap).eventKind, "stream_gap_observed")

  const cancelled = internal.decodeLoggedSessionEvent({
    stable_cursor: true,
    id: "event-3",
    seq: 3,
    session_id: "session-1",
    input_id: "input-1",
    turn_id: "turn-1",
    timestamp_ms: 3,
    type: "turn_cancelled",
    payload: { reason: "stop_requested" },
  })
  assert.deepEqual(internal.projectEventEvidence(cancelled).display, {
    kind: "cancellation-status",
    reason: "stop_requested",
  })
})

test("internal event decoder rejects unsafe runtime error codes", async () => {
  const internal = await import("../dist/internal.js")
  assert.throws(
    () => internal.decodeLoggedSessionEvent({
      stable_cursor: true,
      id: "event-error",
      seq: 1,
      session_id: "session-1",
      input_id: null,
      turn_id: null,
      timestamp_ms: 1,
      type: "error",
      payload: { code: "ProviderRawExceptionText" },
    }),
    (error) => error?.failure?.kind === "protocol",
  )
})

test("internal event decoder preserves provider completion presence semantics", async () => {
  const internal = await import("../dist/internal.js")
  const envelope = (payload) => ({
    stable_cursor: true,
    id: "event-provider-complete",
    seq: 1,
    session_id: "session-1",
    input_id: "input-1",
    turn_id: "turn-1",
    timestamp_ms: 1,
    type: "turn_completed",
    payload,
  })
  const event = internal.decodeLoggedSessionEvent(envelope({
    exchange_ref: {
      exchange_id: "px-1",
      schema_version: "bb.provider_exchange.v2",
    },
    finish_reason: "stop",
    raw_provider_finish: "completed",
    output_emitted: true,
    usage: {
      inputTokens: 0,
      extensions: { providerBucket: "standard" },
    },
  }))

  assert.equal(event.kind, "turn_completed")
  assert.equal(event.payload.usage.inputTokens, 0)
  assert.equal(Object.hasOwn(event.payload.usage, "outputTokens"), false)
  assert.deepEqual(event.payload.exchangeRef, {
    exchangeId: "px-1",
    schemaVersion: "bb.provider_exchange.v2",
  })
  assert.throws(
    () => internal.decodeLoggedSessionEvent(envelope({
      usage: { inputTokens: 0, providerTotal: 9 },
    })),
    (error) => error?.failure?.code === "unknown_provider_usage_field",
  )
  assert.throws(
    () => internal.decodeLoggedSessionEvent(envelope({
      usage: {
        extensions: Object.fromEntries(
          Array.from({ length: 33 }, (_, index) => [`key${index}`, index]),
        ),
      },
    })),
    (error) => error?.failure?.code === "invalid_provider_usage_extensions_bounds",
  )
  assert.throws(
    () => internal.decodeLoggedSessionEvent(envelope({
      usage: { extensions: { oversized: "x".repeat(4097) } },
    })),
    (error) => error?.failure?.code === "invalid_provider_usage_extensions_bounds",
  )
})
