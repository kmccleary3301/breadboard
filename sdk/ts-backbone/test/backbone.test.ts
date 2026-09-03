import test from "node:test"
import assert from "node:assert/strict"

import { createBackbone } from "../src/index.js"
import { createExecutionWorld } from "@breadboard/execution-drivers"
import { buildWorkspaceCapabilitySet, createWorkspace } from "@breadboard/workspace"
import type { KernelEventV1, ProviderExchangeV1, RunRequestV1, SandboxRequestV1 } from "@breadboard/kernel-contracts"

const request: RunRequestV1 = {
  schema_version: "bb.run_request.v1",
  request_id: "req-1",
  entry_mode: "hostkit",
  task: "Say hello",
  workspace_root: "/tmp/project",
  requested_model: "openai/gpt-5.4-mini",
  requested_features: {},
  metadata: { host: "test" },
}

const exchange: ProviderExchangeV1 = {
  schema_version: "bb.provider_exchange.v1",
  exchange_id: "ex-1",
  request: {
    provider_family: "openai",
    runtime_id: "responses",
    route_id: "default",
    model: "openai/gpt-5.4-mini",
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
}

test("createBackbone opens a session with a projection profile", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1", workspaceRoot: "/tmp/project" })
  assert.equal(session.projectionProfile.id, "host_callbacks")
})

test("classifyProviderTurn returns a supported claim on the default profile", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const claim = session.classifyProviderTurn({ request, providerExchange: exchange, assistantText: "hello" })
  assert.equal(claim.level, "supported")
  assert.equal(claim.executionProfileId, "trusted_local")
  assert.equal(claim.executionProfile.backendHint, "inline")
  assert.equal(claim.recommendedHostMode, "inline")
  assert.equal(claim.confidence, "high")
})

test("runProviderTurn returns transcript and provider turn output", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const result = await session.runProviderTurn({
    request,
    providerExchange: exchange,
    assistantText: "hello from provider",
  })
  assert.equal(result.providerTurn?.providerExchange.exchange_id, "ex-1")
  assert.equal(result.transcript.items.length, 3)
  assert.deepEqual(result.coordinationInspection, {
    signals: [],
    review_verdicts: [],
    directives: [],
    latest_signal_by_code: {},
    unresolved_interventions: [],
    resolved_interventions: [],
  })
})

test("classifyToolTurn advertises remote profile when requested", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet({ canRunRemoteIsolated: true }),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const claim = session.classifyToolTurn({
    request,
    toolName: "ls",
    command: ["pwd"],
    driverIdHint: "remote",
  })
  assert.equal(claim.executionProfileId, "remote_isolated")
  assert.equal(claim.recommendedHostMode, "background")
})

