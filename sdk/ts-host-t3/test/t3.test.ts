import test from "node:test"
import assert from "node:assert/strict"

import { createT3CodeStarter } from "../src/index.js"
import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"

const exchange: ProviderExchangeV1 = {
  schema_version: "bb.provider_exchange.v1",
  exchange_id: "exchange-1",
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

test("t3 starter classifies a supported provider-backed prompt turn", () => {
  const starter = createT3CodeStarter()
  const claim = starter.classifyPromptTurn({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    task: "Explain the repository layout",
    requestedModel: "openai/gpt-5.4-mini",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
  })
  assert.equal(claim.level, "supported")
  assert.equal(claim.executionProfile.id, "trusted_local")
})

test("t3 starter produces AI SDK transport frames for a thin-host prompt turn", async () => {
  const starter = createT3CodeStarter()
  const result = await starter.runPromptTurn({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    task: "Explain the repository layout",
    requestedModel: "openai/gpt-5.4-mini",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
    messageId: "msg-1",
  })
  assert.equal(result.supportClaim.recommendedHostMode, "inline")
  assert.deepEqual(result.frames.map((frame) => frame.type), ["start", "text-delta", "finish"])
  assert.equal(result.transportState.lastMessageId, "msg-1")
})

test("t3 starter continues a prompt turn with transcript continuity and transport resume state", async () => {
  const starter = createT3CodeStarter()
  const first = await starter.runPromptTurn({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    task: "Explain the repository layout",
    requestedModel: "openai/gpt-5.4-mini",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
    messageId: "msg-1",
  })
  const second = await starter.continuePromptTurn({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    task: "Continue",
    requestedModel: "openai/gpt-5.4-mini",
    providerExchange: exchange,
    assistantText: "And here is a continuation.",
    existingTranscript: first.turn.transcript,
    previousTransportState: first.transportState,
    messageId: "msg-2",
  })
  assert.equal(second.frames[0]?.type, "resume")
  assert.equal(second.transportState.turnCount, 2)
  assert.equal(second.turn.transcript.metadata?.preserved_prefix_items, 3)
})

test("t3 starter can open a reusable session wrapper", async () => {
  const starter = createT3CodeStarter()
  const session = starter.openSession({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    requestedModel: "openai/gpt-5.4-mini",
    requestedProvider: "openai",
  })
  const claim = session.classifyPromptTurn({
    task: "Explain the repository layout",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
  })
  assert.equal(claim.level, "supported")
  const result = await session.runPromptTurn({
    task: "Explain the repository layout",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
  })
  assert.equal(result.supportClaim.executionProfile.id, "trusted_local")
  assert.equal(session.transportState?.turnCount, 1)
  const continued = await session.continuePromptTurn({
    task: "Continue",
    providerExchange: exchange,
    assistantText: "And here is a continuation.",
    existingTranscript: session.transcript ?? [],
  })
  assert.equal(continued.frames[0]?.type, "resume")
  assert.equal(session.transportState?.turnCount, 2)
})
