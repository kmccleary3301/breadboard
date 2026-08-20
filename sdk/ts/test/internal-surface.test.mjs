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