test("inspectCoordination derives read-only intervention state from kernel events", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const events: KernelEventV1[] = [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-1",
      runId: "run-1",
      sessionId: "s-1",
      seq: 1,
      ts: "2026-03-13T12:00:00Z",
      actor: "engine",
      visibility: "host",
      kind: "coordination.signal",
      payload: {
        schema_version: "bb.signal.v1",
        signal_id: "signal-human-1",
        code: "human_required",
        task_id: "task_worker_1",
        parent_task_id: "task_supervisor_1",
        mission_task_id: "task_supervisor_1",
        authority_scope: "task",
        status: "accepted",
        source: { kind: "runtime", emitter_role: "runtime", detail: null },
        evidence_refs: ["evidence://human-required"],
        payload: {
          required_input: "Approve guarded retry",
          blocking_reason: "Operator approval required",
          allowed_host_actions: ["continue", "checkpoint", "terminate"],
        },
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-2",
      runId: "run-1",
      sessionId: "s-1",
      seq: 2,
      ts: "2026-03-13T12:00:01Z",
      actor: "engine",
      visibility: "host",
      kind: "coordination.review_verdict",
      payload: {
        schema_version: "bb.review_verdict.v1",
        verdict_id: "review-1",
        reviewer_task_id: "task_supervisor_1",
        reviewer_role: "supervisor",
        subject: {
          kind: "signal",
          signal_id: "signal-human-1",
          signal_code: "human_required",
          source_task_id: "task_worker_1",
          mission_task_id: "task_supervisor_1",
        },
        verdict_code: "human_required",
        mission_completed: false,
        required_deliverable_refs: [],
        deliverable_refs: [],
        missing_deliverable_refs: [],
        blocking_reason: "Operator approval required",
        recommended_next_action: null,
        support_claim_ref: null,
        signal_evidence_refs: ["evidence://human-required"],
        metadata: {},
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-3",
      runId: "run-1",
      sessionId: "s-1",
      seq: 3,
      ts: "2026-03-13T12:00:02Z",
      actor: "human",
      visibility: "host",
      kind: "coordination.directive",
      payload: {
        schema_version: "bb.directive.v1",
        directive_id: "directive-host-1",
        directive_code: "continue",
        issuer_task_id: "host::task_supervisor_1",
        issuer_role: "host",
        target_task_id: "task_worker_1",
        target_job_id: "job_worker_1",
        based_on_verdict_id: "review-1",
        based_on_signal_id: "signal-human-1",
        payload: { wake_target: true, intervention_response: true },
        evidence_refs: [],
        metadata: {},
      },
    },
  ]

  const snapshot = session.inspectCoordination({ events })
  assert.equal(snapshot.latest_signal_by_code.human_required?.signal_id, "signal-human-1")
  assert.equal(snapshot.unresolved_interventions.length, 0)
  assert.equal(snapshot.resolved_interventions.length, 1)
  assert.deepEqual(snapshot.resolved_interventions[0].allowed_host_actions, ["continue", "checkpoint", "terminate"])
  assert.equal(snapshot.resolved_interventions[0].host_responses[0]?.directive_code, "continue")
  assert.equal(session.inspectCoordination().resolved_interventions.length, 1)
})

test("BackboneSession exposes terminal and effective-tool-surface helpers", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })

  const registry = session.terminals.reduceRegistry([
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-1",
      runId: "run-1",
      sessionId: "s-1",
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
  ])
  assert.equal(registry.active_sessions.length, 1)

  const cleanup = session.terminals.buildCleanupResult({
    cleanupId: "cleanup-1",
    scope: "single",
    cleanedSessionIds: ["term-1"],
  })
  assert.deepEqual(cleanup.cleaned_session_ids, ["term-1"])

  const surface = session.tools.buildEffectiveSurface({
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
  })
  assert.deepEqual(surface.tool_ids, ["cuda.profile.capture"])
})

test("BackboneSession can classify and drive a local terminal session lifecycle", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-2",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-2", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const claim = session.terminals.classify({})
  assert.equal(claim.level, "supported")
  assert.equal(claim.terminalSupport?.canStart, true)

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'ready\\n'; sleep 0.5"],
  })
  assert.ok(started.descriptor)
  assert.ok(started.session)
  if (!started.session) {
    throw new Error("expected terminal session view")
  }
  const terminalSession = started.session
  assert.equal(terminalSession.descriptor.terminal_session_id, started.descriptor?.terminal_session_id)
  assert.equal(terminalSession.supportClaim.level, "supported")
  assert.equal(terminalSession.executionProfileId, "trusted_local")
  assert.equal(terminalSession.status, "running")
  assert.equal(terminalSession.lastSnapshot, null)
  assert.equal(terminalSession.lastEnd, null)
  assert.equal(terminalSession.summary().status, "running")
  assert.match(terminalSession.summary().commandSummary, /printf 'ready/)

  const interacted = await terminalSession.poll({ settleMs: 100 })
  assert.ok(interacted.outputDeltas.length >= 1)
  assert.equal(terminalSession.summary().outputChunkCount, interacted.outputDeltas.length)
  assert.match(terminalSession.summary().outputPreview, /ready/)

  const snapshot = await terminalSession.snapshot()
  assert.ok(snapshot.snapshot)
  if (!snapshot.snapshot) {
    throw new Error("expected terminal snapshot")
  }
  assert.equal(terminalSession.summary().lastSnapshotId, snapshot.snapshot.snapshot_id)
  const listed = await session.terminals.list()
  assert.ok(listed.snapshot)
  assert.ok(listed.snapshot?.active_sessions.some((item) => item.terminal_session_id === terminalSession.descriptor.terminal_session_id))

  const refreshed = await terminalSession.refresh()
  assert.equal(refreshed.snapshot?.snapshot_id, terminalSession.summary().lastSnapshotId)

  const cleanup = await terminalSession.cleanup()
  assert.ok(cleanup.result)
  assert.deepEqual(cleanup.result.cleaned_session_ids, [terminalSession.descriptor.terminal_session_id])
  assert.equal(terminalSession.status, "ended")
  assert.equal(terminalSession.summary().status, "ended")
  assert.equal(terminalSession.summary().lastEndState, "cleaned_up")
  assert.equal(terminalSession.summary().artifactRefCount, 0)
  assert.equal(terminalSession.summary().evidenceRefCount, 0)
  assert.equal(terminalSession.summary().publicHandles.length, 1)
})

