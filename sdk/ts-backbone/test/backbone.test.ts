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
})
