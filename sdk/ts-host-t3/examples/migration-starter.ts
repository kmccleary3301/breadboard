import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"

import { createT3CodeStarter } from "../src/index.js"

const providerExchange: ProviderExchangeV1 = {
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

async function main(): Promise<void> {
  const starter = createT3CodeStarter()
  const session = starter.openSession({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    requestedModel: "openai/gpt-5.4-mini",
    requestedProvider: "openai",
    initialTranscript: [],
    initialTransportState: null,
  })

  const first = await session.runPromptTurn({
    task: "Summarize the project architecture.",
    providerExchange,
    assistantText: "The project is organized into Backbone, Workspace, and Host Kit layers.",
  })
  console.log("first", first.frames)

  const second = await session.continuePromptTurn({
    task: "Continue with migration guidance.",
    providerExchange,
    assistantText: "Keep persistence host-owned and let BreadBoard manage continuation patches.",
    existingTranscript: session.transcript ?? [],
    previousTransportState: session.transportState,
  })
  console.log("second", second.frames)
}

void main()