test("BackboneSession terminals can get and list multiple sessions with mixed states", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-3",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-3", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const firstStarted = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'one\\n'; sleep 0.5"],
  })
  const secondStarted = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'two\\n'; sleep 0.5"],
  })

  assert.ok(firstStarted.session)
  assert.ok(secondStarted.session)
  if (!firstStarted.session || !secondStarted.session || !firstStarted.descriptor || !secondStarted.descriptor) {
    throw new Error("expected terminal session views")
  }

  await firstStarted.session.poll({ settleMs: 25 })
  await secondStarted.session.poll({ settleMs: 25 })

  const fetchedFirst = await session.terminals.get({
    terminalSessionId: firstStarted.descriptor.terminal_session_id,
  })
  assert.equal(fetchedFirst.supportClaim.level, "supported")
  assert.ok(fetchedFirst.snapshot)
  assert.ok(fetchedFirst.session)
  assert.equal(
    fetchedFirst.session?.descriptor.terminal_session_id,
    firstStarted.descriptor.terminal_session_id,
  )

  const listWhileRunning = await session.terminals.list()
  assert.ok(listWhileRunning.snapshot)
  assert.equal(listWhileRunning.snapshot?.active_sessions.length, 2)

  const cleanedFirst = await firstStarted.session.cleanup()
  assert.ok(cleanedFirst.result)
  assert.deepEqual(cleanedFirst.result?.cleaned_session_ids, [firstStarted.descriptor.terminal_session_id])

  const listAfterCleanup = await session.terminals.list()
  assert.ok(listAfterCleanup.snapshot)
  assert.ok(
    listAfterCleanup.snapshot?.active_sessions.some(
      (item) => item.terminal_session_id === secondStarted.descriptor!.terminal_session_id,
    ),
  )

  const fetchedAfterCleanup = await session.terminals.get({
    terminalSessionId: firstStarted.descriptor.terminal_session_id,
  })
  assert.ok(fetchedAfterCleanup.session)
  assert.equal(fetchedAfterCleanup.session?.status, "ended")
  assert.equal(fetchedAfterCleanup.session?.summary().lastEndState, "cleaned_up")

  const listViewsAfterCleanup = await session.terminals.listViews()
  assert.ok(listViewsAfterCleanup.snapshot)
  assert.equal(listViewsAfterCleanup.sessions.length, 2)
  assert.equal(listViewsAfterCleanup.activeCount, 1)
  assert.equal(listViewsAfterCleanup.endedCount, 1)
  assert.equal(listViewsAfterCleanup.sessionCount, 2)
  const cleanedView = listViewsAfterCleanup.sessions.find(
    (item) => item.descriptor.terminal_session_id === firstStarted.descriptor!.terminal_session_id,
  )
  const runningView = listViewsAfterCleanup.sessions.find(
    (item) => item.descriptor.terminal_session_id === secondStarted.descriptor!.terminal_session_id,
  )
  assert.ok(cleanedView)
  assert.ok(runningView)
  assert.equal(cleanedView?.status, "ended")
  assert.equal(cleanedView?.summary().lastEndState, "cleaned_up")
  assert.equal(runningView?.status, "running")

  const cleanedSecond = await secondStarted.session.cleanup()
  assert.ok(cleanedSecond.result)
  assert.deepEqual(cleanedSecond.result?.cleaned_session_ids, [secondStarted.descriptor.terminal_session_id])
})

