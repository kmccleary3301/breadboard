import test from "node:test"
import assert from "node:assert/strict"

import {
  buildSupportClaimView,
  buildTerminalSupportSummaryView,
  buildTerminalInteractionView,
  buildBackboneTerminalInteractionView,
  buildBackboneEffectiveToolSurfaceAnalysisView,
  buildBackboneTerminalEndView,
  buildBackboneTerminalCleanupView,
  buildBackboneTerminalOutputView,
  buildBackboneEffectiveToolSurfaceView,
  buildBackboneTerminalSessionView,
  buildBackboneLiveTerminalRegistryView,
  buildBackboneTerminalCleanupResult,
  buildBackboneTerminalRegistryView,
  buildTerminalCleanupView,
  buildTerminalEndView,
  buildTerminalOutputView,
  buildEffectiveToolSurfaceView,
  buildEffectiveToolSurfaceAnalysisView,
  buildHostProjectionEnvelope,
  buildHostResultMeta,
  buildHostTranscriptProjection,
  buildTerminalRegistryView,
  buildTerminalSessionView,
  buildProviderHostTurnView,
  resolveProviderHostTurnView,
  buildFallbackHostKitInvocation,
  buildSupportedHostKitInvocation,
  createProviderHostSession,
  createHostKit,
  emitHostProjectionCallbacks,
  normalizeHostManagedTranscript,
  normalizeHostKitSupportClaim,
} from "../src/index.js"
import { createBackbone } from "@breadboard/backbone"
import { buildWorkspaceCapabilitySet, createWorkspace } from "@breadboard/workspace"

const supportedClaim = {
  level: "supported" as const,
  summary: "Supported",
  executionProfileId: "trusted_local" as const,
  executionProfile: {
    id: "trusted_local" as const,
    summary: "Trusted local",
    placementHint: "local_process" as const,
    securityTierHint: "trusted_dev" as const,
    recommendedFor: ["local developer workflows"],
    backendHint: "inline" as const,
  },
  fallbackAvailable: false,
  unsupportedFields: [],
  evidenceMode: "replay_strict",
  recommendedHostMode: "inline" as const,
  confidence: "high" as const,
}

test("createHostKit returns a stable classify/invoke surface", async () => {
  const hostKit = createHostKit<{ id: string }, { ok: boolean }, { source: string }>({
    id: "test.hostkit",
    classify(request) {
      return {
        mode: "supported",
        request,
        unsupportedFields: [],
        supportClaim: supportedClaim,
      }
    },
    async invoke(request) {
      return buildSupportedHostKitInvocation({
        result: { ok: true },
        invocation: { source: request.id },
        supportClaim: supportedClaim,
      })
    },
  })

  assert.equal(hostKit.id, "test.hostkit")
  assert.equal(hostKit.classify({ id: "r-1" }).mode, "supported")
  assert.deepEqual(await hostKit.invoke({ id: "r-1" }), {
    mode: "supported",
    result: { ok: true },
    invocation: { source: "r-1" },
    supportClaim: {
      level: "supported",
      summary: "Supported",
      executionProfileId: "trusted_local",
      executionProfile: {
        id: "trusted_local",
        summary: "Trusted local",
        placementHint: "local_process",
        securityTierHint: "trusted_dev",
        recommendedFor: ["local developer workflows"],
        backendHint: "inline",
      },
      fallbackAvailable: false,
      unsupportedFields: [],
      evidenceMode: "replay_strict",
      recommendedHostMode: "inline",
      confidence: "high",
    },
  })
})

test("normalizeHostKitSupportClaim preserves the base claim while applying explicit overrides", () => {
  const claim = normalizeHostKitSupportClaim(supportedClaim, {
    level: "fallback",
    summary: "Fallback required",
    unsupportedFields: ["images"],
  })
  assert.equal(claim.level, "fallback")
  assert.equal(claim.summary, "Fallback required")
  assert.deepEqual(claim.unsupportedFields, ["images"])
  assert.equal(claim.executionProfile.id, "trusted_local")
})

test("buildSupportClaimView projects host-facing support metadata cleanly", () => {
  const view = buildSupportClaimView({
    ...supportedClaim,
    terminalSupport: {
      canStart: true,
      canInteract: true,
      canPoll: true,
      canList: true,
      canCleanup: true,
      streamMode: "pipes",
    },
  })
  assert.equal(view.executionProfileId, "trusted_local")
  assert.equal(view.terminalSupport?.canInteract, true)
  assert.equal(view.recommendedHostMode, supportedClaim.recommendedHostMode)
})

