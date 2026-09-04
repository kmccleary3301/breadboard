import test from "node:test"
import assert from "node:assert/strict"
import { createExecutionWorld } from "@breadboard/execution-drivers"
import {
  analyzeEffectiveToolSurface,
  buildTerminalInteractionEvent,
  buildTerminalOutputDeltaEvents,
  buildTerminalSessionBeginEvent,
  buildTerminalSessionEndEvent,
  buildExecutionCapabilityFromRunRequest,
  buildExecutionPlacement,
  buildEffectiveToolSurface,
  buildConformanceSummary,
  buildTerminalCleanupResult,
  buildKernelEventId,
  createKernelExecutionWorld,
  executeDriverMediatedToolTurn,
  buildRunContextFromRequest,
  buildTranscriptContinuationPatch,
  buildTaskLineage,
  buildUnsupportedCase,
  cloneCheckpointMetadata,
  cloneTranscriptItem,
  eventBelongsToSession,
  executeProviderTextTurn,
  executeProviderTextContinuationTurn,
  executeScriptedToolTurn,
  executeStaticTextTurn,
  loadEngineConformanceManifest,
  loadKernelFixture,
  normalizeTranscriptContractItem,
  normalizeTranscriptContractItems,
  reduceTerminalRegistry,
  resolveToolBindings,
} from "../src/index.js"

function expectRecord(value: unknown, label: string): Record<string, unknown> {
  assert.equal(value !== null && typeof value === "object" && !Array.isArray(value), true, `${label} must be an object`)
  return value as Record<string, unknown>
}


test("kernel core helpers remain deterministic", () => {
  assert.equal(buildKernelEventId("run-1", 7), "run-1:evt:7")

  const original = {
    kind: "assistant_message",
    visibility: "model",
    content: { text: "hello" },
  } as const
  const cloned = cloneTranscriptItem(original)
  assert.deepEqual(cloned, original)
  assert.notEqual(cloned, original)

  const event = {
    schemaVersion: "bb.kernel_event.v1",
    eventId: "evt-1",
    runId: "run-1",
    sessionId: "sess-1",
    seq: 1,
    ts: "2026-03-08T00:00:00Z",
    actor: "engine",
    visibility: "model",
    kind: "assistant_message",
    payload: {},
  } as const
  assert.equal(eventBelongsToSession(event, "sess-1"), true)
  assert.equal(eventBelongsToSession(event, "sess-2"), false)
})

test("kernel core transcript normalization maps legacy entries", () => {
  const normalized = normalizeTranscriptContractItem({ assistant: "hello" })
  assert.deepEqual(normalized, {
    kind: "assistant_message",
    visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
    content: "hello",
    content_schema_version: null,
    provenance: { source: "import", source_ref: "legacy_transcript_entry:assistant" },
  })
})

test("kernel core transcript normalization maps arrays of mixed items", () => {
  const normalized = normalizeTranscriptContractItems([
    { user: "seed user" },
    {
      kind: "assistant_message",
      visibility: "model",
      content: { text: "seed assistant" },
    },
  ])
  assert.deepEqual(normalized, [
    {
      kind: "user_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: "seed user",
      content_schema_version: null,
      provenance: { source: "import", source_ref: "legacy_transcript_entry:user" },
    },
    {
      kind: "assistant_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: { text: "seed assistant" },
      content_schema_version: null,
      provenance: { source: "import", source_ref: null },
    },
  ])
})
test("kernel core lineage and checkpoint helpers stay deterministic", () => {
  const lineage = buildTaskLineage(
    {
      schema_version: "bb.task.v1",
      task_id: "child",
      kind: "subagent_spawned",
      status: "running",
      parent_task_id: "root",
    },
    {
      root: {
        schema_version: "bb.task.v1",
        task_id: "root",
        kind: "root",
        status: "running",
      },
    },
  )
  assert.deepEqual(lineage, ["root", "child"])

  const checkpoint = cloneCheckpointMetadata({
    schema_version: "bb.checkpoint_metadata.v1",
    source_kind: "workspace_checkpoint",
    checkpoint_ref: "ckpt-1",
    created_at: 1,
    summary: { preview: "test" },
  })
  assert.equal(checkpoint.summary.preview, "test")
})

test("kernel core can load tracked manifest and fixtures", () => {
  const manifest = loadEngineConformanceManifest()
  assert.equal(manifest.schemaVersion, "bb.engine_conformance_manifest.v1")
  assert.ok(manifest.rows.length >= 1)

  const fixture = loadKernelFixture<{ fixture_id: string }>("kernel_event/reference_fixture.json")
  assert.equal(fixture.fixture_id, "kernel_event_python_reference")
})

test("kernel core can build cross-engine conformance summary", () => {
  const summary = buildConformanceSummary()
  assert.equal(summary.schemaVersion, "bb.kernel_conformance_summary.v1")
  assert.ok(summary.manifestRows >= 1)
  assert.ok(summary.fixtureFamilies.includes("kernel_event"))
  assert.ok(summary.comparatorClasses.includes("normalized-trace-equal"))
})

test("kernel core can reduce terminal registry and effective tool surface", () => {
  const registry = reduceTerminalRegistry([
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-1",
      runId: "run-1",
      sessionId: "sess-1",
      seq: 1,
      ts: "2026-03-10T00:00:00Z",
      actor: "tool",
      visibility: "host",
      kind: "terminal_session_begin",
      payload: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: "term-1",
        command: ["bash", "-lc", "sleep 30"],
        stream_mode: "pipes",
        persistence_scope: "thread",
        continuation_scope: "model",
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-2",
      runId: "run-1",
      sessionId: "sess-1",
      seq: 2,
      ts: "2026-03-10T00:00:01Z",
      actor: "tool",
      visibility: "host",
      kind: "terminal_output_delta",
      payload: {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-1",
        stream: "stdout",
        chunk_b64: "aGVsbG8K",
        chunk_seq: 0,
      },
    },
  ])
  assert.equal(registry.schema_version, "bb.terminal_registry_snapshot.v1")
  assert.equal(registry.active_sessions.length, 1)

  const cleanup = buildTerminalCleanupResult({
    cleanupId: "clean-1",
    scope: "all",
    cleanedSessionIds: ["term-1"],
  })
  assert.equal(cleanup.cleaned_session_ids[0], "term-1")

  const surface = buildEffectiveToolSurface({
    surfaceId: "surface-1",
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-1",
        tool_id: "cuda.profile.capture",
        binding_kind: "sandbox",
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "cuda.profile.capture",
        binding_id: "bind-1",
        level: "supported",
        summary: "available",
        fallback_available: false,
        exposed_to_model: true,
      },
    ],
    projectionProfileId: "host_callbacks",
  })
  assert.deepEqual(surface.tool_ids, ["cuda.profile.capture"])
})