test("BackboneSession terminal interactions shape ended-session failures as unsupported cases", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-4",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-4", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'done\\n'; exit 0"],
  })
  assert.ok(started.session)
  assert.ok(started.descriptor)
  if (!started.session || !started.descriptor) {
    throw new Error("expected started terminal session")
  }

  await started.session.poll({ settleMs: 25 })
  const cleaned = await started.session.cleanup()
  assert.ok(cleaned.result)

  const interaction = await session.terminals.interact({
    terminalSessionId: started.descriptor.terminal_session_id,
    interactionKind: "stdin",
    inputText: "status\n",
  })
  assert.equal(interaction.interaction, null)
  assert.equal(interaction.outputDeltas.length, 0)
  assert.equal(interaction.unsupportedCase?.reason_code, "terminal_interaction_failed")
  assert.equal(
    interaction.unsupportedCase?.metadata?.terminal_session_id,
    started.descriptor.terminal_session_id,
  )

  const ended = await session.terminals.get({
    terminalSessionId: started.descriptor.terminal_session_id,
  })
  assert.ok(ended.session)
  assert.equal(ended.session?.status, "ended")
  assert.equal(ended.session?.summary().lastEndState, "cleaned_up")
})

test("BackboneSession terminal lookup shapes unknown sessions as unsupported cases", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-5",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-5", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const missing = await session.terminals.get({
    terminalSessionId: "term-missing-1",
  })
  assert.equal(missing.session, null)
  assert.equal(missing.snapshot?.active_sessions.length, 0)
  assert.equal(missing.unsupportedCase?.reason_code, "terminal_session_not_found")
  assert.equal(missing.unsupportedCase?.metadata?.terminal_session_id, "term-missing-1")
})

test("BackboneSession can signal a running terminal session and preserve cancelled end state", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-6",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-6", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "sleep 5"],
  })
  assert.ok(started.session)
  assert.ok(started.descriptor)
  if (!started.session || !started.descriptor) {
    throw new Error("expected started terminal session")
  }

  const signaled = await started.session.sendSignal("SIGTERM", {
    causingCallId: "call-signal-1",
  })
  assert.ok(signaled.interaction)
  assert.equal(signaled.interaction?.interaction_kind, "signal")
  assert.equal(signaled.interaction?.signal, "SIGTERM")

  const settled = await started.session.poll({ settleMs: 50 })
  assert.equal(settled.end?.terminal_state, "cancelled")

  const ended = await session.terminals.get({
    terminalSessionId: started.descriptor.terminal_session_id,
  })
  assert.ok(ended.session)
  assert.equal(ended.session?.status, "ended")
  assert.equal(ended.session?.summary().lastEndState, "cancelled")
})

test("BackboneSession terminal signal on an ended session shapes an unsupported case", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-7",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-7", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'done\\n'; exit 0"],
  })
  assert.ok(started.session)
  assert.ok(started.descriptor)
  if (!started.session || !started.descriptor) {
    throw new Error("expected started terminal session")
  }

  await started.session.poll({ settleMs: 25 })
  const cleaned = await started.session.cleanup()
  assert.ok(cleaned.result)

  const interaction = await started.session.sendSignal("SIGTERM", {
    causingCallId: "call-signal-dead-1",
  })
  assert.equal(interaction.interaction, null)
  assert.equal(interaction.outputDeltas.length, 0)
  assert.equal(interaction.unsupportedCase?.reason_code, "terminal_interaction_failed")
  assert.equal(interaction.unsupportedCase?.metadata?.terminal_session_id, started.descriptor.terminal_session_id)
  assert.equal(interaction.unsupportedCase?.metadata?.interaction_kind, "signal")
})