test("buildTerminalSupportSummaryView produces a stable terminal capability summary", () => {
  const supported = buildTerminalSupportSummaryView({
    ...supportedClaim,
    terminalSupport: {
      canStart: true,
      canInteract: true,
      canPoll: true,
      canList: true,
      canCleanup: true,
      streamMode: "pty",
    },
  })
  assert.deepEqual(supported, {
    canStart: true,
    canInteract: true,
    canPoll: true,
    canList: true,
    canCleanup: true,
    streamMode: "pty",
  })

  const unsupported = buildTerminalSupportSummaryView(supportedClaim)
  assert.deepEqual(unsupported, {
    canStart: false,
    canInteract: false,
    canPoll: false,
    canList: false,
    canCleanup: false,
    streamMode: "unknown",
  })
})

test("buildFallbackHostKitInvocation emits a fallback-mode invocation with normalized support metadata", () => {
  const invocation = buildFallbackHostKitInvocation({
    result: { ok: false },
    invocation: { source: "native-fallback" },
    supportClaim: normalizeHostKitSupportClaim(supportedClaim, {
      summary: "Fallback required",
      unsupportedFields: ["images"],
    }),
  })
  assert.equal(invocation.mode, "fallback")
  assert.equal(invocation.supportClaim.level, "fallback")
  assert.equal(invocation.supportClaim.fallbackAvailable, true)
  assert.deepEqual(invocation.supportClaim.unsupportedFields, ["images"])
})

test("createProviderHostSession preserves transcript continuity and projection state", async () => {
  let transcriptCounter = 0
  const providerSession = createProviderHostSession<
    { assistantText: string },
    { count: number },
    { summary: string }
  >({
    backboneSession: {
      descriptor: {
        sessionId: "session-1",
      },
      workspace: {} as never,
      projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
      terminals: {
        classify() {
          throw new Error("not used in this test")
        },
        async get() {
          throw new Error("not used in this test")
        },
        async start() {
          throw new Error("not used in this test")
        },
        async interact() {
          throw new Error("not used in this test")
        },
        async snapshot() {
          throw new Error("not used in this test")
        },
        async list() {
          throw new Error("not used in this test")
        },
        async cleanup() {
          throw new Error("not used in this test")
        },
        reduceRegistry() {
          throw new Error("not used in this test")
        },
        buildCleanupResult() {
          throw new Error("not used in this test")
        },
      },
      tools: {
        resolveEffectiveSurface() {
          throw new Error("not used in this test")
        },
        analyzeEffectiveSurface() {
          throw new Error("not used in this test")
        },
        buildEffectiveSurface() {
          throw new Error("not used in this test")
        },
        resolveBindings() {
          throw new Error("not used in this test")
        },
      },
      classifyProviderTurn() {
        return supportedClaim
      },
      classifyToolTurn() {
        return supportedClaim
      },
      async runProviderTurn(input) {
        transcriptCounter += 1
        return {
          supportClaim: supportedClaim,
          projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
          runContextId: `run-${transcriptCounter}`,
          transcript: {
            schemaVersion: "bb.session_transcript.v1",
            sessionId: "session-1",
            metadata: {
              preserved_prefix_items: transcriptCounter - 1,
            },
            items: [
              {
                kind: "assistant_message",
                visibility: "model",
                content: { text: input.assistantText },
              },
            ],
          },
          events: [],
          providerTurn: undefined,
          driverTurn: undefined,
          unsupportedCase: undefined,
        }
      },
      async runToolTurn() {
        throw new Error("not used in this test")
      },
    },
    buildInput(input, transcript) {
      return {
        request: {
          schema_version: "bb.run_request.v1",
          request_id: `request-${transcriptCounter + 1}`,
          entry_mode: "test",
          task: input.assistantText,
          workspace_root: null,
          requested_model: null,
          requested_features: {},
          metadata: {
            prior_items: transcript?.items.length ?? 0,
          },
        },
        providerExchange: {
          schema_version: "bb.provider_exchange.v1",
          exchange_id: `exchange-${transcriptCounter + 1}`,
          request: {
            provider_family: "openai",
            runtime_id: "responses",
            route_id: null,
            model: "mock",
            stream: true,
            message_count: 1,
            tool_count: 0,
            metadata: {},
          },
          response: {
            message_count: 1,
            finish_reasons: ["stop"],
            usage: null,
            metadata: {},
          },
        },
        assistantText: input.assistantText,
        existingTranscript: transcript ?? [],
      }
    },
    projectTurn(turn, context) {
      const nextCount = (context.previousState?.count ?? 0) + 1
      return {
        state: { count: nextCount },
        output: {
          summary: `${context.resumed ? "resume" : "turn"}:${turn.runContextId}`,
        },
      }
    },
  })

  const first = await providerSession.runProviderTurn({ assistantText: "hello" })
  assert.equal(first.supportClaim.level, "supported")
  assert.equal(first.projectionOutput?.summary, "turn:run-1")
  assert.equal(providerSession.transcript?.metadata?.preserved_prefix_items, 0)
  assert.equal(providerSession.projectionState?.count, 1)

  const second = await providerSession.continueProviderTurn({ assistantText: "continue" })
  assert.equal(second.projectionOutput?.summary, "resume:run-2")
  assert.equal(providerSession.transcript?.metadata?.preserved_prefix_items, 1)
  assert.equal(providerSession.projectionState?.count, 2)

  const view = buildProviderHostTurnView(second)
  assert.equal(view.supportClaim.level, "supported")
  assert.equal(view.projectionOutput?.summary, "resume:run-2")
  assert.equal(view.projectionState?.count, 2)
})