test("kernel core can resolve tool bindings through packs, fallbacks, and hidden provider-native surfaces", () => {
  const resolved = resolveToolBindings({
    surfaceId: "surface-bindings-1",
    profileId: "trusted_local",
    providerFamily: "openai",
    driverClass: "local_process",
    features: ["terminal_sessions"],
    serviceIds: ["firecrawl-local"],
    toolPacks: [
      {
        packId: "codex.background-terminals",
        toolIds: ["exec_command", "write_stdin"],
        bindingIds: ["bind-term-primary", "bind-stdin-primary", "bind-term-fallback"],
      },
    ],
    activePackIds: ["codex.background-terminals"],
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-term-primary",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["sandboxed_local"],
        },
        fallback_binding_ids: ["bind-term-fallback"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-term-fallback",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["trusted_local"],
          features: ["terminal_sessions"],
        },
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-stdin-primary",
        tool_id: "write_stdin",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["trusted_local"],
          features: ["terminal_sessions"],
        },
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-firecrawl-hidden",
        tool_id: "firecrawl.local.search",
        binding_kind: "service",
        environment_selector: {
          service_ids: ["firecrawl-local"],
        },
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-term-primary",
        level: "unsupported",
        summary: "sandboxed local terminal unavailable",
        fallback_available: true,
        hidden_reason: "selector_mismatch",
        exposed_to_model: false,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-term-fallback",
        level: "supported",
        summary: "trusted local terminal available",
        fallback_available: false,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "write_stdin",
        binding_id: "bind-stdin-primary",
        level: "supported",
        summary: "terminal continuation available",
        fallback_available: false,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "firecrawl.local.search",
        binding_id: "bind-firecrawl-hidden",
        level: "supported",
        summary: "bound but hidden from provider-native surface",
        fallback_available: false,
        exposed_to_model: false,
      },
    ],
  })

  assert.deepEqual(
    resolved.map((binding) => [binding.toolId, binding.binding.binding_id]),
    [
      ["exec_command", "bind-term-fallback"],
      ["write_stdin", "bind-stdin-primary"],
    ],
  )
  assert.equal(resolved[0]?.selectedViaFallback, true)
  assert.deepEqual(resolved[0]?.resolutionPath, ["bind-term-primary", "bind-term-fallback"])

  const analysis = analyzeEffectiveToolSurface({
    surfaceId: "surface-bindings-1",
    profileId: "trusted_local",
    providerFamily: "openai",
    driverClass: "local_process",
    features: ["terminal_sessions"],
    serviceIds: ["firecrawl-local"],
    toolPacks: [
      {
        packId: "codex.background-terminals",
        toolIds: ["exec_command", "write_stdin"],
        bindingIds: ["bind-term-primary", "bind-stdin-primary", "bind-term-fallback"],
      },
      {
        packId: "firecrawl.local",
        toolIds: ["firecrawl.local.search"],
        bindingIds: ["bind-firecrawl-hidden"],
        environmentSelector: {
          service_ids: ["firecrawl-local"],
        },
      },
    ],
    activePackIds: ["codex.background-terminals", "firecrawl.local"],
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-term-primary",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["sandboxed_local"],
        },
        fallback_binding_ids: ["bind-term-fallback"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-term-fallback",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["trusted_local"],
          features: ["terminal_sessions"],
        },
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-stdin-primary",
        tool_id: "write_stdin",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["trusted_local"],
          features: ["terminal_sessions"],
        },
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-firecrawl-hidden",
        tool_id: "firecrawl.local.search",
        binding_kind: "service",
        environment_selector: {
          service_ids: ["firecrawl-local"],
        },
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-term-primary",
        level: "unsupported",
        summary: "sandboxed local terminal unavailable",
        fallback_available: true,
        hidden_reason: "selector_mismatch",
        exposed_to_model: false,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-term-fallback",
        level: "supported",
        summary: "trusted local terminal available",
        fallback_available: false,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "write_stdin",
        binding_id: "bind-stdin-primary",
        level: "supported",
        summary: "terminal continuation available",
        fallback_available: false,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "firecrawl.local.search",
        binding_id: "bind-firecrawl-hidden",
        level: "hidden",
        summary: "bound but hidden from provider-native surface",
        fallback_available: false,
        hidden_reason: "provider_native_hidden",
        exposed_to_model: false,
      },
    ],
  })
  assert.equal(analysis.visibleEntries.length, 2)
  assert.equal(analysis.hiddenEntries[0]?.toolId, "firecrawl.local.search")
  assert.equal(analysis.visibleEntries[0]?.selectedViaFallback, true)
})

test("kernel core resolves tool packs deterministically across pack defaults and fallback chains", () => {
  const analysis = analyzeEffectiveToolSurface({
    surfaceId: "surface-bindings-deterministic",
    profileId: "sandboxed_local",
    providerFamily: "openai",
    driverClass: "oci",
    features: ["cuda", "profiling"],
    toolPacks: [
      {
        packId: "cuda.profiler",
        toolIds: ["cuda.flamegraph", "cuda.trace"],
        defaultBindingIds: ["bind-cuda-trace-primary", "bind-cuda-flamegraph-primary"],
        environmentSelector: {
          profile_ids: ["sandboxed_local"],
          features: ["cuda", "profiling"],
        },
      },
    ],
    activePackIds: ["cuda.profiler"],
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-cuda-trace-primary",
        tool_id: "cuda.trace",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["sandboxed_local"],
          features: ["cuda", "profiling"],
        },
        fallback_binding_ids: ["bind-cuda-trace-service"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-cuda-trace-service",
        tool_id: "cuda.trace",
        binding_kind: "service",
        environment_selector: {
          service_ids: ["cuda-profiler-sidecar"],
        },
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-cuda-flamegraph-primary",
        tool_id: "cuda.flamegraph",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["sandboxed_local"],
          features: ["cuda", "profiling"],
        },
        fallback_binding_ids: ["bind-cuda-flamegraph-alt"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-cuda-flamegraph-alt",
        tool_id: "cuda.flamegraph",
        binding_kind: "sandbox",
        environment_selector: {
          profile_ids: ["sandboxed_local"],
          features: ["cuda", "profiling"],
        },
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "cuda.trace",
        binding_id: "bind-cuda-trace-primary",
        level: "supported",
        summary: "CUDA trace is available in sandbox",
        fallback_available: true,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "cuda.trace",
        binding_id: "bind-cuda-trace-service",
        level: "degraded",
        summary: "Fallback sidecar trace available",
        fallback_available: false,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "cuda.flamegraph",
        binding_id: "bind-cuda-flamegraph-primary",
        level: "supported",
        summary: "Flamegraph capture available",
        fallback_available: true,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "cuda.flamegraph",
        binding_id: "bind-cuda-flamegraph-alt",
        level: "supported",
        summary: "Alternate flamegraph capture available",
        fallback_available: false,
        exposed_to_model: true,
      },
    ],
  })

  assert.deepEqual(
    analysis.visibleEntries.map((entry) => [entry.toolId, entry.bindingId]),
    [
      ["cuda.flamegraph", "bind-cuda-flamegraph-primary"],
      ["cuda.trace", "bind-cuda-trace-primary"],
    ],
  )
  assert.equal(analysis.visibleEntries[0]?.selectedViaFallback, false)
  assert.equal(analysis.visibleEntries[1]?.selectedViaFallback, false)
})

