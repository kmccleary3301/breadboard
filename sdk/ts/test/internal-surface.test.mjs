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