test("buildHostTranscriptProjection extracts assistant text and tool previews", () => {
  const projection = buildHostTranscriptProjection({
    transcript: {
      schemaVersion: "bb.session_transcript.v1",
      sessionId: "session-1",
      items: [
        {
          kind: "assistant_message",
          visibility: "model",
          content: { text: "hello" },
        },
        {
          kind: "tool_result",
          visibility: "model",
          content: { parts: [{ preview: "pwd => /tmp/project" }] },
        },
      ],
    },
  })

  assert.deepEqual(projection, {
    entries: [
      { kind: "assistant_text", text: "hello" },
      { kind: "tool_preview", text: "pwd => /tmp/project" },
    ],
    assistantTexts: ["hello"],
    toolPreviews: ["pwd => /tmp/project"],
  })
})

test("Host Kit can build terminal and tool-surface views", () => {
  const sessionView = buildTerminalSessionView({
    schema_version: "bb.terminal_session_descriptor.v1",
    terminal_session_id: "term-raw-1",
    command: ["bash", "-lc", "echo hi"],
    stream_mode: "pipes",
    persistence_scope: "thread",
    continuation_scope: "both",
    public_handles: [{ namespace: "host", label: "pid", value: 42, audience: "host" }],
  }, {
    exitCode: 0,
    durationMs: 125,
    artifactRefs: [{ artifactId: "artifact://terminal/stdout/1", kind: "generic", location: "artifact://terminal/stdout/1" }],
    evidenceRefs: ["evidence://terminal/1"],
  })
  assert.equal(sessionView.commandSummary, "bash -lc echo hi")
  assert.equal(sessionView.publicHandles[0]?.label, "pid")
  assert.equal(sessionView.exitCode, 0)
  assert.equal(sessionView.artifactRefs?.[0]?.location, "artifact://terminal/stdout/1")

  const terminalView = buildTerminalRegistryView({
    schema_version: "bb.terminal_registry_snapshot.v1",
    snapshot_id: "term-reg:1",
    active_sessions: [
      {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: "term-1",
        command: ["bash", "-lc", "sleep 30"],
        stream_mode: "pipes",
        persistence_scope: "thread",
        continuation_scope: "model",
      },
    ],
    ended_session_ids: ["term-0"],
  })
  assert.equal(terminalView.activeSessions[0]?.commandSummary, "bash -lc sleep 30")

  const workspace = createWorkspace({
    workspaceId: "ws-term",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const outputView = buildTerminalOutputView({
    outputDeltas: [
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-1",
        stream: "stdout",
        chunk_b64: Buffer.from("ready\n", "utf8").toString("base64"),
        chunk_seq: 0,
      },
    ],
    workspace,
  })
  assert.equal(outputView.text, "ready\n")
  assert.equal(outputView.shape?.chunkCount, 1)

  const cleanupView = buildTerminalCleanupView({
    schema_version: "bb.terminal_cleanup_result.v1",
    cleanup_id: "cleanup-raw-1",
    scope: "filtered",
    cleaned_session_ids: ["term-1"],
    failed_session_ids: ["term-2"],
  })
  assert.equal(cleanupView.cleanedCount, 1)
  assert.equal(cleanupView.failedCount, 1)

  const endView = buildTerminalEndView({
    terminalState: "completed",
    exitCode: 0,
    durationMs: 125,
    artifactRefs: [{ artifactId: "artifact://terminal/stdout/1", kind: "generic", location: "artifact://terminal/stdout/1" }],
    evidenceRefs: ["evidence://terminal/1"],
  })
  assert.equal(endView.terminalState, "completed")
  assert.equal(endView.artifactRefs[0]?.location, "artifact://terminal/stdout/1")

  const surfaceView = buildEffectiveToolSurfaceView({
    schema_version: "bb.effective_tool_surface.v1",
    surface_id: "surface-1",
    tool_ids: ["cuda.profile.capture"],
    binding_ids: ["bind-1"],
    hidden_tool_ids: ["firecrawl.local"],
  })
  assert.deepEqual(surfaceView.visibleToolIds, ["cuda.profile.capture"])
  assert.deepEqual(surfaceView.hiddenToolIds, ["firecrawl.local"])

  const analysisView = buildEffectiveToolSurfaceAnalysisView({
    surface: {
      schema_version: "bb.effective_tool_surface.v1",
      surface_id: "surface-analysis-1",
      tool_ids: ["exec_command"],
      binding_ids: ["bind-term-fallback"],
      hidden_tool_ids: ["firecrawl.local.search"],
    },
    visibleEntries: [
      {
        toolId: "exec_command",
        bindingId: "bind-term-fallback",
        bindingKind: "sandbox",
        level: "supported",
        summary: "trusted local terminal available",
        exposedToModel: true,
        selectedViaFallback: true,
        hiddenReason: null,
        resolutionPath: ["bind-term-primary", "bind-term-fallback"],
      },
    ],
    hiddenEntries: [
      {
        toolId: "firecrawl.local.search",
        bindingId: "bind-firecrawl-hidden",
        bindingKind: "service",
        level: "hidden",
        summary: "bound but hidden",
        exposedToModel: false,
        selectedViaFallback: false,
        hiddenReason: "provider_native_hidden",
        resolutionPath: ["bind-firecrawl-hidden"],
      },
    ],
    unsupportedEntries: [],
  })
  assert.equal(analysisView.visibleEntries[0]?.selectedViaFallback, true)
  assert.equal(analysisView.hiddenEntries[0]?.hiddenReason, "provider_native_hidden")
})

test("Host Kit can project terminal and tool surfaces through Backbone", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "session-1" })

  const terminalView = buildBackboneTerminalRegistryView(session, [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-1",
      runId: "run-1",
      sessionId: "session-1",
      seq: 1,
      ts: "2026-03-10T00:00:00Z",
      actor: "tool",
      visibility: "host",
      kind: "terminal_session_begin",
      payload: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: "term-1",
        command: ["python", "-m", "http.server"],
        stream_mode: "pipes",
        persistence_scope: "thread",
        continuation_scope: "both",
      },
    },
  ])
  assert.equal(terminalView.activeSessions[0]?.terminalSessionId, "term-1")

  const cleanup = buildBackboneTerminalCleanupResult(session, {
    cleanupId: "cleanup-1",
    scope: "all",
    cleanedSessionIds: ["term-1"],
  })
  assert.deepEqual(cleanup.cleaned_session_ids, ["term-1"])
  const cleanupView = buildBackboneTerminalCleanupView(session, {
    cleanupId: "cleanup-1",
    scope: "all",
    cleanedSessionIds: ["term-1"],
  })
  assert.equal(cleanupView.cleanedCount, 1)
  assert.deepEqual(cleanupView.cleanedSessionIds, ["term-1"])

  const outputView = buildBackboneTerminalOutputView(session, [
    {
      schema_version: "bb.terminal_output_delta.v1",
      terminal_session_id: "term-1",
      stream: "stdout",
      chunk_b64: Buffer.from("ready\\n", "utf8").toString("base64"),
      chunk_seq: 0,
    },
  ])
  assert.equal(outputView.text, "ready\\n")
  assert.equal(outputView.shape?.chunkCount, 1)

  const view = buildBackboneTerminalSessionView({
    descriptor: {
      schema_version: "bb.terminal_session_descriptor.v1",
      terminal_session_id: "term-1",
      command: ["python", "-m", "http.server"],
      stream_mode: "pipes",
      persistence_scope: "thread",
      continuation_scope: "both",
    },
    supportClaim: session.terminals.classify({}),
    executionProfileId: "trusted_local",
    status: "running",
    lastSnapshot: {
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: "snap-1",
      active_sessions: [],
      ended_session_ids: [],
    },
    lastEnd: null,
    summary() {
      return {
        terminalSessionId: "term-1",
        commandSummary: "python -m http.server",
        status: "running",
        publicHandles: [],
        outputPreview: "ready",
        outputChunkCount: 1,
        persistenceScope: "thread",
        continuationScope: "both",
        lastSnapshotId: "snap-1",
        lastEndState: null,
        exitCode: null,
        durationMs: null,
        artifactRefCount: 0,
        evidenceRefCount: 0,
      }
    },
    async refresh() {
      throw new Error("not used in this test")
    },
    async poll() {
      throw new Error("not used in this test")
    },
    async writeStdin() {
      throw new Error("not used in this test")
    },
    async sendSignal() {
      throw new Error("not used in this test")
    },
    async snapshot() {
      throw new Error("not used in this test")
    },
    async cleanup() {
      throw new Error("not used in this test")
    },
  })
  assert.equal(view.outputPreview, "ready")
  assert.equal(view.lastSnapshotId, "snap-1")
  assert.equal(view.support?.level, "supported")

  const interactionView = buildBackboneTerminalInteractionView(session, {
    supportClaim: {
      ...session.terminals.classify({}),
      terminalSupport: {
        canStart: true,
        canInteract: true,
        canPoll: true,
        canList: true,
        canCleanup: true,
        streamMode: "pipes",
      },
    },
    unsupportedCase: undefined,
    interaction: {
      schema_version: "bb.terminal_interaction.v1",
      terminal_session_id: "term-1",
      startup_call_id: "call-start-1",
      causing_call_id: "call-write-1",
      interaction_kind: "stdin",
      input_b64: Buffer.from("status\n", "utf8").toString("base64"),
    },
    outputDeltas: [
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-1",
        stream: "stdout",
        chunk_b64: Buffer.from("ok\n", "utf8").toString("base64"),
        chunk_seq: 0,
      },
    ],
    end: undefined,
  })
  assert.equal(interactionView.terminalSessionId, "term-1")
  assert.equal(interactionView.output.shape?.chunkCount, 1)
  assert.equal(interactionView.support?.terminalSupport?.canInteract, true)

  const endView = buildBackboneTerminalEndView(session, {
    terminal_state: "completed",
    exit_code: 0,
    duration_ms: 25,
    artifact_refs: ["artifact://terminal/stdout/1"],
    evidence_refs: ["evidence://terminal/1"],
  })
  assert.equal(endView.exitCode, 0)
  assert.equal(endView.artifactRefs[0]?.artifactId, "artifact://terminal/stdout/1")

  const surfaceView = buildBackboneEffectiveToolSurfaceView(session, {
    surfaceId: "surface-1",
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-1",
        tool_id: "firecrawl.local",
        binding_kind: "service",
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "firecrawl.local",
        binding_id: "bind-1",
        level: "supported",
        summary: "available",
        exposed_to_model: true,
      },
    ],
  })
  assert.deepEqual(surfaceView.visibleToolIds, ["firecrawl.local"])

  const analysisView = buildBackboneEffectiveToolSurfaceAnalysisView(session, {
    surfaceId: "surface-2",
    profileId: "trusted_local",
    features: ["terminal_sessions"],
    bindings: [
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-exec-primary",
        tool_id: "exec_command",
        binding_kind: "sandbox",
        environment_selector: { profile_ids: ["sandboxed_local"] },
        fallback_binding_ids: ["bind-exec-fallback"],
      },
      {
        schema_version: "bb.tool_binding.v1",
        binding_id: "bind-exec-fallback",
        tool_id: "exec_command",
        binding_kind: "host",
        environment_selector: { profile_ids: ["trusted_local"], features: ["terminal_sessions"] },
      },
    ],
    claims: [
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-exec-primary",
        level: "unsupported",
        summary: "sandbox terminal unavailable",
        exposed_to_model: false,
        hidden_reason: "selector_mismatch",
      },
      {
        schema_version: "bb.tool_support_claim.v1",
        tool_id: "exec_command",
        binding_id: "bind-exec-fallback",
        level: "supported",
        summary: "local terminal available",
        exposed_to_model: true,
      },
    ],
  })
  assert.equal(analysisView.visibleEntries[0]?.selectedViaFallback, true)
  assert.deepEqual(analysisView.visibleEntries[0]?.resolutionPath, ["bind-exec-primary", "bind-exec-fallback"])
})