test("kernel core ignores cyclic fallback chains and preserves hidden unsupported surfaces deterministically", () => {
  const analysis = analyzeEffectiveToolSurface({
    surfaceId: "surface-bindings-cycle",
    profileId: "trusted_local",
    providerFamily: "openai",
    driverClass: "local_process",
    toolPacks: [
      {
        packId: "terminal.control",
        toolIds: ["exec_command", "write_stdin", "provider.native.secret"],
        bindingIds: ["bind-exec-a", "bind-exec-b", "bind-provider-hidden"],
      },
    ],
    activePackIds: ["terminal.control"],
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-exec-a",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        fallback_binding_ids: ["bind-exec-b"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-exec-b",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        fallback_binding_ids: ["bind-exec-a"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-provider-hidden",
        tool_id: "provider.native.secret",
        binding_kind: "provider_hosted",
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-exec-a",
        level: "supported",
        summary: "Exec command available",
        fallback_available: true,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-exec-b",
        level: "supported",
        summary: "Alternate exec command available",
        fallback_available: true,
        exposed_to_model: true,
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "provider.native.secret",
        binding_id: "bind-provider-hidden",
        level: "hidden",
        summary: "Provider native secret is hidden from the model",
        fallback_available: false,
        hidden_reason: "provider_native_hidden",
        exposed_to_model: false,
      },
    ],
  })

  assert.deepEqual(
    analysis.visibleEntries.map((entry) => [entry.toolId, entry.bindingId]),
    [["exec_command", "bind-exec-a"]],
  )
  assert.deepEqual(
    analysis.hiddenEntries.map((entry) => [entry.toolId, entry.bindingId, entry.hiddenReason]),
    [["provider.native.secret", "bind-provider-hidden", "provider_native_hidden"]],
  )
})

test("kernel core can wrap terminal lifecycle payloads into canonical events", () => {
  const descriptor = {
    schema_version: "bb.terminal_session_descriptor.v1",
    terminal_session_id: "term-2",
    startup_call_id: "call-start-2",
    command: ["bash", "-lc", "sleep 30"] as string[],
    stream_mode: "pipes",
    persistence_scope: "thread",
    continuation_scope: "both",
  } as const
  const begin = buildTerminalSessionBeginEvent(
    {
      runId: "run-term-2",
      sessionId: "sess-term-2",
      eventId: "evt-term-begin",
      seq: 1,
      ts: "2026-03-10T01:00:00Z",
      callId: "call-start-2",
    },
    descriptor,
  )
  const interaction = buildTerminalInteractionEvent(
    {
      runId: "run-term-2",
      sessionId: "sess-term-2",
      eventId: "evt-term-interact",
      seq: 2,
      ts: "2026-03-10T01:00:01Z",
      callId: "call-continue-2",
    },
    {
      schema_version: "bb.terminal_interaction.v1",
      terminal_session_id: "term-2",
      startup_call_id: "call-start-2",
      causing_call_id: "call-continue-2",
      interaction_kind: "stdin",
      input_b64: Buffer.from("hello\n", "utf8").toString("base64"),
      signal: null,
    },
  )
  const deltas = buildTerminalOutputDeltaEvents(
    {
      runId: "run-term-2",
      sessionId: "sess-term-2",
      ts: "2026-03-10T01:00:02Z",
      callId: "call-continue-2",
      startingSeq: 3,
      eventIdForSeq: (seq) => `evt-term-delta-${seq}`,
    },
    [
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-2",
        startup_call_id: "call-start-2",
        causing_call_id: "call-continue-2",
        stream: "stdout",
        chunk_b64: Buffer.from("echo:hello\n", "utf8").toString("base64"),
        chunk_seq: 0,
      },
    ],
  )
  const end = buildTerminalSessionEndEvent(
    {
      runId: "run-term-2",
      sessionId: "sess-term-2",
      eventId: "evt-term-end",
      seq: 4,
      ts: "2026-03-10T01:00:03Z",
      callId: "call-continue-2",
    },
    {
      schema_version: "bb.terminal_session_end.v1",
      terminal_session_id: "term-2",
      startup_call_id: "call-start-2",
      causing_call_id: "call-continue-2",
      terminal_state: "completed",
      exit_code: 0,
    },
  )

  const registry = reduceTerminalRegistry([begin, interaction, ...deltas, end])
  assert.equal(interaction.kind, "terminal_interaction")
  assert.equal(deltas[0]?.kind, "terminal_output_delta")
  assert.equal(end.kind, "terminal_session_end")
  assert.equal(registry.active_sessions.length, 0)
  assert.deepEqual(registry.ended_session_ids, ["term-2"])
})

test("kernel core can execute a constrained static text turn", () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-static-1",
    entry_mode: "interactive",
    task: "Write a short plan.",
    requested_model: "openai/gpt-5.2",
  } as const
  const runContext = buildRunContextFromRequest(request, {
    sessionId: "sess-static-1",
    resolvedProviderRoute: "primary",
  })
  assert.equal(runContext.session_id, "sess-static-1")
  assert.equal(runContext.request_id, "run-static-1")

  const result = executeStaticTextTurn(request, {
    sessionId: "sess-static-1",
    resolvedProviderRoute: "primary",
    assistantText: "1. Inspect the repo\\n2. Draft the plan",
  })
  assert.equal(result.events.length, 2)
  assert.equal(result.events[0].kind, "user_message")
  assert.equal(result.events[1].kind, "assistant_message")
  assert.equal(result.transcript.items.length, 2)
  assert.deepEqual(result.transcript.items[1]?.content, {
    text: "1. Inspect the repo\\n2. Draft the plan",
  })
})

test("kernel core can build execution capability and placement from a run request", () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-cap-1",
    entry_mode: "interactive",
    task: "inspect workspace",
    workspace_root: "/tmp/workspace",
  } as const

  const capability = buildExecutionCapabilityFromRunRequest(request, {
    capabilityId: "cap-run-cap-1",
    isolationClass: "oci",
    securityTier: "single_tenant",
    allowRunPrograms: ["rg"],
  })
  assert.equal(capability.schema_version, "bb.execution_capability.v1")
  assert.equal(capability.isolation_class, "oci")
  assert.deepEqual(capability.allow_run_programs, ["rg"])

  const placement = buildExecutionPlacement(capability, {
    placementId: "placement-1",
    placementClass: "local_oci",
    runtimeId: "docker.oci",
  })
  assert.equal(placement.schema_version, "bb.execution_placement.v1")
  assert.equal(placement.placement_class, "local_oci")
  assert.equal(placement.capability_id, "cap-run-cap-1")
})

