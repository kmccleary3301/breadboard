import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"

import { assertValid, kernelValidators } from "../src/index.js"

function loadJson(relPath: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), relPath), "utf8"))
}

test("kernel validators accept tracked examples", () => {
  assert.doesNotThrow(() => assertValid("kernelEvent", loadJson("../../contracts/kernel/examples/kernel_event_minimal.json")))
  assert.doesNotThrow(() => assertValid("runRequest", loadJson("../../contracts/kernel/examples/run_request_minimal.json")))
  assert.doesNotThrow(() => assertValid("providerExchange", loadJson("../../contracts/kernel/examples/provider_exchange_minimal.json")))
  assert.doesNotThrow(() => assertValid("task", loadJson("../../contracts/kernel/examples/task_minimal.json")))
  assert.doesNotThrow(() => assertValid("checkpointMetadata", loadJson("../../contracts/kernel/examples/checkpoint_metadata_minimal.json")))
})

test("kernel validator rejects malformed run request", () => {
  assert.equal(kernelValidators.runRequest({ schema_version: "bb.run_request.v1", request_id: "r1", entry_mode: "interactive" }), false)
})