test("BackboneSession preserves session view identity and accumulated output across listViews and summary", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-views-1",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-views-1", workspaceRoot: "/tmp" })
  await session.terminals.cleanup({ scope: "all" })

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "echo 'accumulated-payload-line'; sleep 5"],
  })
  assert.ok(started.session)
  assert.ok(started.descriptor)
  if (!started.session || !started.descriptor) {
    throw new Error("expected started terminal session")
  }

  // Wait for initial output to be produced and polled
  await started.session.poll({ settleMs: 50 })
  const summaryBefore = started.session.summary()
  assert.ok(summaryBefore.outputPreview.includes("accumulated-payload-line"))
  assert.ok(summaryBefore.outputChunkCount > 0)

  // Call listViews - should NOT recreate active descriptor or wipe accumulated output/end state
  const listed = await session.terminals.listViews()
  assert.equal(listed.sessions.length, 1)
  const listedSession = listed.sessions[0]
  assert.ok(listedSession)

  const summaryAfter = started.session.summary()
  assert.ok(summaryAfter.outputPreview.includes("accumulated-payload-line"))
  assert.equal(summaryAfter.outputChunkCount, summaryBefore.outputChunkCount)

  const listedSummary = listedSession?.summary()
  assert.ok(listedSummary?.outputPreview.includes("accumulated-payload-line"))
  assert.equal(listedSummary?.outputChunkCount, summaryBefore.outputChunkCount)

  await started.session.cleanup()
})

test("BackboneSession terminal classification and start on default remote world without remoteHttp adapter is unsupported", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-remote-unsupported-1",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet({ canRunRemoteIsolated: true }),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-remote-1", workspaceRoot: "/tmp" })

  const claim = session.terminals.classify({ executionProfileId: "remote_isolated" })
  assert.equal(claim.level, "unsupported")
  assert.equal(claim.terminalSupport?.canStart, false)
  assert.equal(claim.terminalSupport?.canInteract, false)
  assert.equal(claim.terminalSupport?.canCleanup, false)
  assert.ok(claim.unsupportedFields.includes("terminal_sessions"))

  const started = await session.terminals.start({
    executionProfileId: "remote_isolated",
    command: ["echo", "test"],
  })
  assert.equal(started.session, null)
  assert.equal(started.descriptor, null)
  assert.ok(started.unsupportedCase != null)
  assert.equal(started.unsupportedCase?.reason_code, "unsupported_terminal_driver")
})

test("BackboneSession interact and cleanup pass exact terminalSessionId to resolveTerminalDriver, satisfying capability-id-gated driver", async () => {
  const targetSessionId = "session-exact-cap-1"
  const gatedDriver = {
    driverId: "gated-terminal-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: (capability: { capability_id: string }) => {
      return (
        capability.capability_id === `term-cap:${targetSessionId}` ||
        capability.capability_id === "term-cap:registry" ||
        capability.capability_id === "term-cap:all"
      )
    },
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input: { terminalSessionId: string; command: string[] }) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1" as const,
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        command: input.command,
        cwd: null,
        stream_mode: "pipes" as const,
        stream_split: "stdout_stderr" as const,
        capability_id: null,
        placement_id: null,
        persistence_scope: "thread" as const,
        continuation_scope: "both" as const,
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input: { terminalSessionId: string; interactionKind: "stdin" | "signal" }) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1" as const,
        terminal_session_id: input.terminalSessionId,
        interaction_kind: input.interactionKind,
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input: { cleanupId: string; scope: "all" | "single" | "filtered"; sessionIds?: string[] }) => ({
      schema_version: "bb.terminal_cleanup_result.v1" as const,
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.scope === "all" ? [targetSessionId] : (input.sessionIds ?? []),
      failed_session_ids: [],
    }),
  }
  const customWorld = createExecutionWorld({ drivers: [gatedDriver] })
  const workspace = createWorkspace({
    workspaceId: "ws-gated-1",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace, executionWorld: customWorld })
  const session = backbone.openSession({ sessionId: "s-gated-1", workspaceRoot: "/tmp" })

  // 1. Start with targetSessionId
  const started = await session.terminals.start({
    terminalSessionId: targetSessionId,
    command: ["bash"],
  })
  assert.ok(started.session)
  assert.equal(started.descriptor?.terminal_session_id, targetSessionId)

  // 2. Interact with targetSessionId - capability_id must match term-cap:session-exact-cap-1
  const interacted = await session.terminals.interact({
    terminalSessionId: targetSessionId,
    interactionKind: "stdin",
    inputText: "hello\n",
  })
  assert.ok(interacted.interaction)
  assert.equal(interacted.interaction?.terminal_session_id, targetSessionId)

  // 3. Single cleanup with targetSessionId
  const cleaned = await session.terminals.cleanup({
    scope: "single",
    sessionIds: [targetSessionId],
  })
  assert.ok(cleaned.result)
  assert.deepEqual(cleaned.result?.cleaned_session_ids, [targetSessionId])
})