test("kernel core can execute a constrained scripted tool turn", () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-tool-1",
    entry_mode: "interactive",
    task: "Run the calculator tool.",
  } as const

  const result = executeScriptedToolTurn(request, {
    sessionId: "sess-tool-1",
    toolCall: {
      schema_version: "bb.tool_call.v2",
      call_id: "call-1",
      tool_name: "calculator",
      args: { expression: "2+2" },
      state: "completed",
      requested_at_utc: "2026-03-08T00:00:00Z",
      actor: { actor_kind: "host", actor_id: "test" },
      visibility: { model_visible: true, provider_visible: true, host_visible: true },
      task_id: "task-1",
    },
    toolOutcome: {
      schema_version: "bb.tool_execution_outcome.v2",
      call_id: "call-1",
      terminal_state: "completed",
      completed_at_utc: "2026-03-08T00:00:01Z",
      result: { value: 4 },
      metadata: { tool: "calculator" },
    },
    toolRender: {
      schema_version: "bb.tool_model_render.v2",
      call_id: "call-1",
      parts: [{ part_kind: "text", content: "4", truncated: false }],
      visibility: { model_visible: true, provider_visible: true, host_visible: true },
      metadata: { tool: "calculator" },
    },
    assistantText: "The calculator returned 4.",
  })

  assert.equal(result.events.length, 4)
  assert.equal(result.events[1]?.kind, "tool_call")
  assert.equal(result.events[2]?.kind, "tool_result")
  assert.equal(result.events[3]?.kind, "assistant_message")
  assert.equal(result.transcript.items.length, 3)
  assert.deepEqual(result.transcript.items[1]?.content, {
    parts: [{ part_kind: "text", content: "4", truncated: false }],
  })
})

test("kernel core can execute a driver-mediated trusted-local tool turn", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-local-1",
    entry_mode: "interactive",
    task: "Run a local formatter tool.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-local-1",
    toolName: "formatter",
    command: ["prettier", "--check", "README.md"],
    workspaceRef: "/tmp/workspace",
    allowRunPrograms: ["prettier"],
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "completed",
      placement_id: "placement-local-1",
      stdout_ref: "artifact://stdout/local-1",
      stderr_ref: "artifact://stderr/local-1",
      artifact_refs: ["artifact://report/local-1"],
      side_effect_digest: "sha256:local1",
      usage: { wall_ms: 32 },
      evidence_refs: ["evidence://local/1"],
      error: null,
    }),
  })

  assert.equal(result.driverId, "local-process")
  assert.equal(result.executionPlacement.placement_class, "local_process")
  assert.equal(result.sandboxRequest.placement_class, "local_process")
  assert.equal(result.sandboxRequest.network_policy, null)
  assert.equal(result.sandboxResult.status, "completed")
  assert.equal(result.evidenceExpectation.require_evidence_refs, true)
  assert.equal(result.sideEffectExpectation.filesystem_scope, "workspace_scoped")
  assert.equal(result.transcript.items[1]?.kind, "tool_result")
})

test("kernel core executes the default unrestricted local process path", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-local-default-network",
    entry_mode: "interactive",
    task: "Run a local command without a network policy.",
    workspace_root: "/tmp",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-local-default-network",
    toolName: "node",
    command: [process.execPath, "-e", "process.stdout.write('local default ok')"],
    workspaceRef: "/tmp",
    allowRunPrograms: [process.execPath],
  })

  assert.equal(result.sandboxRequest.network_policy, null)
  assert.equal(result.sandboxResult.status, "completed")
})

test("kernel core can execute a driver-mediated OCI tool turn", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-oci-1",
    entry_mode: "interactive",
    task: "Run a containerized linter.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-oci-1",
    toolName: "container_linter",
    command: ["ruff", "check", "."],
    workspaceRef: "/tmp/workspace",
    imageRef: "ghcr.io/example/ruff:latest",
    isolationClass: "oci",
    securityTier: "single_tenant",
    driverIdHint: "oci",
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "completed",
      placement_id: "placement-oci-1",
      stdout_ref: "artifact://stdout/oci-1",
      stderr_ref: "artifact://stderr/oci-1",
      artifact_refs: ["artifact://report/oci-1"],
      side_effect_digest: "sha256:oci1",
      usage: { wall_ms: 101 },
      evidence_refs: ["evidence://oci/1"],
      error: null,
    }),
  })

  assert.equal(result.driverId, "oci")
  assert.equal(result.executionPlacement.placement_class, "local_oci")
  assert.equal(result.sandboxRequest.image_ref, "ghcr.io/example/ruff:latest")
  assert.equal(result.sideEffectExpectation.filesystem_scope, "container_scoped")
  assert.equal(result.transcript.items.at(-1)?.kind, "assistant_message")
})

test("kernel core preserves provider-neutral equality across full Session product events for local and OCI worlds under timestamp-only mask", async () => {
  const definitionRequest = {
    schema_version: "bb.run_request.v1",
    request_id: "run-lock-verify-1",
    entry_mode: "interactive",
    task: "Execute verification suite.",
    workspace_root: "/tmp/workspace",
  } as const

  const localWorldResult = await executeDriverMediatedToolTurn(definitionRequest, {
    sessionId: "sess-lock-verify-1",
    toolName: "pytest_runner",
    command: ["pytest", "tests/unit"],
    workspaceRef: "/tmp/workspace",
    imageRef: "docker://breadboard/python-base:latest",
    isolationClass: "process",
    securityTier: "trusted_dev",
    startedAt: "2026-03-08T12:00:00.000Z",
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "completed",
      placement_id: `local-process:${sandboxRequest.request_id}`,
      stdout_ref: "sha256:pytest-stdout-digest",
      stderr_ref: "sha256:pytest-stderr-digest",
      artifact_refs: ["artifact://pytest-report.xml"],
      side_effect_digest: "sha256:pytest-run-digest",
      usage: { exit_code: 0 },
      evidence_refs: ["evidence://test/pytest-1"],
      error: null,
    }),
  })

  const ociWorldResult = await executeDriverMediatedToolTurn(definitionRequest, {
    sessionId: "sess-lock-verify-1",
    toolName: "pytest_runner",
    command: ["pytest", "tests/unit"],
    workspaceRef: "/tmp/workspace",
    imageRef: "docker://breadboard/python-base:latest",
    isolationClass: "oci",
    securityTier: "single_tenant",
    startedAt: "2026-03-08T12:00:00.000Z",
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "completed",
      placement_id: `oci:${sandboxRequest.request_id}`,
      stdout_ref: "sha256:pytest-stdout-digest",
      stderr_ref: "sha256:pytest-stderr-digest",
      artifact_refs: ["artifact://pytest-report.xml"],
      side_effect_digest: "sha256:pytest-run-digest",
      usage: { exit_code: 0 },
      evidence_refs: ["evidence://test/pytest-1"],
      error: null,
    }),
  })

  // Strip ONLY exact occurred_at and timestamp fields
  const stripExactTimestamps = (obj: unknown): unknown => {
    if (obj === null || typeof obj !== "object") return obj
    if (Array.isArray(obj)) return obj.map(stripExactTimestamps)
    const result: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (key === "occurred_at_utc" || key === "occurred_at" || key === "timestamp" || key === "timestamp_utc") {
        continue
      }
      result[key] = stripExactTimestamps(value)
    }
    return result
  }

  assert.deepEqual(
    stripExactTimestamps(localWorldResult.events),
    stripExactTimestamps(ociWorldResult.events),
  )

  assert.deepEqual(
    stripExactTimestamps(localWorldResult.transcript),
    stripExactTimestamps(ociWorldResult.transcript),
  )

  assert.equal(localWorldResult.driverId, "local-process")
  assert.equal(ociWorldResult.driverId, "oci")
  assert.equal(localWorldResult.executionPlacement.placement_class, "local_process")
  assert.equal(ociWorldResult.executionPlacement.placement_class, "local_oci")
  assert.ok(localWorldResult.sandboxResult.placement_id?.startsWith("local-process:"))
  assert.ok(ociWorldResult.sandboxResult.placement_id?.startsWith("oci:"))
  assert.equal(localWorldResult.sandboxRequest.image_ref, null)
  assert.equal(ociWorldResult.sandboxRequest.image_ref, "docker://breadboard/python-base:latest")
})

