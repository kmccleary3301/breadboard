import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join, resolve } from "node:path"
import test from "node:test"

const root = resolve(new URL("..", import.meta.url).pathname)
const docs = readFileSync(resolve(root, "../../../docs_tmp/bb_direction_assessment/ts_consolidation/T3_030_SURFACE.md"), "utf8")
const names = [...docs.matchAll(/^\| `([^`]+)` \| [^|]+ \| `\.\/internal` \|/gm)].map((match) => match[1])
const declarations = ["internal.d.ts", "session-runtime.d.ts", "session-evidence.d.ts", "lifecycle-client.d.ts", "endpoint-client.d.ts"].map((file) => readFileSync(join(root, "dist", file), "utf8")).join("\n")

test("every T3 internal disposition resolves to a real declaration", async () => {
  assert.equal(names.length, 200)
  for (const name of names) assert.match(declarations, new RegExp(`\\b${name.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}\\b`), name)
  const internal = await import("../dist/internal.js")
  assert.equal(internal.ENGINE_IDENTITY_SCHEMA_VERSION, "bb.engine_identity.v1")
  assert.equal(internal.REDACTED_VALUE, "[redacted]")
  assert.deepEqual([...internal.deterministicSerialize({ b: 2, a: 1 })], [...internal.deterministicSerialize({ b: 2, a: 1 })])
  assert.ok(internal.detectSensitiveValues({ token: "sk-secret-value" }).findings.length > 0)
  assert.equal(typeof internal.createLifecycleE4Client, "function")
  assert.equal(typeof internal.createEndpointScopedE4Client, "function")
  assert.equal(typeof internal.createLocalEndpointScopedTransport, "function")
})