test("BackboneSession with custom executionWorld routes both terminal API and tool turns to the same world", async () => {
  let terminalStarted = false
  let toolTurnExecuted = false

  const customDriver = {
    driverId: "unified-custom-world-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    buildSandboxRequest: (input: { requestId: string; command: string[] }): SandboxRequestV1 => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: input.requestId,
      capability_id: "cap-unified",
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [input.command[0] ?? "", ...input.command.slice(1)] as [string, ...string[]],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: 30,
      evidence_mode: "minimal",
      metadata: {},
    }),
    execute: async (req: { request_id: string }) => {
      toolTurnExecuted = true
      return {
        schema_version: "bb.sandbox_result.v1" as const,
        request_id: req.request_id,
        status: "completed" as const,
        placement_id: "place-unified",
        stdout_ref: "sha256:custom-stdout",
        stderr_ref: null,
        artifact_refs: [],
        evidence_refs: [],
        side_effect_digest: "sha256:custom-digest",
        usage: { wall_ms: 5 },
        error: null,
      }
    },
    startTerminalSession: async (input: { terminalSessionId: string; command: string[] }) => {
      terminalStarted = true
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1" as const,
          terminal_session_id: input.terminalSessionId,
          startup_call_id: null,
          owner_task_id: null,
          public_handles: [],
          command: input.command,
          cwd: null,
          stream_mode: "pipes" as const,
          stream_split: "stdout_stderr" as const,
          capability_id: null,
          placement_id: null,
          persistence_scope: "thread" as const,
          continuation_scope: "both" as const,
        },
        outputDeltas: [],
      }
    },
    interactTerminalSession: async (input: { terminalSessionId: string; interactionKind: "stdin" | "signal" }) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1" as const,
        terminal_session_id: input.terminalSessionId,
        interaction_kind: input.interactionKind,
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input: { cleanupId: string; scope: "all" | "single" | "filtered"; sessionIds?: string[] }) => ({
      schema_version: "bb.terminal_cleanup_result.v1" as const,
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ?? [],
      failed_session_ids: [],
    }),
  }

  const customWorld = createExecutionWorld({ drivers: [customDriver] })
  const workspace = createWorkspace({
    workspaceId: "ws-unified-1",
    rootDir: "/tmp",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace, executionWorld: customWorld })
  const session = backbone.openSession({ sessionId: "s-unified-1", workspaceRoot: "/tmp" })

  // 1. Exercise terminal API through custom world
  const termRes = await session.terminals.start({
    terminalSessionId: "term-unified-1",
    command: ["echo", "term"],
  })
  assert.ok(termRes.session)
  assert.equal(terminalStarted, true)

  // 2. Exercise tool turn through custom world
  const toolTurnRes = await session.runToolTurn({
    request: {
      schema_version: "bb.run_request.v1",
      request_id: "req-unified-1",
      entry_mode: "hostkit",
      task: "Run unified tool turn",
      workspace_root: "/tmp",
      requested_model: "openai/gpt-5.4-mini",
      requested_features: {},
      metadata: {},
    },
    toolName: "unified_tool",
    command: ["echo", "turn"],
  })
  assert.equal(toolTurnExecuted, true)
  assert.equal(toolTurnRes.driverTurn?.driverId, "unified-custom-world-driver")
  assert.equal(toolTurnRes.driverTurn?.sandboxResult.status, "completed")
})