test("Host Kit can derive a live terminal registry view from Backbone", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-live-term",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "session-live-term" })

  const testSession = {
    ...session,
    terminals: {
      ...session.terminals,
      async list() {
        return {
          supportClaim: session.terminals.classify({}),
          unsupportedCase: undefined,
          snapshot: {
            schema_version: "bb.terminal_registry_snapshot.v1" as const,
            snapshot_id: "snap-live-1",
            active_sessions: [
              {
                schema_version: "bb.terminal_session_descriptor.v1" as const,
                terminal_session_id: "term-live-1",
                command: ["node", "-e", "console.log('ready')"],
                stream_mode: "pipes" as const,
                persistence_scope: "thread" as const,
                continuation_scope: "model" as const,
              },
            ],
            ended_session_ids: [],
          },
        }
      },
    },
  }

  const liveView = await buildBackboneLiveTerminalRegistryView(testSession)
  assert.ok(liveView)
  if (!liveView) {
    throw new Error("expected terminal registry view")
  }
  assert.ok(liveView.activeSessions.length >= 1)
  assert.equal(liveView.activeSessions[0]?.terminalSessionId, "term-live-1")
  assert.equal(liveView.activeSessions[0]?.support, null)
})

test("Host Kit can resolve terminal session, output, and cleanup views consistently", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-hostkit",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })

  const sessionView = buildTerminalSessionView({
    schema_version: "bb.terminal_session_descriptor.v1",
    terminal_session_id: "term-view-1",
    command: ["bash", "-lc", "python profiler.py"],
    stream_mode: "pty",
    persistence_scope: "thread",
    continuation_scope: "both",
    startup_call_id: "call-start-1",
  })
  assert.equal(sessionView.terminalSessionId, "term-view-1")
  assert.equal(sessionView.commandSummary, "bash -lc python profiler.py")
  assert.equal(sessionView.publicHandles.length, 0)

  const outputView = buildTerminalOutputView({
    outputDeltas: [
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-view-1",
        startup_call_id: "call-start-1",
        causing_call_id: "call-poll-1",
        stream: "stdout",
        chunk_b64: Buffer.from("trace ready\n", "utf8").toString("base64"),
        chunk_seq: 0,
      },
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-view-1",
        startup_call_id: "call-start-1",
        causing_call_id: "call-poll-1",
        stream: "stdout",
        chunk_b64: Buffer.from("capturing...\n", "utf8").toString("base64"),
        chunk_seq: 1,
      },
    ],
    workspace,
  })
  assert.equal(outputView.text, "trace ready\ncapturing...\n")
  assert.equal(outputView.shape?.chunkCount, 2)

  const cleanupView = buildTerminalCleanupView({
    schema_version: "bb.terminal_cleanup_result.v1",
    cleanup_id: "cleanup-view-1",
    scope: "filtered",
    cleaned_session_ids: ["term-view-1"],
    failed_session_ids: ["term-view-2"],
  })
  assert.equal(cleanupView.cleanedCount, 1)
  assert.equal(cleanupView.failedCount, 1)
  assert.deepEqual(cleanupView.failedSessionIds, ["term-view-2"])
})

