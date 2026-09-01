import assert from "node:assert/strict"
import test from "node:test"

test("supported product entrypoints expose engine, session, and lifecycle interfaces", async () => {
  const [engine, session, lifecycle] = await Promise.all([
    import("../dist/engine.js"),
    import("../dist/session.js"),
    import("../dist/lifecycle.js"),
  ])

  assert.equal(typeof engine.createBreadboardClient, "function")
  assert.equal(typeof session.createCanonicalE4Client, "function")
  assert.equal(typeof session.decodeLoggedSessionEvent, "function")
  assert.equal(typeof session.detectSensitiveValues, "function")
  assert.equal(typeof lifecycle.createLifecycleE4Client, "function")
  assert.equal(lifecycle.ENGINE_IDENTITY_SCHEMA_VERSION, "bb.engine_identity.v1")
})