test("Backbone terminals preserves one-time selected driver across dynamic capability changes during execution", async () => {
  let dynamicSupport = true
  let executedDriverId = ""

  const dynamicDriver = {
    driverId: "dynamic-terminal-driver",
    supportedPlacements: ["local_process" as const, "local_oci" as const],
    supportsCapability: () => true,
    supportsTerminalSessions: () => dynamicSupport,
    startTerminalSession: async (input: { terminalSessionId: string; command: string[] }) => {
      executedDriverId = "dynamic-terminal-driver"
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1" as const,
          terminal_session_id: input.terminalSessionId,
          command: input.command,
          startup_call_id: null,
          owner_task_id: null,
          public_handles: [],
          capability_id: "cap-dynamic",
          placement_id: "place-dynamic",
          persistence_scope: "thread" as const,
          continuation_scope: "both" as const,
          stream_mode: "pipes" as const,
          stream_split: "stdout_stderr" as const,
        },
        outputDeltas: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [dynamicDriver] })
  const workspace = createWorkspace({
    workspaceId: "ws-dynamic",
    rootDir: "/tmp",
    defaultExecutionProfileId: "sandboxed_local",
    capabilitySet: buildWorkspaceCapabilitySet({}),
  })
  const backbone = createBackbone({ workspace, executionWorld: world })
  const session = backbone.openSession({ sessionId: "sess-dynamic-1" })
  // Select driver while dynamicSupport is true
  const classification = session.terminals.classify({ executionProfileId: "sandboxed_local" })

  // Mutate dynamicSupport to false before start executes
  dynamicSupport = false

  // Start with reserved driver selection carried through execute
  const startResult = await session.terminals.start({
    terminalSessionId: "term-dynamic-1",
    command: ["echo", "dynamic"],
    executionProfileId: "sandboxed_local",
    driverId: "dynamic-terminal-driver",
  })

  assert.ok(startResult.session)
  assert.equal(executedDriverId, "dynamic-terminal-driver")
})

