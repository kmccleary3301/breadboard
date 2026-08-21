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