test("Host Kit can build terminal interaction views", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-terminal-interaction",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })

  const interactionView = buildTerminalInteractionView({
    interaction: {
      terminal_session_id: "term-int-1",
      interaction_kind: "stdin",
    },
    outputDeltas: [
      {
        schema_version: "bb.terminal_output_delta.v1",
        terminal_session_id: "term-int-1",
        stream: "stdout",
        chunk_b64: Buffer.from("build complete\n", "utf8").toString("base64"),
        chunk_seq: 0,
      },
    ],
    end: {
      terminal_state: "completed",
      exit_code: 0,
      duration_ms: 42,
      artifact_refs: ["artifact://terminal/stdout/int-1"],
      evidence_refs: ["evidence://terminal/int-1"],
    },
    support: buildSupportClaimView({
      ...supportedClaim,
      terminalSupport: {
        canStart: true,
        canInteract: true,
        canPoll: true,
        canList: true,
        canCleanup: true,
        streamMode: "pipes",
      },
    }),
    workspace,
  })

  assert.equal(interactionView.terminalSessionId, "term-int-1")
  assert.equal(interactionView.interactionKind, "stdin")
  assert.equal(interactionView.output.text, "build complete\n")
  assert.equal(interactionView.ended?.terminalState, "completed")
  assert.equal(interactionView.support?.terminalSupport?.streamMode, "pipes")
})