test("kernel core handles pre-aborted signal in driver-mediated tool turn without throwing", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-preabort-1",
    entry_mode: "interactive",
    task: "Execute with pre-aborted signal.",
    workspace_root: "/tmp/workspace",
  } as const

  const abortController = new AbortController()
  abortController.abort(new Error("pre-aborted"))

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-preabort-1",
    toolName: "preabort_tool",
    command: ["echo", "unreachable"],
    workspaceRef: "/tmp/workspace",
    signal: abortController.signal,
  })

  assert.equal(result.sandboxResult.status, "cancelled")
  assert.equal((result.events.find((e) => e.kind === "tool_call")?.payload as Record<string, unknown>)?.state, "cancelled")
  assert.equal((result.events.find((e) => e.kind === "tool_result")?.payload as Record<string, unknown>)?.terminalState, "cancelled")
  assert.ok(String((result.transcript.items.at(-1)?.content as Record<string, unknown>)?.text ?? "").includes("cancelled"))
})

test("kernel core handles pre-expired deadline in driver-mediated tool turn without throwing", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-preexpired-1",
    entry_mode: "interactive",
    task: "Execute with pre-expired deadline.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-preexpired-1",
    toolName: "preexpired_tool",
    command: ["echo", "unreachable"],
    workspaceRef: "/tmp/workspace",
    deadlineMs: 0,
  })

  assert.equal(result.sandboxResult.status, "timed_out")
  assert.equal((result.events.find((e) => e.kind === "tool_call")?.payload as Record<string, unknown>)?.state, "timed_out")
  assert.equal((result.events.find((e) => e.kind === "tool_result")?.payload as Record<string, unknown>)?.terminalState, "timed_out")
  assert.ok(String((result.transcript.items.at(-1)?.content as Record<string, unknown>)?.text ?? "").includes("timed_out"))
})

test("kernel core can execute a driver-mediated remote tool turn", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-remote-1",
    entry_mode: "interactive",
    task: "Run a delegated remote audit.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-remote-1",
    toolName: "remote_audit",
    command: ["python", "audit.py"],
    workspaceRef: "/tmp/workspace",
    isolationClass: "remote_service",
    securityTier: "multi_tenant",
    driverIdHint: "remote",
    remoteHttp: {
      endpointUrl: "https://remote.example.test/execute",
      fetchImpl: async () =>
        ({
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_execution_response.v1",
            result: {
              schema_version: "bb.sandbox_result.v1",
              request_id: "run-driver-remote-1:sandbox",
              status: "completed",
              placement_id: "remote:exec:1",
              stdout_ref: "artifact://remote/stdout/1",
              stderr_ref: "artifact://remote/stderr/1",
              artifact_refs: ["artifact://remote/report/1"],
              side_effect_digest: "sha256:remoteexec1",
              usage: { wall_ms: 44 },
              evidence_refs: ["evidence://remote/1"],
              error: null,
            },
          }),
        }) as Response,
    },
  })

  assert.equal(result.driverId, "remote")
  assert.equal(result.executionPlacement.placement_class, "remote_worker")
  assert.equal(result.sandboxRequest.placement_class, "remote_worker")
  assert.equal(result.sandboxResult.placement_id, "remote:exec:1")
  assert.equal(result.sideEffectExpectation.filesystem_scope, "remote_scoped")
})

test("kernel core maps failed sandbox driver results to errored v2 tool outcomes", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-failed-1",
    entry_mode: "interactive",
    task: "Run a formatter that fails.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-failed-1",
    toolName: "formatter",
    command: ["prettier", "--check", "README.md"],
    workspaceRef: "/tmp/workspace",
    allowRunPrograms: ["prettier"],
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "failed",
      placement_id: "placement-failed-1",
      stdout_ref: "artifact://stdout/failed-1",
      stderr_ref: "artifact://stderr/failed-1",
      artifact_refs: [],
      side_effect_digest: "sha256:failed1",
      usage: { wall_ms: 18 },
      evidence_refs: ["evidence://failed/1"],
      error: { message: "formatter exited 1", code: "exit_nonzero", retryable: false },
    }),
  })

  const toolCallPayload = expectRecord(result.events[1]?.payload, "failed tool-call payload")
  const toolResultPayload = expectRecord(result.events[2]?.payload, "failed tool-result payload")
  const toolResultMetadata = expectRecord(toolResultPayload.metadata, "failed tool-result metadata")
  const toolRenderContent = expectRecord(result.transcript.items[1]?.content, "failed tool-render content")

  assert.equal(result.sandboxResult.status, "failed")
  assert.equal(result.events[1]?.kind, "tool_call")
  assert.equal(toolCallPayload.state, "failed")
  if ("schema_version" in toolCallPayload) {
    assert.equal(toolCallPayload.schema_version, "bb.tool_call.v2")
  }
  assert.equal(result.events[2]?.kind, "tool_result")
  assert.equal(toolResultPayload.terminalState, "errored")
  assert.equal(toolResultPayload.result, null)
  assert.deepEqual(toolResultPayload.error, { message: "formatter exited 1", code: "exit_nonzero", retryable: false })
  assert.equal(toolResultMetadata.sandbox_status, "failed")
  if ("schema_version" in toolResultPayload) {
    assert.equal(toolResultPayload.schema_version, "bb.tool_execution_outcome.v2")
  }
  assert.deepEqual(toolRenderContent.parts, [{ part_kind: "error", content: "formatter failed via sandbox", truncated: false }])
  if ("schema_version" in toolRenderContent) {
    assert.equal(toolRenderContent.schema_version, "bb.tool_model_render.v2")
  }
})

