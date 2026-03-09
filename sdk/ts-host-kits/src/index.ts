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

export interface ProviderHostTurnView<ProjectionState, ProjectionOutput> {
  readonly supportClaim: SupportClaim
  readonly turn: BackboneTurnResult
  readonly projectionOutput: ProjectionOutput | null
  readonly projectionState: ProjectionState | null
}

export interface ResolvedProviderHostTurnView<ProjectionState, ProjectionOutput>
  extends ProviderHostTurnView<ProjectionState, ProjectionOutput> {
  readonly projectionOutput: ProjectionOutput
  readonly projectionState: ProjectionState
}

export interface HostTranscriptProjection {
  readonly entries: Array<
    | { kind: "assistant_text"; text: string }
    | { kind: "tool_preview"; text: string }
  >
  readonly assistantTexts: readonly string[]
  readonly toolPreviews: readonly string[]
}

export interface HostProjectionCallbackSink {
  readonly onAssistantMessageStart?: () => void | Promise<void>
  readonly onPartialReply?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>
  readonly onToolResult?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>
  readonly onAgentEvent?: (payload: { stream: string; data: Record<string, unknown> }) => void | Promise<void>
}

export interface HostProjectionEnvelope<Result> {
  readonly transcript: HostTranscriptProjection
  readonly result: Result
}

export interface HostAgentMeta {
  readonly sessionId: string
  readonly provider: string
  readonly model: string
  readonly usage?: Record<string, unknown>
}

export interface HostResultMeta {
  readonly durationMs: number
  readonly agentMeta: HostAgentMeta
  readonly stopReason: string
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
  readonly initialTranscript?: SessionTranscriptV1 | null
  readonly initialProjectionState?: ProjectionState | null
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
 * Normalize the common public view returned from a provider-backed host session turn.
 */
export function buildProviderHostTurnView<ProjectionState, ProjectionOutput>(
  result: ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>,
): ProviderHostTurnView<ProjectionState, ProjectionOutput> {
  return {
    supportClaim: result.supportClaim,
    turn: result.turn,
    projectionOutput: result.projectionOutput,
    projectionState: result.projectionState,
  }
}

/**
 * Resolve a provider-host turn into a fully populated projection view. This is useful for thin
 * hosts that want a stable product-layer result shape without repeating fallback-state logic for
 * missing projection output or state.
 */
export function resolveProviderHostTurnView<ProjectionState, ProjectionOutput>(options: {
  readonly result: ProviderHostSessionTurnResult<ProjectionState, ProjectionOutput>
  readonly fallbackProjectionState: ProjectionState
  readonly fallbackProjectionOutput: ProjectionOutput
}): ResolvedProviderHostTurnView<ProjectionState, ProjectionOutput> {
  const view = buildProviderHostTurnView(options.result)
  return {
    ...view,
    projectionOutput: view.projectionOutput ?? options.fallbackProjectionOutput,
    projectionState: view.projectionState ?? options.fallbackProjectionState,
  }
}

/**
 * Build a host-facing transcript projection from any transcript-bearing turn/result shape.
 */
export function buildHostTranscriptProjection(source: {
  readonly transcript: SessionTranscriptV1
}): HostTranscriptProjection {
  const entries: Array<
    | { kind: "assistant_text"; text: string }
    | { kind: "tool_preview"; text: string }
  > = []
  const assistantTexts: string[] = []
  const toolPreviews: string[] = []

  for (const item of source.transcript.items) {
    if (item.kind === "assistant_message" && item.visibility === "model") {
      const text = ((item.content ?? {}) as { text?: string }).text
      if (typeof text === "string" && text.length > 0) {
        entries.push({ kind: "assistant_text", text })
        assistantTexts.push(text)
      }
      continue
    }

    if (item.kind === "tool_result") {
      const parts = ((item.content ?? {}) as { parts?: Array<{ preview?: string }> }).parts ?? []
      const preview = parts
        .map((part) => part.preview)
        .filter((value): value is string => typeof value === "string" && value.length > 0)
        .join("\n")
      if (preview.length > 0) {
        entries.push({ kind: "tool_preview", text: preview })
        toolPreviews.push(preview)
      }
    }
  }

  return {
    entries,
    assistantTexts,
    toolPreviews,
  }
}

/**
 * Emit a normalized transcript projection through a host callback sink.
 */
export async function emitHostProjectionCallbacks(
  sink: HostProjectionCallbackSink,
  projection: HostTranscriptProjection,
  agentEvents: Array<{ stream: string; data: Record<string, unknown> }> = [],
): Promise<void> {
  let assistantStarted = false
  for (const entry of projection.entries) {
    if (entry.kind === "assistant_text") {
      if (!assistantStarted) {
        assistantStarted = true
        await sink.onAssistantMessageStart?.()
      }
      await sink.onPartialReply?.({ text: entry.text })
      continue
    }
    await sink.onToolResult?.({ text: entry.text, mediaUrls: [] })
  }

  for (const event of agentEvents) {
    await sink.onAgentEvent?.(event)
  }
}

/**
 * Normalize the common envelope many hosts need: transcript projection plus a host-shaped result.
 */
export function buildHostProjectionEnvelope<Result>(options: {
  readonly transcriptSource: { readonly transcript: SessionTranscriptV1 }
  readonly result: Result
}): HostProjectionEnvelope<Result> {
  return {
    transcript: buildHostTranscriptProjection(options.transcriptSource),
    result: options.result,
  }
}

/**
 * Build a small, reusable host-facing result metadata block without coupling hosts to any one
 * bridge implementation.
 */
export function buildHostResultMeta(options: {
  readonly sessionId: string
  readonly provider: string
  readonly model: string
  readonly stopReason: string
  readonly durationMs?: number
  readonly usage?: Record<string, unknown>
}): HostResultMeta {
  return {
    durationMs: options.durationMs ?? 0,
    agentMeta: {
      sessionId: options.sessionId,
      provider: options.provider,
      model: options.model,
      usage: options.usage,
    },
    stopReason: options.stopReason,
  }
}

/**
 * Normalize a host-managed transcript input into the canonical transcript envelope used by the
 * session helper. Hosts may retain looser local item typing, but this helper pins the shape at
 * the Host Kit boundary.
 */
export function normalizeHostManagedTranscript(
  sessionId: string,
  transcript: HostManagedTranscript | null | undefined,
): SessionTranscriptV1 | null {
  if (!transcript) {
    return null
  }
  if (Array.isArray(transcript)) {
    return {
      schemaVersion: "bb.session_transcript.v1",
      sessionId,
      items: transcript as SessionTranscriptV1Item[],
    }
  }
  return transcript
}

/**
 * Create a reusable host-managed provider-turn session that keeps transcript continuity and
 * optional projection state outside of host-specific bridge code.
 */
export function createProviderHostSession<Input, ProjectionState, ProjectionOutput>(
  options: ProviderHostSessionOptions<Input, ProjectionState, ProjectionOutput>,
): ProviderHostSession<Input, ProjectionState, ProjectionOutput> {
  let transcript: SessionTranscriptV1 | null = options.initialTranscript ?? null
  let projectionState: ProjectionState | null = options.initialProjectionState ?? null

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