test("Backbone terminals supported driver interacting with unknown session returns supported supportClaim and typed unsupportedCase", async () => {
  const driver = {
    driverId: "mock-terminal-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    interactTerminalSession: async () => {
      throw new Error("Unknown terminal session term-nonexistent-1")
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const workspace = createWorkspace({
    workspaceId: "ws-support-claim",
    rootDir: "/tmp",
    defaultExecutionProfileId: "trusted_local",
    capabilitySet: buildWorkspaceCapabilitySet({}),
  })
  const backbone = createBackbone({ workspace, executionWorld: world })
  const session = backbone.openSession({ sessionId: "sess-support-1" })

  const interactResult = await session.terminals.interact({
    terminalSessionId: "term-nonexistent-1",
    interactionKind: "stdin",
    inputText: "hello\n",
  })

  assert.equal(interactResult.supportClaim.level, "supported")
  assert.equal(interactResult.interaction, null)
  assert.ok(interactResult.unsupportedCase !== null)
  assert.equal(interactResult.unsupportedCase?.reason_code, "terminal_interaction_failed")
})

test("Backbone terminals cleanup then restart with same session ID creates fresh running session view", async () => {
  const startedSessions: string[] = []
  const cleanedSessions: string[] = []
  const driver = {
    driverId: "lifecycle-terminal-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input: { terminalSessionId: string; command: string[] }) => {
      startedSessions.push(input.terminalSessionId)
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1" as const,
          terminal_session_id: input.terminalSessionId,
          command: input.command,
          startup_call_id: null,
          owner_task_id: null,
          public_handles: [],
          capability_id: "cap-life",
          placement_id: "place-life",
          persistence_scope: "thread" as const,
          continuation_scope: "both" as const,
          stream_mode: "pipes" as const,
        },
        outputDeltas: [
          {
            schema_version: "bb.terminal_output_delta.v1" as const,
            terminal_session_id: input.terminalSessionId,
            chunk_seq: 1,
            stream: "stdout" as const,
            chunk_b64: Buffer.from(`start-${startedSessions.length}\n`).toString("base64"),
          },
        ],
      }
    },
    cleanupTerminalSessions: async (input: { sessionIds?: readonly string[]; cleanupId: string }) => {
      cleanedSessions.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1" as const,
        cleanup_id: input.cleanupId,
        scope: "single" as const,
        cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const workspace = createWorkspace({
    workspaceId: "ws-lifecycle",
    rootDir: "/tmp",
    defaultExecutionProfileId: "trusted_local",
    capabilitySet: buildWorkspaceCapabilitySet({}),
  })
  const backbone = createBackbone({ workspace, executionWorld: world })
  const session = backbone.openSession({ sessionId: "sess-life-1" })

  // Start session first time
  const start1 = await session.terminals.start({
    terminalSessionId: "term-reused-1",
    command: ["bash"],
  })
  assert.ok(start1.session)
  assert.equal(start1.session.status, "running")
  assert.equal(start1.session.summary().outputChunkCount, 1)
  assert.equal(start1.session.summary().lastEndState, null)

  // Cleanup session
  const cleanup = await session.terminals.cleanup({
    scope: "single",
    sessionIds: ["term-reused-1"],
  })
  assert.deepEqual(cleanup.result?.cleaned_session_ids, ["term-reused-1"])

  // Restart session with same ID
  const start2 = await session.terminals.start({
    terminalSessionId: "term-reused-1",
    command: ["bash"],
  })
  assert.ok(start2.session)
  assert.equal(start2.session.status, "running")
  assert.equal(start2.session.summary().lastEndState, null)
  assert.equal(start2.session.summary().outputChunkCount, 1)
})

test("Backbone terminals session.cleanup after poll-delivered end routes cleanup to driver", async () => {
  let cleanupCalledWith: string[] = []
  const driver = {
    driverId: "poll-end-driver",
    supportedPlacements: ["local_process" as const],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input: { terminalSessionId: string; command: string[] }) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1" as const,
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: "cap-life",
        placement_id: "place-life",
        persistence_scope: "thread" as const,
        continuation_scope: "both" as const,
        stream_mode: "pipes" as const,
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input: { terminalSessionId: string; interactionKind: any }) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1" as const,
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1" as const,
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed" as const,
        exit_code: 0,
      },
    }),
    cleanupTerminalSessions: async (input: { sessionIds?: readonly string[]; cleanupId: string }) => {
      cleanupCalledWith.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1" as const,
        cleanup_id: input.cleanupId,
        scope: "single" as const,
        cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver] })
  const workspace = createWorkspace({
    workspaceId: "ws-poll-end",
    rootDir: "/tmp",
    defaultExecutionProfileId: "trusted_local",
    capabilitySet: buildWorkspaceCapabilitySet({}),
  })
  const backbone = createBackbone({ workspace, executionWorld: world })
  const session = backbone.openSession({ sessionId: "sess-poll-end-1" })

  const start = await session.terminals.start({
    terminalSessionId: "term-poll-end-1",
    command: ["bash"],
  })
  assert.ok(start.session)
  assert.equal(start.session.status, "running")

  // Poll interaction delivers end of process
  const poll = await start.session.poll()
  assert.equal(start.session.status, "ended")
  assert.equal(start.session.summary().lastEndState, "completed")
  // Call session.cleanup() on the ended session
  const cleanRes = await start.session.cleanup()
  assert.deepEqual(cleanRes.result?.cleaned_session_ids, ["term-poll-end-1"])
  assert.deepEqual(cleanupCalledWith, ["term-poll-end-1"])
})
