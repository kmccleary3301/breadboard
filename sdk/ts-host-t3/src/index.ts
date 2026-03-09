import type { ProviderExchangeV1 } from "@breadboard/kernel-contracts"
import { createBackbone, type BackboneTurnResult, type SupportClaim } from "@breadboard/backbone"
import { buildProviderHostTurnView, createProviderHostSession } from "@breadboard/host-kits"
import {
  createAiSdkTransportSession,
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

export interface T3CodeContinueTurnInput extends Omit<T3CodePromptTurnInput, "existingTranscript"> {
  readonly existingTranscript: BackboneTurnResult["transcript"] | Array<Record<string, unknown>>
  readonly previousTransportState?: AiSdkTransportState | null
}

export interface T3CodePromptTurnResult {
  readonly supportClaim: SupportClaim
  readonly turn: BackboneTurnResult
  readonly frames: readonly AiSdkTransportFrame[]
  readonly transportState: AiSdkTransportState
}

export interface T3CodeSession {
  classifyPromptTurn(
    input: Omit<T3CodePromptTurnInput, "sessionId" | "workspaceId" | "workspaceRoot" | "requestedModel">,
  ): SupportClaim
  runPromptTurn(
    input: Omit<T3CodePromptTurnInput, "sessionId" | "workspaceId" | "workspaceRoot" | "requestedModel">,
  ): Promise<T3CodePromptTurnResult>
  continuePromptTurn(
    input: Omit<T3CodeContinueTurnInput, "sessionId" | "workspaceId" | "workspaceRoot" | "requestedModel">,
  ): Promise<T3CodePromptTurnResult>
  readonly transcript: BackboneTurnResult["transcript"] | null
  readonly transportState: AiSdkTransportState | null
}

export interface T3CodeStarter {
  classifyPromptTurn(input: T3CodePromptTurnInput): SupportClaim
  runPromptTurn(input: T3CodePromptTurnInput): Promise<T3CodePromptTurnResult>
  continuePromptTurn(input: T3CodeContinueTurnInput): Promise<T3CodePromptTurnResult>
  openSession(input: {
    sessionId: string
    workspaceId: string
    workspaceRoot?: string | null
    requestedModel: string
    requestedProvider: string
  }): T3CodeSession
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
  function buildBackboneForInput(input: {
    workspaceId: string
    workspaceRoot?: string | null
    requestedModel: string
    requestedProvider: string
    sessionId: string
  }) {
    const workspace = createWorkspace({
      workspaceId: input.workspaceId,
      rootDir: input.workspaceRoot ?? null,
      capabilitySet: buildWorkspaceCapabilitySet(options.workspaceCapabilities),
      defaultExecutionProfileId: options.defaultExecutionProfileId,
    })
    const backbone = createBackbone({ workspace, defaultProjectionProfileId: "ai_sdk_transport" })
    return backbone.openSession({
      sessionId: input.sessionId,
      workspaceRoot: input.workspaceRoot ?? null,
      requestedModel: input.requestedModel,
      requestedProvider: input.requestedProvider,
      projectionProfileId: "ai_sdk_transport",
    })
  }

  async function runTurn(
    input: T3CodePromptTurnInput,
    previousTransportState?: AiSdkTransportState | null,
  ): Promise<T3CodePromptTurnResult> {
    const session = buildBackboneForInput({
      ...input,
      requestedProvider: input.providerExchange.request.provider_family,
    })
    const turn = await session.runProviderTurn({
      request: buildRequest(input),
      providerExchange: input.providerExchange,
      assistantText: input.assistantText,
      existingTranscript: input.existingTranscript,
    })
    const transportProjection = projectBackboneTurnToAiSdkTransport(turn, {
      messageId: input.messageId,
      previousState: previousTransportState,
    })
    return {
      supportClaim: turn.supportClaim,
      turn,
      frames: transportProjection.frames,
      transportState: transportProjection.state,
    }
  }

  return {
    classifyPromptTurn(input: T3CodePromptTurnInput): SupportClaim {
      const session = buildBackboneForInput({
        ...input,
        requestedProvider: input.providerExchange.request.provider_family,
      })
      return session.classifyProviderTurn({
        request: buildRequest(input),
        providerExchange: input.providerExchange,
        assistantText: input.assistantText,
      })
    },
    async runPromptTurn(input: T3CodePromptTurnInput): Promise<T3CodePromptTurnResult> {
      return runTurn(input)
    },
    async continuePromptTurn(input: T3CodeContinueTurnInput): Promise<T3CodePromptTurnResult> {
      return runTurn(input, input.previousTransportState)
    },
    openSession(sessionInput): T3CodeSession {
      const transportSession = createAiSdkTransportSession()
      const base = {
        sessionId: sessionInput.sessionId,
        workspaceId: sessionInput.workspaceId,
        workspaceRoot: sessionInput.workspaceRoot ?? null,
        requestedModel: sessionInput.requestedModel,
      }
      const providerHostSession = createProviderHostSession<
        Omit<T3CodePromptTurnInput, "sessionId" | "workspaceId" | "workspaceRoot" | "requestedModel">,
        AiSdkTransportState,
        readonly AiSdkTransportFrame[]
      >({
        backboneSession: buildBackboneForInput({
          ...base,
          requestedProvider: sessionInput.requestedProvider,
          sessionId: sessionInput.sessionId,
        }),
        buildInput(input, transcript) {
          return {
            request: buildRequest({
              ...base,
              ...input,
            }),
            providerExchange: input.providerExchange,
            assistantText: input.assistantText,
            existingTranscript: input.existingTranscript ?? transcript ?? [],
          }
        },
        projectTurn(turn, context) {
          const projection = context.resumed
            ? transportSession.projectResumedTurn(turn)
            : transportSession.projectTurn(turn)
          return {
            state: projection.state,
            output: projection.frames,
          }
        },
      })

      return {
        /**
         * Classify a prompt turn against the current thin-host session boundary.
         */
        classifyPromptTurn(input) {
          return providerHostSession.classifyProviderTurn(input)
        },
        /**
         * Run a provider-backed prompt turn and persist transcript/transport state in-session.
         */
        async runPromptTurn(input) {
          const result = buildProviderHostTurnView(await providerHostSession.runProviderTurn(input))
          return {
            supportClaim: result.supportClaim,
            turn: result.turn,
            frames: result.projectionOutput ?? [],
            transportState: result.projectionState ?? transportSession.state ?? {
              lastMessageId: result.turn.runContextId,
              transcriptDigest: null,
              turnCount: 0,
            },
          }
        },
        /**
         * Continue a prior prompt turn using transcript and transport state owned by the session wrapper.
         */
        async continuePromptTurn(input) {
          const result = buildProviderHostTurnView(await providerHostSession.continueProviderTurn({
            ...input,
            existingTranscript: input.existingTranscript ?? providerHostSession.transcript ?? [],
          }))
          return {
            supportClaim: result.supportClaim,
            turn: result.turn,
            frames: result.projectionOutput ?? [],
            transportState: result.projectionState ?? transportSession.state ?? {
              lastMessageId: result.turn.runContextId,
              transcriptDigest: null,
              turnCount: 0,
            },
          }
        },
        get transcript() {
          return providerHostSession.transcript
        },
        get transportState() {
          return providerHostSession.projectionState ?? transportSession.state
        }
      }
    },
  }
}
