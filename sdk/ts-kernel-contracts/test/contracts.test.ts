import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"

import { assertValid, kernelValidators } from "../src/index.js"
import { GENERATED_SCHEMAS } from "../src/generated/index.js"

const PROVIDER_EXCHANGE_V2_SCHEMA_ID =
  "https://breadboard.dev/contracts/kernel/schemas/bb.provider_exchange.v2.schema.json"
const providerExchangeV2Validator = GENERATED_SCHEMAS[PROVIDER_EXCHANGE_V2_SCHEMA_ID]?.validate
if (!providerExchangeV2Validator) throw new Error("generated provider exchange v2 validator is missing")

function loadJson(relPath: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), relPath), "utf8"))
}

test("kernel validators accept tracked examples", () => {
  assert.doesNotThrow(() => assertValid("kernelEvent", loadJson("../../contracts/kernel/examples/kernel_event_minimal.json")))
  assert.doesNotThrow(() => assertValid("kernelEventV2", loadJson("../../contracts/kernel/examples/kernel_event_v2_minimal.json")))
  assert.doesNotThrow(() => assertValid("runRequest", loadJson("../../contracts/kernel/examples/run_request_minimal.json")))
  assert.doesNotThrow(() => assertValid("runContext", loadJson("../../contracts/kernel/examples/run_context_minimal.json")))
  assert.doesNotThrow(() => assertValid("providerExchange", loadJson("../../contracts/kernel/examples/provider_exchange_minimal.json")))
  for (const filename of [
    "provider_exchange_v2_done.json",
    "provider_exchange_v2_error.json",
    "provider_exchange_v2_cancelled.json",
  ]) {
    const value = loadJson(`../../contracts/kernel/examples/${filename}`)
    assert.equal(providerExchangeV2Validator(value), true, JSON.stringify(providerExchangeV2Validator.errors))
  }
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
  assert.doesNotThrow(() => assertValid("signal", loadJson("../../contracts/kernel/examples/signal_complete_minimal.json")))
  assert.doesNotThrow(() =>
    assertValid("reviewVerdict", loadJson("../../contracts/kernel/examples/review_verdict_complete_validated.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("reviewVerdict", loadJson("../../contracts/kernel/examples/review_verdict_blocked_retry.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("directive", loadJson("../../contracts/kernel/examples/directive_retry_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("wakeSubscription", loadJson("../../contracts/kernel/examples/wake_subscription_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid(
      "distributedTaskDescriptor",
      loadJson("../../contracts/kernel/examples/distributed_task_descriptor_minimal.json"),
    ),
  )
  assert.doesNotThrow(() =>
    assertValid("signal", loadJson("../../contracts/kernel/examples/signal_complete_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("signal", loadJson("../../contracts/kernel/examples/signal_blocked_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("wakeSubscription", loadJson("../../contracts/kernel/examples/wake_subscription_minimal.json")),
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
  assert.doesNotThrow(() => assertValid("toolSpecV2", loadJson("../../contracts/kernel/examples/tool_spec_v2_minimal.json")))
  assert.doesNotThrow(() => assertValid("toolCallV2", loadJson("../../contracts/kernel/examples/tool_call_v2_minimal.json")))
  assert.doesNotThrow(() =>
    assertValid("toolExecutionOutcomeV2", loadJson("../../contracts/kernel/examples/tool_execution_outcome_v2_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("toolModelRenderV2", loadJson("../../contracts/kernel/examples/tool_model_render_v2_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("sessionTranscriptV2", loadJson("../../contracts/kernel/examples/session_transcript_v2_minimal.json")),
  )
  assert.doesNotThrow(() =>
    assertValid("coordinationSliceV2", loadJson("../../contracts/kernel/examples/coordination_slice_v2_minimal.json")),
  )
  assert.doesNotThrow(() => assertValid("task", loadJson("../../contracts/kernel/examples/task_minimal.json")))
  assert.doesNotThrow(() => assertValid("checkpointMetadata", loadJson("../../contracts/kernel/examples/checkpoint_metadata_minimal.json")))
})

test("kernel validator rejects malformed run request", () => {
  assert.equal(kernelValidators.runRequest({ schema_version: "bb.run_request.v1", request_id: "r1", entry_mode: "interactive" }), false)
})

test("provider exchange v2 validator rejects Python-incompatible wire values", () => {
  interface MutableContentBlock extends Record<string, unknown> {
    payload?: Record<string, unknown>
  }
  interface MutableProviderExchangeFixture {
    exchange_id: string
    provider: { route_id: string; model: string }
    correlation: { turn_id: string }
    request: {
      messages: Array<{ content: MutableContentBlock[] }>
      tools: Array<Record<string, unknown>>
    }
    events: Array<Record<string, unknown>>
    terminal: {
      output_emitted: boolean
      raw_provider_finish: string
      evidence_refs: string[]
      usage: { extensions: Record<string, unknown> }
      provider_replay: Array<{ payload: Record<string, unknown> }>
    }
  }
  interface ProviderExchangeParityCases {
    argument_cases: Array<{
      name: string
      arguments_json: string
      arguments: unknown
      accepted: boolean
    }>
    replay_cases: Array<{
      name: string
      field: string
      value: unknown
      accepted: boolean
    }>
    wire_shape_cases: Array<{
      name: string
      path: Array<string | number>
      operation: "delete" | "set"
      value?: unknown
      accepted: boolean
    }>

  }
  const loaded = loadJson("../../contracts/kernel/examples/provider_exchange_v2_done.json")
  assert.equal(providerExchangeV2Validator(loaded), true)
  const base = loaded as unknown as MutableProviderExchangeFixture
  const reject = (label: string, mutate: (value: MutableProviderExchangeFixture) => void) => {
    const value = structuredClone(base)
    mutate(value)
    assert.equal(providerExchangeV2Validator(value), false, label)
  }

  const parityCases = loadJson(
    "../../tests/fixtures/provider_exchange_v2_parity_cases.json",
  ) as ProviderExchangeParityCases
  for (const parityCase of parityCases.argument_cases) {
    const value = structuredClone(base)
    value.request.messages[3].content[0].arguments_json = parityCase.arguments_json
    value.request.messages[3].content[0].arguments = parityCase.arguments
    assert.equal(
      providerExchangeV2Validator(value),
      parityCase.accepted,
      parityCase.name,
    )
  }
  for (const parityCase of parityCases.replay_cases) {
    const value = structuredClone(base)
    value.terminal.provider_replay[0].payload[parityCase.field] = parityCase.value
    assert.equal(
      providerExchangeV2Validator(value),
      parityCase.accepted,
      parityCase.name,
    )
  }
  for (const parityCase of parityCases.wire_shape_cases) {
    const value = structuredClone(base)
    let target = value as unknown as Record<string | number, unknown>
    for (const part of parityCase.path.slice(0, -1)) {
      target = target[part] as Record<string | number, unknown>
    }
    const field = parityCase.path.at(-1)
    assert.notEqual(field, undefined)
    if (parityCase.operation === "delete") {
      delete target[field!]
    } else {
      target[field!] = structuredClone(parityCase.value)
    }
    assert.equal(
      providerExchangeV2Validator(value),
      parityCase.accepted,
      parityCase.name,
    )
  }

  reject("whitespace exchange identity", (value) => { value.exchange_id = " " })
  reject("noncanonical route", (value) => { value.provider.route_id = " route" })
  reject("noncanonical model", (value) => { value.provider.model = "模型" })
  reject("whitespace correlation", (value) => { value.correlation.turn_id = " \t" })
  reject("whitespace redacted data", (value) => { value.request.messages[2].content[1].data = " " })
  reject("whitespace tool description", (value) => { value.request.tools[0].description = " " })
  reject("whitespace replay schema", (value) => { value.request.messages[3].content[1].schema_version = " " })
  reject("whitespace replay identity", (value) => { value.request.messages[3].content[1].payload!.item_id = "\n" })
  reject("whitespace event delta", (value) => { value.events[2].delta = "\n" })
  reject("invalid provider finish token", (value) => { value.terminal.raw_provider_finish = "complete now" })
  reject("whitespace evidence ref", (value) => { value.terminal.evidence_refs = [" "] })
  reject("mismatched tool arguments", (value) => {
    value.events[9].arguments = { path: "OTHER.md" }
  })
  reject("oversized replay collection", (value) => {
    value.terminal.provider_replay[0].payload.signature = Array.from({ length: 33 }, (_, index) => index)
  })
  reject("deep replay value", (value) => {
    value.terminal.provider_replay[0].payload.signature = [[[[["too-deep"]]]]]
  })
  reject("oversized replay bytes", (value) => {
    value.terminal.provider_replay[0].payload.signature = "é".repeat(4096)
  })
  reject("oversized usage extension bytes", (value) => {
    value.terminal.usage.extensions = {
      first: "é".repeat(4096),
      second: "é".repeat(4096),
      third: "é".repeat(4096),
    }
  })
  reject("sequence gap", (value) => { value.events[1].sequence = 2 })
  reject("duplicate response start", (value) => { value.events[1] = { sequence: 1, kind: "response_start" } })
  reject("unclosed done content", (value) => {
    value.events.splice(3, 1)
    value.events.forEach((event, index) => { event.sequence = index })
  })
  reject("false output claim", (value) => { value.terminal.output_emitted = false })
})

test("kernel validator accepts a coordination verification result payload", () => {
  assert.doesNotThrow(() =>
    assertValid("coordinationVerificationResult", {
      schema_version: "bb.coordination_verification_result.v1",
      subject_signal_id: "signal_worker_complete_1",
      subject_task_id: "task_worker_1",
      validator_task_id: "task_verifier_1",
      status: "pass",
      verification_artifact_refs: ["artifact://verification/pass.json"],
      summary: "Verifier confirmed worker deliverable.",
    }),
  )
})

test("kernel validator accepts tracked engine conformance manifest", () => {
  assert.doesNotThrow(() =>
    assertValid(
      "engineConformanceManifest",
      loadJson("../../conformance/engine_fixtures/python_reference_manifest_v1.json"),
    ),
  )
})