test("kernel core maps timed out sandbox driver results to timed_out v2 tool outcomes", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-timed-out-1",
    entry_mode: "interactive",
    task: "Run a long job that times out.",
    workspace_root: "/tmp/workspace",
  } as const

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-timed-out-1",
    toolName: "long_job",
    command: ["node", "slow-job.js"],
    workspaceRef: "/tmp/workspace",
    allowRunPrograms: ["node"],
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "timed_out",
      placement_id: "placement-timeout-1",
      stdout_ref: "artifact://stdout/timed-out-1",
      stderr_ref: "artifact://stderr/timed-out-1",
      artifact_refs: [],
      side_effect_digest: "sha256:timeout1",
      usage: { wall_ms: 30000 },
      evidence_refs: ["evidence://timed-out/1"],
      error: { message: "sandbox timed out", code: "timeout", retryable: true },
    }),
  })

  const toolCallPayload = expectRecord(result.events[1]?.payload, "timed-out tool-call payload")
  const toolResultPayload = expectRecord(result.events[2]?.payload, "timed-out tool-result payload")
  const toolResultMetadata = expectRecord(toolResultPayload.metadata, "timed-out tool-result metadata")
  const toolRenderContent = expectRecord(result.transcript.items[1]?.content, "timed-out tool-render content")

  assert.equal(result.sandboxResult.status, "timed_out")
  assert.equal(result.events[1]?.kind, "tool_call")
  assert.equal(toolCallPayload.state, "timed_out")
  if ("schema_version" in toolCallPayload) {
    assert.equal(toolCallPayload.schema_version, "bb.tool_call.v2")
  }
  assert.equal(result.events[2]?.kind, "tool_result")
  assert.equal(toolResultPayload.terminalState, "timed_out")
  assert.equal(toolResultPayload.result, null)
  assert.deepEqual(toolResultPayload.error, { message: "Execution exceeded its deadline", code: "timeout", retryable: true })
  assert.equal(toolResultMetadata.sandbox_status, "timed_out")
  if ("schema_version" in toolResultPayload) {
    assert.equal(toolResultPayload.schema_version, "bb.tool_execution_outcome.v2")
  }
  assert.deepEqual(toolRenderContent.parts, [{ part_kind: "error", content: "long_job timed_out via sandbox", truncated: false }])
  if ("schema_version" in toolRenderContent) {
    assert.equal(toolRenderContent.schema_version, "bb.tool_model_render.v2")
  }
})

test("kernel core maps cancelled sandbox driver results to cancelled v2 tool outcomes", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "req-cancelled-1",
    entry_mode: "interactive",
    task: "Run a job that gets cancelled.",
    workspace_root: "/tmp/workspace",
  } as const
  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-cancelled-1",
    toolName: "cancelled_job",
    command: ["node", "cancelled-job.js"],
    workspaceRef: "/tmp/workspace",
    allowRunPrograms: ["node"],
    executeSandbox: async (sandboxRequest) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: sandboxRequest.request_id,
      status: "cancelled",
      placement_id: "placement-cancelled-1",
      stdout_ref: null,
      stderr_ref: null,
      artifact_refs: [],
      side_effect_digest: null,
      usage: { wall_ms: 120 },
      evidence_refs: ["evidence://cancelled/1"],
      error: { message: "sandbox cancelled", code: "cancelled", retryable: false },
    }),
  })

  const toolCallPayload = expectRecord(result.events[1]?.payload, "cancelled tool-call payload")
  const toolResultPayload = expectRecord(result.events[2]?.payload, "cancelled tool-result payload")
  const toolResultMetadata = expectRecord(toolResultPayload.metadata, "cancelled tool-result metadata")
  const toolRenderContent = expectRecord(result.transcript.items[1]?.content, "cancelled tool-render content")

  assert.equal(result.sandboxResult.status, "cancelled")
  assert.equal(result.events[1]?.kind, "tool_call")
  assert.equal(toolCallPayload.state, "cancelled")
  if ("schema_version" in toolCallPayload) {
    assert.equal(toolCallPayload.schema_version, "bb.tool_call.v2")
  }
  assert.equal(result.events[2]?.kind, "tool_result")
  assert.equal(toolResultPayload.terminalState, "cancelled")
  assert.deepEqual(toolResultPayload.error, { message: "Execution was cancelled", code: "cancelled", retryable: false })
  assert.equal(toolResultMetadata.sandbox_status, "cancelled")
  if ("schema_version" in toolResultPayload) {
    assert.equal(toolResultPayload.schema_version, "bb.tool_execution_outcome.v2")
  }
  assert.deepEqual(toolRenderContent.parts, [{ part_kind: "error", content: "cancelled_job cancelled via sandbox", truncated: false }])
  if ("schema_version" in toolRenderContent) {
    assert.equal(toolRenderContent.schema_version, "bb.tool_model_render.v2")
  }
})

test("kernel core can execute a provider-aware constrained text turn", () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-provider-1",
    entry_mode: "interactive",
    task: "Summarize the repository state.",
  } as const

  const result = executeProviderTextTurn(request, {
    sessionId: "sess-provider-1",
    providerExchange: {
      schema_version: "bb.provider_exchange.v1",
      exchange_id: "px-1",
      request: {
        provider_family: "openai",
        runtime_id: "responses_api",
        route_id: "primary",
        model: "openai/gpt-5.2",
        stream: false,
        message_count: 1,
        tool_count: 0,
        metadata: { message_roles: ["user"] },
      },
      response: {
        message_count: 1,
        finish_reasons: ["stop"],
        metadata: { provider_family: "openai", runtime_id: "responses_api" },
      },
    },
    assistantText: "The repository is clean and the TS kernel substrate is in place.",
  })

  assert.equal(result.runContext.resolved_model, "openai/gpt-5.2")
  assert.equal(result.runContext.resolved_provider_route, "primary")
  assert.equal(result.events.length, 2)
  assert.equal(result.events[0]?.kind, "provider_response")
  assert.equal(result.events[1]?.kind, "assistant_message")
  assert.equal(result.transcript.items.length, 3)
  assert.equal(result.providerExchange.exchange_id, "px-1")
})

test("kernel core can continue from an existing transcript during a provider-aware turn", () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-provider-2",
    entry_mode: "interactive",
    task: "hello",
  } as const

  const result = executeProviderTextContinuationTurn(request, {
    sessionId: "sess-provider-2",
    existingTranscript: [
      { user: "seed user" },
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: "seed assistant" },
      },
    ],
    providerExchange: {
      schema_version: "bb.provider_exchange.v1",
      exchange_id: "px-2",
      request: {
        provider_family: "openai",
        runtime_id: "responses_api",
        route_id: "primary",
        model: "openai/gpt-5.2",
        stream: false,
      },
      response: {
        message_count: 1,
        finish_reasons: ["stop"],
        metadata: { provider_family: "openai", runtime_id: "responses_api" },
      },
    },
    assistantText: "hello from the continuation turn",
  })

  assert.equal(result.transcript.items.length, 5)
  assert.deepEqual(result.transcript.items.slice(0, 2), [
    {
      kind: "user_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: "seed user",
      content_schema_version: null,
      provenance: { source: "import", source_ref: "legacy_transcript_entry:user" },
    },
    {
      kind: "assistant_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: { text: "seed assistant" },
      content_schema_version: null,
      provenance: { source: "import", source_ref: null },
    },
  ])
  assert.equal(result.transcript.items[4]?.kind, "assistant_message")
  assert.deepEqual(result.transcript.metadata, {
    execution_mode: "provider_text_turn",
    source: "ts-kernel-core",
    exchange_id: "px-2",
    provider_family: "openai",
    continuation_from_existing_transcript: true,
    preserved_prefix_items: 2,
  })
  assert.equal(result.transcriptContinuationPatch?.schema_version, "bb.transcript_continuation_patch.v1")
  assert.equal(result.transcriptContinuationPatch?.appended_messages.length, 3)
})