test("emitHostProjectionCallbacks replays projected assistant text, tool previews, and agent events", async () => {
  const seen: string[] = []
  await emitHostProjectionCallbacks(
    {
      async onAssistantMessageStart() {
        seen.push("assistant:start")
      },
      async onPartialReply(payload) {
        seen.push(`assistant:${payload.text}`)
      },
      async onToolResult(payload) {
        seen.push(`tool:${payload.text}`)
      },
      async onAgentEvent(payload) {
        seen.push(`event:${payload.stream}`)
      },
    },
    {
      entries: [
        { kind: "assistant_text", text: "hello" },
        { kind: "tool_preview", text: "pwd => /tmp/project" },
      ],
      assistantTexts: ["hello"],
      toolPreviews: ["pwd => /tmp/project"],
    },
    [{ stream: "provider_response", data: { ok: true } }],
  )

  assert.deepEqual(seen, [
    "assistant:start",
    "assistant:hello",
    "tool:pwd => /tmp/project",
    "event:provider_response",
  ])
})

test("buildHostProjectionEnvelope pairs transcript projection with a host-shaped result", () => {
  const envelope = buildHostProjectionEnvelope({
    transcriptSource: {
      transcript: {
        schemaVersion: "bb.session_transcript.v1",
        sessionId: "session-1",
        items: [{ kind: "assistant_message", visibility: "model", content: { text: "hello" } }],
      },
    },
    result: { ok: true },
  })

  assert.deepEqual(envelope, {
    transcript: {
      entries: [{ kind: "assistant_text", text: "hello" }],
      assistantTexts: ["hello"],
      toolPreviews: [],
    },
    result: { ok: true },
  })
})

