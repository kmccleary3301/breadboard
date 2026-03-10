import test from "node:test"
import assert from "node:assert/strict"

import {
  buildTerminalInteractionEvent,
  buildTerminalOutputDeltaEvents,
  buildTerminalSessionBeginEvent,
  buildTerminalSessionEndEvent,
  buildExecutionCapabilityFromRunRequest,
  buildExecutionPlacement,
  buildEffectiveToolSurface,
  buildConformanceSummary,
  buildTerminalCleanupResult,
  executeDriverMediatedToolTurn,
  buildKernelEventId,
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
} from "../src/index.js"

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
    visibility: "model",
    content: "hello",
    provenance: { source: "legacy_transcript_entry", legacy_key: "assistant" },
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
      visibility: "model",
      content: "seed user",
      provenance: { source: "legacy_transcript_entry", legacy_key: "user" },
    },
    {
      kind: "assistant_message",
      visibility: "model",
      content: { text: "seed assistant" },
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
      schemaVersion: "bb.tool_call.v1",
      callId: "call-1",
      toolName: "calculator",
      args: { expression: "2+2" },
      state: "completed",
      taskId: "task-1",
    },
    toolOutcome: {
      schemaVersion: "bb.tool_execution_outcome.v1",
      callId: "call-1",
      terminalState: "completed",
      result: { value: 4 },
      metadata: { tool: "calculator" },
    },
    toolRender: {
      schemaVersion: "bb.tool_model_render.v1",
      callId: "call-1",
      parts: [{ tool: "calculator", preview: "4", status: "ok" }],
      visibility: "model",
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
    parts: [{ tool: "calculator", preview: "4", status: "ok" }],
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
  assert.equal(result.sandboxResult.status, "completed")
  assert.equal(result.evidenceExpectation.require_evidence_refs, true)
  assert.equal(result.sideEffectExpectation.filesystem_scope, "workspace_scoped")
  assert.equal(result.transcript.items[1]?.kind, "tool_result")
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
      visibility: "model",
      content: "seed user",
      provenance: { source: "legacy_transcript_entry", legacy_key: "user" },
    },
    {
      kind: "assistant_message",
      visibility: "model",
      content: { text: "seed assistant" },
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
      schemaVersion: "bb.session_transcript.v1",
      sessionId: "sess-1",
      items: [
        { kind: "user_message", visibility: "model", content: { text: "hi" } },
        { kind: "assistant_message", visibility: "model", content: { text: "hello" } },
      ],
      eventCursor: 2,
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