test("kernel core can build transcript continuation patches and unsupported cases", () => {
  const patch = buildTranscriptContinuationPatch(
    {
      schema_version: "bb.session_transcript.v2",
      session_id: "sess-1",
      items: [
        {
          kind: "user_message",
          visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
          content: { text: "hi" },
          content_schema_version: null,
        },
        {
          kind: "assistant_message",
          visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
          content: { text: "hello" },
          content_schema_version: null,
        },
      ],
      event_cursor: 2,
    },
    {
      patchId: "patch-1",
      preservedPrefixItems: 1,
    },
  )
  assert.equal(patch.schema_version, "bb.transcript_continuation_patch.v1")
  assert.equal(patch.appended_messages.length, 1)

  const unsupported = buildUnsupportedCase("placement_unavailable", "microvm unavailable", {
    fallbackAllowed: true,
    fallbackTaken: true,
    unavailablePlacement: "local_microvm",
  })
  assert.equal(unsupported.schema_version, "bb.unsupported_case.v1")
  assert.equal(unsupported.fallback_taken, true)
})

test("createKernelExecutionWorld with executeSandbox observes cancellation or deadline when override responds to signal", async () => {
  const world = createKernelExecutionWorld({
    executeSandbox: async (_req, context) => {
      return new Promise((_resolve, reject) => {
        context.signal?.addEventListener("abort", () => {
          reject(new Error("override received abort signal"))
        })
      })
    },
  })

  const abortController = new AbortController()
  const promise = world.execute({
    kind: "sandbox",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-override-test",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-override-test",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-override-test",
    },
    requestId: "req-override-test-1",
    command: ["sleep", "60"],
    signal: abortController.signal,
    terminationGraceMs: 50,
  })

  await new Promise((r) => setTimeout(r, 10))
  abortController.abort(new Error("aborted by caller"))
  const result = await promise
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "cancelled")
  assert.equal(result.livenessEvidence.state, "cancelled")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
})

test("kernel override retains rejected execution until world termination cleanup", async () => {
  let receivedSignal: AbortSignal | undefined
  const world = createKernelExecutionWorld({
    executeSandbox: async (_request, context) => {
      receivedSignal = context.signal
      throw new Error("override rejected after launch")
    },
  })

  const result = await world.execute({
    kind: "sandbox",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-override-rejected-after-launch",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-override-rejected-after-launch",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-override-rejected-after-launch",
    },
    requestId: "req-override-rejected-after-launch",
    command: ["sleep", "60"],
    terminationGraceMs: 50,
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(receivedSignal?.aborted, true)
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
})

test("createKernelExecutionWorld with stubborn executeSandbox override marks terminationObserved false when override ignores signal", async () => {
  const world = createKernelExecutionWorld({
    executeSandbox: async () => {
      // Stubborn override: ignores signal and never settles
      return new Promise(() => {})
    },
  })

  const abortController = new AbortController()
  const promise = world.execute({
    kind: "sandbox",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-stubborn-test",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-stubborn-test",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-stubborn-test",
    },
    requestId: "req-stubborn-test-1",
    command: ["sleep", "60"],
    signal: abortController.signal,
    terminationGraceMs: 20,
  })

  await new Promise((r) => setTimeout(r, 10))
  abortController.abort(new Error("aborted by caller"))
  const result = await promise
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "cancelled")
  assert.equal(result.livenessEvidence.state, "cancelled")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, false)
})
test("createKernelExecutionWorld with executeSandbox does not invoke override when pre-aborted", async () => {
  let backendInvoked = false
  const world = createKernelExecutionWorld({
    executeSandbox: async () => {
      backendInvoked = true
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: "req-pre-abort",
        status: "completed",
        placement_id: "place-1",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: null,
      }
    },
  })

  const abortController = new AbortController()
  abortController.abort(new Error("pre-aborted"))
  const result = await world.execute({
    kind: "sandbox",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-pre-abort",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-pre-abort",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-pre-abort",
    },
    requestId: "req-pre-abort-1",
    command: ["echo", "test"],
    signal: abortController.signal,
  })

  assert.equal(backendInvoked, false)
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "cancelled")
  assert.equal(result.livenessEvidence.executionStarted, false)
})

test("executeDriverMediatedToolTurn passes execution context controls (signal, deadlineAtMs, terminationGraceMs) to executeSandbox callback", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-context-1",
    entry_mode: "interactive",
    task: "Verify context controls.",
    workspace_root: "/tmp/workspace",
  } as const

  let receivedSignal: AbortSignal | undefined
  let receivedDeadlineAtMs: number | null | undefined
  let receivedGraceMs: number | undefined

  const controller = new AbortController()

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-driver-context-1",
    toolName: "inspector",
    command: ["test", "--dry-run"],
    workspaceRef: "/tmp/workspace",
    signal: controller.signal,
    timeoutMs: 5000,
    terminationGraceMs: 250,
    executeSandbox: async (sandboxRequest, context) => {
      receivedSignal = context.signal
      receivedDeadlineAtMs = context.deadlineAtMs
      receivedGraceMs = context.terminationGraceMs
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: sandboxRequest.request_id,
        status: "completed",
        placement_id: "placement-context-1",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: "sha256:context1",
        error: null,
      }
    },
  })

  assert.equal(result.sandboxResult.status, "completed")
  assert.ok(receivedSignal !== undefined)
  assert.equal(typeof receivedDeadlineAtMs, "number")
  assert.equal(receivedGraceMs, 250)
})

test("executeDriverMediatedToolTurn rejects when selected driver has no executable backend or sandbox request builder", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-driver-unsupported-driver-1",
    entry_mode: "interactive",
    task: "Verify driver without backend throws instead of synthesizing failed result.",
    workspace_root: "/tmp/workspace",
  } as const

  const noExecuteDriver = {
    driverId: "no-execute-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: () => true,
  }
  const customWorld = createExecutionWorld({ drivers: [noExecuteDriver] })

  await assert.rejects(
    () =>
      executeDriverMediatedToolTurn(request, {
        sessionId: "sess-no-execute-1",
        toolName: "test_tool",
        command: ["echo", "hello"],
        executionWorld: customWorld,
      }),
    /No execution driver produced a sandbox request|has no executable backend/,
  )
})

