import type { HostManagedTranscript } from "@breadboard/host-kits"
import type { AiSdkTransportState } from "@breadboard/transport-ai-sdk"

import { createT3CodeStarter } from "../src/index.js"
import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"

type SessionRecord = {
  transcript: HostManagedTranscript | null
  transportState: AiSdkTransportState | null
}

const sessions = new Map<string, SessionRecord>()
const starter = createT3CodeStarter()

const providerExchange: ProviderExchangeV1 = {
  schema_version: "bb.provider_exchange.v1",
  exchange_id: "exchange:session-store",
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

async function runSessionTurn(sessionId: string, task: string) {
  const prior = sessions.get(sessionId) ?? { transcript: null, transportState: null }
  const session = starter.openSession({
    sessionId,
    workspaceId: "workspace-1",
    requestedModel: "openai/gpt-5.4-mini",
    requestedProvider: "openai",
    initialTranscript: prior.transcript,
    initialTransportState: prior.transportState,
  })

  const result = prior.transportState
    ? await session.continuePromptTurn({
        task,
        providerExchange,
        assistantText: "Continuation content emitted through BreadBoard.",
        existingTranscript: session.transcript ?? [],
        previousTransportState: session.transportState,
      })
    : await session.runPromptTurn({
        task,
        providerExchange,
        assistantText: "Initial content emitted through BreadBoard.",
      })

  sessions.set(sessionId, {
    transcript: session.transcript ?? null,
    transportState: session.transportState,
  })

  return result
}

void runSessionTurn("session-1", "Describe the migration path.").then((result) => {
  console.log(result.frames)
})
