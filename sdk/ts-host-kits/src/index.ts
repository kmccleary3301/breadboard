import type {
  BackboneSession,
  BackboneTurnResult,
  ProviderTurnInput,
  SupportClaim,
} from "@breadboard/backbone"
import type { SessionTranscriptV1, SessionTranscriptV1Item } from "@breadboard/kernel-contracts"

export type HostKitMode = "supported" | "fallback"

export interface HostKitClassification<Request> {
  readonly mode: HostKitMode
  readonly supportClaim: SupportClaim
  readonly unsupportedFields: readonly string[]
  readonly request: Request
}

export interface HostKitInvocation<Result, Invocation> {
  readonly mode: HostKitMode
  readonly result: Result
  readonly invocation: Invocation
  readonly supportClaim: SupportClaim
}

export interface HostKit<Request, Result, Invocation> {
  readonly id: string
  classify(request: Request): HostKitClassification<Request>
  invoke(request: Request): Promise<HostKitInvocation<Result, Invocation>>
}

export interface HostKitOptions<Request, Result, Invocation> {
  readonly id: string
  readonly classify: (request: Request) => HostKitClassification<Request>
  readonly invoke: (request: Request) => Promise<HostKitInvocation<Result, Invocation>>
}

export type HostManagedTranscript =
  | SessionTranscriptV1
  | Array<Record<string, unknown> | SessionTranscriptV1Item>

export interface ProviderHostSessionProjectContext<ProjectionState> {
  readonly previousState: ProjectionState | null
  readonly resumed: boolean
}

export interface ProviderHostSessionProjectionResult<ProjectionState, ProjectionOutput> {
  readonly state: ProjectionState | null
  readonly output: ProjectionOutput
}

export interface ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput> {
  readonly supportClaim: SupportClaim
  readonly turn: BackboneTurnResult
  readonly projectionState: ProjectionState | null
  readonly projectionOutput: ProjectionOutput | null
}

export interface ProviderHostSession<Input, ProjectionState, ProjectionOutput> {
  classifyProviderTurn(input: Input): SupportClaim
  runProviderTurn(input: Input): Promise<ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>>
  continueProviderTurn(input: Input): Promise<ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>>
  readonly transcript: SessionTranscriptV1 | null
  readonly projectionState: ProjectionState | null
}

export interface ProviderHostSessionOptions<Input, ProjectionState, ProjectionOutput> {
  readonly backboneSession: BackboneSession
  readonly buildInput: (input: Input, transcript: SessionTranscriptV1 | null) => ProviderTurnInput
  readonly projectTurn?: (
    turn: BackboneTurnResult,
    context: ProviderHostSessionProjectContext<ProjectionState>,
  ) => ProviderHostSessionProjectionResult<ProjectionState, ProjectionOutput>
}

/**
 * Apply consistent override semantics to a public SupportClaim without mutating the base claim.
 */
export function normalizeHostKitSupportClaim(
  claim: SupportClaim,
  overrides: Partial<SupportClaim> = {},
): SupportClaim {
  return {
    ...claim,
    ...overrides,
    unsupportedFields: [...(overrides.unsupportedFields ?? claim.unsupportedFields)],
  }
}

/**
 * Build a fallback-mode Host Kit invocation while preserving a normalized support claim.
 */
export function buildFallbackHostKitInvocation<Result, Invocation>(options: {
  result: Result
  invocation: Invocation
  supportClaim: SupportClaim
}): HostKitInvocation<Result, Invocation> {
  return {
    mode: "fallback",
    result: options.result,
    invocation: options.invocation,
    supportClaim: normalizeHostKitSupportClaim(options.supportClaim, {
      level: "fallback",
      fallbackAvailable: true,
      confidence: options.supportClaim.confidence === "high" ? "medium" : options.supportClaim.confidence,
    }),
  }
}

/**
 * Build a supported-mode Host Kit invocation while preserving a normalized support claim.
 */
export function buildSupportedHostKitInvocation<Result, Invocation>(options: {
  result: Result
  invocation: Invocation
  supportClaim: SupportClaim
}): HostKitInvocation<Result, Invocation> {
  return {
    mode: "supported",
    result: options.result,
    invocation: options.invocation,
    supportClaim: normalizeHostKitSupportClaim(options.supportClaim, {
      level: "supported",
    }),
  }
}

/**
 * Create the stable Host Kit classify/invoke surface for a concrete host integration.
 */
export function createHostKit<Request, Result, Invocation>(
  options: HostKitOptions<Request, Result, Invocation>,
): HostKit<Request, Result, Invocation> {
  return {
    id: options.id,
    classify(request: Request): HostKitClassification<Request> {
      return options.classify(request)
    },
    invoke(request: Request): Promise<HostKitInvocation<Result, Invocation>> {
      return options.invoke(request)
    },
  }
}

/**
 * Create a reusable host-managed provider-turn session that keeps transcript continuity and
 * optional projection state outside of host-specific bridge code.
 */
export function createProviderHostSession<Input, ProjectionState, ProjectionOutput>(
  options: ProviderHostSessionOptions<Input, ProjectionState, ProjectionOutput>,
): ProviderHostSession<Input, ProjectionState, ProjectionOutput> {
  let transcript: SessionTranscriptV1 | null = null
  let projectionState: ProjectionState | null = null

  async function run(
    input: Input,
    resumed: boolean,
  ): Promise<ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>> {
    const providerInput = options.buildInput(input, transcript)
    const supportClaim = options.backboneSession.classifyProviderTurn(providerInput)
    const turn = await options.backboneSession.runProviderTurn(providerInput)
    transcript = turn.transcript

    let projectionOutput: ProjectionOutput | null = null
    if (options.projectTurn) {
      const projected = options.projectTurn(turn, {
        previousState: projectionState,
        resumed,
      })
      projectionState = projected.state
      projectionOutput = projected.output
    }

    return {
      supportClaim,
      turn,
      projectionState,
      projectionOutput,
    }
  }

  return {
    classifyProviderTurn(input: Input): SupportClaim {
      return options.backboneSession.classifyProviderTurn(options.buildInput(input, transcript))
    },
    runProviderTurn(input: Input): Promise<ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>> {
      return run(input, false)
    },
    continueProviderTurn(input: Input): Promise<ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>> {
      return run(input, true)
    },
    get transcript(): SessionTranscriptV1 | null {
      return transcript
    },
    get projectionState(): ProjectionState | null {
      return projectionState
    },
  }
}