test("executeDriverMediatedToolTurn on remote isolation without remote backend throws typed unsupported before sandbox result", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-remote-unconfigured-1",
    entry_mode: "interactive",
    task: "Run tool on remote backend without config",
    workspace_root: "/tmp/workspace",
  } as const

  await assert.rejects(
    () =>
      executeDriverMediatedToolTurn(request, {
        sessionId: "sess-remote-unconfigured-1",
        toolName: "remote_tool",
        command: ["python", "-c", "print(1)"],
        isolationClass: "remote_service",
        securityTier: "multi_tenant",
        driverIdHint: "remote",
      }),
    /No execution driver supports remote_worker|No execution driver supports execution capability|No execution driver produced a sandbox request/,
  )
})

test("createKernelExecutionWorld with executeSandbox override advertises and executes remote capability without remote backend config", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-remote-override-1",
    entry_mode: "interactive",
    task: "Run tool on remote capability with executeSandbox override",
    workspace_root: "/tmp/workspace",
  } as const

  let injectedCalled = false
  const world = createKernelExecutionWorld({
    executeSandbox: async (req, context) => {
      injectedCalled = true
      assert.equal(context.driverId, "remote")
      assert.equal(context.capability.isolation_class, "remote_service")
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: req.request_id,
        status: "completed",
        placement_id: "remote:override-place-1",
        stdout_ref: "sha256:remote-override-stdout",
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: "sha256:remote-override-digest",
        usage: { exit_code: 0 },
        evidence_refs: [],
        error: null,
      }
    },
  })

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-remote-override-1",
    toolName: "remote_tool",
    command: ["python", "-c", "print('remote override')"],
    isolationClass: "remote_service",
    securityTier: "multi_tenant",
    driverIdHint: "remote",
    executionWorld: world,
  })

  assert.ok(injectedCalled, "injected executeSandbox was called for remote placement")
  assert.equal(result.sandboxResult.status, "completed")
  assert.equal(result.driverId, "remote")
})

test("executeDriverMediatedToolTurn preserves image_ref, workspace_ref, and observable refs in tool call, outcome, and render", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-preserve-refs-1",
    entry_mode: "interactive",
    task: "Run tool with full observable refs",
    workspace_root: "/workspace",
  } as const

  const world = createKernelExecutionWorld({
    executeSandbox: async (req) => {
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: req.request_id,
        status: "completed",
        placement_id: "place-preserve-1",
        stdout_ref: "sha256:preserve-stdout-digest",
        stderr_ref: "sha256:preserve-stderr-digest",
        artifact_refs: ["artifact://file1.txt", "artifact://file2.txt"],
        evidence_refs: ["evidence://test/1"],
        side_effect_digest: "sha256:preserve-side-effect",
        usage: { exit_code: 0 },
        error: null,
      }
    },
  })

  const result = await executeDriverMediatedToolTurn(request, {
    sessionId: "sess-preserve-refs-1",
    toolName: "oci_tool",
    command: ["python", "-c", "print('preserve')"],
    imageRef: "docker://breadboard/custom:v1",
    workspaceRef: "/custom/workspace",
    isolationClass: "oci",
    securityTier: "single_tenant",
    driverIdHint: "oci",
    executionWorld: world,
  })

  // Verify toolCall args preserve image_ref and workspace_ref
  const toolCallEvent = result.events.find((e) => e.kind === "tool_call")
  assert.ok(toolCallEvent)
  const toolCall = toolCallEvent.payload as { args?: Record<string, unknown> }
  assert.equal(toolCall.args?.image_ref, "docker://breadboard/custom:v1")
  assert.equal(toolCall.args?.workspace_ref, "/custom/workspace")

  // Verify tool_result preserves stdout, stderr, artifact, evidence refs and side effect digest
  const toolResultEvent = result.events.find((e) => e.kind === "tool_result")
  assert.ok(toolResultEvent)
  const toolOutcome = toolResultEvent.payload as {
    result?: Record<string, unknown>
    metadata?: Record<string, unknown>
  }
  assert.equal(toolOutcome.result?.stdout_ref, "sha256:preserve-stdout-digest")
  assert.equal(toolOutcome.result?.stderr_ref, "sha256:preserve-stderr-digest")
  assert.deepEqual(toolOutcome.result?.artifact_refs, ["artifact://file1.txt", "artifact://file2.txt"])
  assert.deepEqual(toolOutcome.result?.evidence_refs, ["evidence://test/1"])
  assert.equal(toolOutcome.metadata?.side_effect_digest, "sha256:preserve-side-effect")

  // Verify transcript tool_result item preserves render parts and metadata
  const transcriptToolItem = result.transcript.items.find((item) => item.kind === "tool_result")
  assert.ok(transcriptToolItem)
  assert.equal(transcriptToolItem.metadata?.stdout_ref, "sha256:preserve-stdout-digest")
  assert.equal(transcriptToolItem.metadata?.stderr_ref, "sha256:preserve-stderr-digest")
  assert.deepEqual(transcriptToolItem.metadata?.artifact_refs, ["artifact://file1.txt", "artifact://file2.txt"])
  assert.deepEqual(transcriptToolItem.metadata?.evidence_refs, ["evidence://test/1"])
})

test("executeDriverMediatedToolTurn rejects when injected executionWorld returns sandboxResult with mismatched request_id", async () => {
  const request = {
    schema_version: "bb.run_request.v1",
    request_id: "run-hostile-world-1",
    entry_mode: "interactive",
    task: "Verify request_id identity enforcement",
    workspace_root: "/workspace",
  } as const

  const hostileWorld = {
    select: () => ({
      driverId: "hostile-driver",
      capability: {
        schema_version: "bb.execution_capability.v1" as const,
        capability_id: "cap-hostile",
        security_tier: "trusted_dev" as const,
        isolation_class: "process" as const,
        secret_mode: "ref_only" as const,
        evidence_mode: "minimal" as const,
      },
      placement: {
        schema_version: "bb.execution_placement.v1" as const,
        placement_id: "place-hostile",
        placement_class: "local_process" as const,
        runtime_id: "local",
        capability_id: "cap-hostile",
      },
    }),
    execute: async () => ({
      kind: "sandbox" as const,
      driverId: "hostile-driver",
      plan: null,
      sandboxResult: {
        schema_version: "bb.sandbox_result.v1" as const,
        request_id: "run-different-request-id:sandbox", // Mismatched request_id
        status: "completed" as const,
        placement_id: "place-hostile",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        evidence_refs: [],
        side_effect_digest: "sha256:hostile",
        usage: { exit_code: 0 },
        error: null,
      },
      livenessEvidence: {
        observed: true,
        executionStarted: true,
        state: "completed" as const,
        terminationRequested: false,
        terminationObserved: false,
        observedAtUtc: new Date().toISOString(),
        evidenceRefs: [],
      },
    }),
  }

  await assert.rejects(
    () =>
      executeDriverMediatedToolTurn(request, {
        sessionId: "sess-hostile-1",
        toolName: "hostile_tool",
        command: ["echo", "test"],
        executionWorld: hostileWorld,
      }),
    /does not match expected 'run-hostile-world-1:sandbox'/,
  )
})
