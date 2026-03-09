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