test("buildHostResultMeta produces reusable host-facing result metadata", () => {
  assert.deepEqual(
    buildHostResultMeta({
      sessionId: "session-1",
      provider: "openai",
      model: "openai/gpt-5.4-mini",
      stopReason: "completed",
      usage: { output_tokens: 32 },
    }),
    {
      durationMs: 0,
      agentMeta: {
        sessionId: "session-1",
        provider: "openai",
        model: "openai/gpt-5.4-mini",
        usage: { output_tokens: 32 },
      },
      stopReason: "completed",
    },
  )
})

test("resolveProviderHostTurnView fills missing projection output and state", () => {
  const resolved = resolveProviderHostTurnView({
    result: {
      supportClaim: supportedClaim,
      turn: {
        supportClaim: supportedClaim,
        projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
        runContextId: "run-1",
        transcript: {
          schemaVersion: "bb.session_transcript.v1",
          sessionId: "session-1",
          items: [],
        },
        events: [],
        providerTurn: undefined,
        driverTurn: undefined,
        unsupportedCase: undefined,
      },
      projectionOutput: null,
      projectionState: null,
    },
    fallbackProjectionOutput: [],
    fallbackProjectionState: {
      lastMessageId: "run-1",
      transcriptDigest: null,
      turnCount: 0,
    },
  })

  assert.deepEqual(resolved.projectionOutput, [])
  assert.deepEqual(resolved.projectionState, {
    lastMessageId: "run-1",
    transcriptDigest: null,
    turnCount: 0,
  })
})

test("normalizeHostManagedTranscript wraps host-owned transcript items in the canonical envelope", () => {
  const transcript = normalizeHostManagedTranscript("session-1", [
    {
      kind: "assistant_message",
      visibility: "model",
      content: { text: "hello" },
    },
  ])

  assert.deepEqual(transcript, {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: "session-1",
    items: [
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: "hello" },
      },
    ],
  })
})
