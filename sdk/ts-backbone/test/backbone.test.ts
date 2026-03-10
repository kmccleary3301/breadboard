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

  const claim = session.terminals.classify({})
  assert.equal(claim.level, "supported")
  assert.equal(claim.terminalSupport?.canStart, true)

  const started = await session.terminals.start({
    command: ["/bin/bash", "-lc", "printf 'ready\\n'; sleep 0.5"],
  })
  assert.ok(started.descriptor)

  const interacted = await session.terminals.interact({
    terminalSessionId: started.descriptor!.terminal_session_id,
    interactionKind: "poll",
    settleMs: 25,
  })
  assert.ok(interacted.outputDeltas.length >= 1)

  const snapshot = await session.terminals.snapshot()
  assert.ok(snapshot.snapshot)
})
