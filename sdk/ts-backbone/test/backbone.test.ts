import test from "node:test"
import assert from "node:assert/strict"

import { createBackbone } from "../src/index.js"
import { buildWorkspaceCapabilitySet, createWorkspace } from "@breadboard/workspace"
import type { ProviderExchangeV1, RunRequestV1 } from "@breadboard/kernel-contracts"

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

  const interacted = await terminalSession.poll({ settleMs: 25 })
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
