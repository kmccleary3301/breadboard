import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"

import { createT3CodeStarter } from "../src/index.js"

const starter = createT3CodeStarter()

function buildProviderExchange(task: string): ProviderExchangeV1 {
  return {
    schema_version: "bb.provider_exchange.v1",
    exchange_id: `exchange:${task}`,
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
}

export async function POST(request: Request): Promise<Response> {
  const body = (await request.json()) as {
    sessionId: string
    workspaceId: string
    workspaceRoot?: string | null
    task: string
    transportState?: Record<string, unknown> | null
    transcript?: Array<Record<string, unknown>> | null
  }

  const result = body.transportState
    ? await starter.continuePromptTurn({
        sessionId: body.sessionId,
        workspaceId: body.workspaceId,
        workspaceRoot: body.workspaceRoot ?? null,
        task: body.task,
        requestedModel: "openai/gpt-5.4-mini",
        providerExchange: buildProviderExchange(body.task),
        assistantText: "Continuation content emitted through BreadBoard.",
        existingTranscript: body.transcript ?? [],
        previousTransportState: body.transportState as never,
        messageId: `${body.sessionId}:continue`,
      })
    : await starter.runPromptTurn({
        sessionId: body.sessionId,
        workspaceId: body.workspaceId,
        workspaceRoot: body.workspaceRoot ?? null,
        task: body.task,
        requestedModel: "openai/gpt-5.4-mini",
        providerExchange: buildProviderExchange(body.task),
        assistantText: "Initial content emitted through BreadBoard.",
        messageId: `${body.sessionId}:first`,
      })

  return Response.json({
    supportClaim: result.supportClaim,
    frames: result.frames,
    transportState: result.transportState,
    transcript: result.turn.transcript,
  })
}
