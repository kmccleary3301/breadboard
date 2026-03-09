import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"
import { createBackbone, type BackboneTurnResult, type SupportClaim } from "@breadboard/backbone"
import {
  projectBackboneTurnToAiSdkTransport,
  type AiSdkTransportFrame,
  type AiSdkTransportState,
} from "@breadboard/transport-ai-sdk"
import {
  buildWorkspaceCapabilitySet,
  createWorkspace,
  type WorkspaceCapabilitySet,
  type ExecutionProfileId,
} from "@breadboard/workspace"

export interface T3CodePromptTurnInput {
  readonly sessionId: string
  readonly workspaceId: string
  readonly workspaceRoot?: string | null
  readonly task: string
  readonly requestedModel: string
  readonly providerExchange: ProviderExchangeV1
  readonly assistantText: string
  readonly existingTranscript?: BackboneTurnResult["transcript"] | Array<Record<string, unknown>>
  readonly messageId?: string
}

export interface T3CodeStarterOptions {
  readonly workspaceCapabilities?: Partial<WorkspaceCapabilitySet>
  readonly defaultExecutionProfileId?: ExecutionProfileId
}

export interface T3CodePromptTurnResult {
  readonly supportClaim: SupportClaim
  readonly turn: BackboneTurnResult
  readonly frames: readonly AiSdkTransportFrame[]
  readonly transportState: AiSdkTransportState
}

export interface T3CodeStarter {
  classifyPromptTurn(input: T3CodePromptTurnInput): SupportClaim
  runPromptTurn(input: T3CodePromptTurnInput): Promise<T3CodePromptTurnResult>
}

function buildRequest(input: T3CodePromptTurnInput) {
  return {
    schema_version: "bb.run_request.v1" as const,
    request_id: `${input.sessionId}:${input.messageId ?? "turn"}`,
    entry_mode: "t3_code" as const,
    task: input.task,
    workspace_root: input.workspaceRoot ?? null,
    requested_model: input.requestedModel,
    requested_features: {},
    metadata: {
      host: "t3_code",
      workspace_id: input.workspaceId,
    },
  }
}

export function createT3CodeStarter(options: T3CodeStarterOptions = {}): T3CodeStarter {
  return {
    classifyPromptTurn(input: T3CodePromptTurnInput): SupportClaim {
      const workspace = createWorkspace({
        workspaceId: input.workspaceId,
        rootDir: input.workspaceRoot ?? null,
        capabilitySet: buildWorkspaceCapabilitySet(options.workspaceCapabilities),
        defaultExecutionProfileId: options.defaultExecutionProfileId,
      })
      const backbone = createBackbone({ workspace, defaultProjectionProfileId: "ai_sdk_transport" })
      const session = backbone.openSession({
        sessionId: input.sessionId,
        workspaceRoot: input.workspaceRoot ?? null,
        requestedModel: input.requestedModel,
        requestedProvider: input.providerExchange.request.provider_family,
        projectionProfileId: "ai_sdk_transport",
      })
      return session.classifyProviderTurn({
        request: buildRequest(input),
        providerExchange: input.providerExchange,
        assistantText: input.assistantText,
      })
    },
    async runPromptTurn(input: T3CodePromptTurnInput): Promise<T3CodePromptTurnResult> {
      const workspace = createWorkspace({
        workspaceId: input.workspaceId,
        rootDir: input.workspaceRoot ?? null,
        capabilitySet: buildWorkspaceCapabilitySet(options.workspaceCapabilities),
        defaultExecutionProfileId: options.defaultExecutionProfileId,
      })
      const backbone = createBackbone({ workspace, defaultProjectionProfileId: "ai_sdk_transport" })
      const session = backbone.openSession({
        sessionId: input.sessionId,
        workspaceRoot: input.workspaceRoot ?? null,
        requestedModel: input.requestedModel,
        requestedProvider: input.providerExchange.request.provider_family,
        projectionProfileId: "ai_sdk_transport",
      })
      const turn = await session.runProviderTurn({
        request: buildRequest(input),
        providerExchange: input.providerExchange,
        assistantText: input.assistantText,
        existingTranscript: input.existingTranscript,
      })
      const transportProjection = projectBackboneTurnToAiSdkTransport(turn, {
        messageId: input.messageId,
      })
      return {
        supportClaim: turn.supportClaim,
        turn,
        frames: transportProjection.frames,
        transportState: transportProjection.state,
      }
    },
  }
}
