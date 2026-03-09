import { createT3CodeStarter } from "../src/index.js"
import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"

const starter = createT3CodeStarter()

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

async function main() {
  const session = starter.openSession({
    sessionId: "session-1",
    workspaceId: "workspace-1",
    workspaceRoot: "/tmp/project",
    requestedModel: "openai/gpt-5.4-mini",
    requestedProvider: "openai",
  })

  const first = await session.runPromptTurn({
    task: "Summarize the repository layout",
    providerExchange: exchange,
    assistantText: "Here is the repository layout.",
  })

  console.log(first.frames)

  const second = await session.continuePromptTurn({
    task: "Continue with the most important modules",
    providerExchange: exchange,
    assistantText: "The core modules are ts-backbone and ts-workspace.",
    existingTranscript: session.transcript ?? [],
  })

  console.log(second.frames)
  console.log(session.transportState)
}

void main()
