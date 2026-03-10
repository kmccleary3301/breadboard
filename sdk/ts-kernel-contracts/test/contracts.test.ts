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
  assert.doesNotThrow(() => assertValid("runContext", loadJson("../../contracts/kernel/examples/run_context_minimal.json")))
  assert.doesNotThrow(() => assertValid("providerExchange", loadJson("../../contracts/kernel/examples/provider_exchange_minimal.json")))
  assert.doesNotThrow(() =>
    assertValid("executionCapability", loadJson("../../contracts/kernel/examples/execution_capability_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("executionPlacement", loadJson("../../contracts/kernel/examples/execution_placement_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("sandboxRequest", loadJson("../../contracts/kernel/examples/sandbox_request_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("sandboxResult", loadJson("../../contracts/kernel/examples/sandbox_result_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "distributedTaskDescriptor",
      loadJson("../../contracts/kernel/examples/distributed_task_descriptor_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "transcriptContinuationPatch",
      loadJson("../../contracts/kernel/examples/transcript_continuation_patch_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid("unsupportedCase", loadJson("../../contracts/kernel/examples/unsupported_case_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "terminalSessionDescriptor",
      loadJson("../../contracts/kernel/examples/terminal_session_descriptor_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid("terminalOutputDelta", loadJson("../../contracts/kernel/examples/terminal_output_delta_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("terminalInteraction", loadJson("../../contracts/kernel/examples/terminal_interaction_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("terminalSessionEnd", loadJson("../../contracts/kernel/examples/terminal_session_end_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "terminalRegistrySnapshot",
      loadJson("../../contracts/kernel/examples/terminal_registry_snapshot_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "terminalCleanupResult",
      loadJson("../../contracts/kernel/examples/terminal_cleanup_result_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid("environmentSelector", loadJson("../../contracts/kernel/examples/environment_selector_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("toolBinding", loadJson("../../contracts/kernel/examples/tool_binding_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("toolSupportClaim", loadJson("../../contracts/kernel/examples/tool_support_claim_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("effectiveToolSurface", loadJson("../../contracts/kernel/examples/effective_tool_surface_minimal.json")),
  )
  assert.doesNotThrow(() => assertValid("toolSpec", loadJson("../../contracts/kernel/examples/tool_spec_minimal.json")))
  assert.doesNotThrow(() => assertValid("task", loadJson("../../contracts/kernel/examples/task_minimal.json")))
  assert.doesNotThrow(() => assertValid("checkpointMetadata", loadJson("../../contracts/kernel/examples/checkpoint_metadata_minimal.json")))
})

test("kernel validator rejects malformed run request", () => {
  assert.equal(kernelValidators.runRequest({ schema_version: "bb.run_request.v1", request_id: "r1", entry_mode: "interactive" }), false)
})

test("kernel validator accepts tracked engine conformance manifest", () => {
  assert.doesNotThrow(() =>
    assertValid(
      "engineConformanceManifest",
      loadJson("../../conformance/engine_fixtures/python_reference_manifest_v1.json"),
    ),
  )
})
