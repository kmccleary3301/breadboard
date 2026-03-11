import type {
  BackboneSession,
  BackboneTurnResult,
  EffectiveToolSurfaceAnalysisView,
  EffectiveToolSurfaceInput,
  ProviderTurnInput,
  SupportClaim,
  TerminalCleanupInput,
} from "@breadboard/backbone"
import type {
  EffectiveToolSurfaceV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
  TerminalCleanupResultV1,
  TerminalOutputDeltaV1,
  TerminalRegistrySnapshotV1,
  TerminalSessionDescriptorV1,
} from "@breadboard/kernel-contracts"
import type { TerminalOutputShape, TerminalSessionEndShape, Workspace, WorkspaceArtifactRef } from "@breadboard/workspace"

export type HostKitMode = "supported" | "fallback"

export interface HostKitClassification<Request> {
  readonly mode: HostKitMode
  readonly supportClaim: SupportClaim
  readonly unsupportedFields: readonly string[]
  readonly request: Request
}

export interface SupportClaimView {
  readonly level: SupportClaim["level"]
  readonly summary: string
  readonly executionProfileId: string
  readonly executionProfileSummary: string
  readonly recommendedHostMode: SupportClaim["recommendedHostMode"]
  readonly confidence: SupportClaim["confidence"]
  readonly fallbackAvailable: boolean
  readonly unsupportedFields: readonly string[]
  readonly terminalSupport: SupportClaim["terminalSupport"] | null
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

export interface HostTerminalSessionView {
  readonly terminalSessionId: string
  readonly commandSummary: string
  readonly status: "running" | "ended"
  readonly publicHandles: readonly {
    namespace: string
    label: string
    value: string | number
    audience: "model" | "host"
  }[]
  readonly outputPreview?: string | null
  readonly outputChunkCount?: number
  readonly persistenceScope: string
  readonly continuationScope: string
  readonly lastSnapshotId?: string | null
  readonly lastEndState?: string | null
  readonly exitCode?: number | null
  readonly durationMs?: number | null
  readonly artifactRefs?: readonly WorkspaceArtifactRef[]
  readonly evidenceRefs?: readonly string[]
  readonly support?: SupportClaimView | null
}

export interface HostTerminalOutputView {
  readonly text: string
  readonly shape: TerminalOutputShape | null
}

export interface HostTerminalEndView {
  readonly terminalState: string | null
  readonly exitCode: number | null
  readonly durationMs: number | null
  readonly artifactRefs: readonly WorkspaceArtifactRef[]
  readonly evidenceRefs: readonly string[]
}

export interface HostTerminalCleanupView {
  readonly cleanupId: string
  readonly scope: string
  readonly cleanedCount: number
  readonly failedCount: number
  readonly cleanedSessionIds: readonly string[]
  readonly failedSessionIds: readonly string[]
}

export interface HostTerminalRegistryView {
  readonly snapshotId: string
  readonly activeSessions: HostTerminalSessionView[]
  readonly endedSessionIds: readonly string[]
}

export interface EffectiveToolSurfaceView {
  readonly surfaceId: string
  readonly visibleToolIds: readonly string[]
  readonly hiddenToolIds: readonly string[]
  readonly bindingIds: readonly string[]
}

export interface EffectiveToolSurfaceEntryHostView {
  readonly toolId: string
  readonly bindingId: string | null
  readonly bindingKind: string | null
  readonly level: string
  readonly summary: string
  readonly exposedToModel: boolean
  readonly selectedViaFallback: boolean
  readonly hiddenReason: string | null
  readonly resolutionPath: readonly string[]
}

export interface EffectiveToolSurfaceAnalysisHostView {
  readonly surface: EffectiveToolSurfaceView
  readonly visibleEntries: readonly EffectiveToolSurfaceEntryHostView[]
  readonly hiddenEntries: readonly EffectiveToolSurfaceEntryHostView[]
  readonly unsupportedEntries: readonly EffectiveToolSurfaceEntryHostView[]
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
 * Project a runtime-facing SupportClaim into a smaller host/product-facing view that is easy to
 * log, render, and compare across host integrations.
 */
export function buildSupportClaimView(claim: SupportClaim): SupportClaimView {
  return {
    level: claim.level,
    summary: claim.summary,
    executionProfileId: claim.executionProfileId,
    executionProfileSummary: claim.executionProfile.summary,
    recommendedHostMode: claim.recommendedHostMode,
    confidence: claim.confidence,
    fallbackAvailable: claim.fallbackAvailable,
    unsupportedFields: claim.unsupportedFields,
    terminalSupport: claim.terminalSupport ?? null,
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
 * Normalize a terminal registry snapshot into a small host-facing view that avoids exposing the
 * full kernel descriptor shape to every host integration.
 */
export function buildTerminalRegistryView(
  snapshot: TerminalRegistrySnapshotV1,
): HostTerminalRegistryView {
  return {
    snapshotId: snapshot.snapshot_id,
    activeSessions: snapshot.active_sessions.map((session) => buildTerminalSessionView(session)),
    endedSessionIds: snapshot.ended_session_ids ?? [],
  }
}

export function buildTerminalSessionView(
  descriptor: TerminalSessionDescriptorV1,
  options: {
    readonly status?: "running" | "ended"
    readonly outputPreview?: string | null
    readonly outputChunkCount?: number
    readonly lastSnapshotId?: string | null
    readonly lastEndState?: string | null
    readonly exitCode?: number | null
    readonly durationMs?: number | null
    readonly artifactRefs?: readonly WorkspaceArtifactRef[]
    readonly evidenceRefs?: readonly string[]
    readonly support?: SupportClaimView | null
  } = {},
): HostTerminalSessionView {
  return {
    terminalSessionId: descriptor.terminal_session_id,
    commandSummary: descriptor.command.join(" "),
    status: options.status ?? "running",
    publicHandles: [...(descriptor.public_handles ?? [])],
    outputPreview: options.outputPreview ?? null,
    outputChunkCount: options.outputChunkCount ?? 0,
    persistenceScope: descriptor.persistence_scope,
    continuationScope: descriptor.continuation_scope,
    lastSnapshotId: options.lastSnapshotId ?? null,
    lastEndState: options.lastEndState ?? null,
    exitCode: options.exitCode ?? null,
    durationMs: options.durationMs ?? null,
    artifactRefs: options.artifactRefs ?? [],
    evidenceRefs: options.evidenceRefs ?? [],
    support: options.support ?? null,
  }
}

function decodeTerminalOutputText(outputDeltas: readonly TerminalOutputDeltaV1[]): string {
  return outputDeltas
    .map((delta) => Buffer.from(delta.chunk_b64, "base64").toString("utf8"))
    .join("")
}

export function buildTerminalOutputView(options: {
  readonly outputDeltas: readonly TerminalOutputDeltaV1[]
  readonly workspace?: Workspace | null
}): HostTerminalOutputView {
  const text = decodeTerminalOutputText(options.outputDeltas)
  return {
    text,
    shape: options.workspace ? options.workspace.shapeTerminalOutput(text, { chunkCount: options.outputDeltas.length }) : null,
  }
}

/**
 * Convenience helper for hosts that want to shape terminal output through Backbone while keeping
 * workspace-aware output shaping at the product layer.
 */
export function buildBackboneTerminalOutputView(
  session: BackboneSession,
  outputDeltas: readonly TerminalOutputDeltaV1[],
): HostTerminalOutputView {
  const text = decodeTerminalOutputText(outputDeltas)
  return {
    text,
    shape: session.workspace.shapeTerminalOutputDeltas(outputDeltas),
  }
}

export function buildBackboneTerminalSessionView(
  sessionView: import("@breadboard/backbone").BackboneTerminalSessionView,
): HostTerminalSessionView {
  const summary = sessionView.summary()
  return buildTerminalSessionView(sessionView.descriptor, {
    status: summary.status,
    outputPreview: summary.outputPreview,
    outputChunkCount: summary.outputChunkCount,
    lastSnapshotId: summary.lastSnapshotId,
    lastEndState: summary.lastEndState,
    exitCode: summary.exitCode,
    durationMs: summary.durationMs,
    artifactRefs: sessionView.lastEnd?.artifact_refs?.map((location) => ({
      artifactId: location,
      kind: "generic" as const,
      location,
    })) ?? [],
    evidenceRefs: sessionView.lastEnd?.evidence_refs ?? [],
    support: buildSupportClaimView(sessionView.supportClaim),
  })
}

export function buildTerminalEndView(end: TerminalSessionEndShape): HostTerminalEndView {
  return {
    terminalState: end.terminalState,
    exitCode: end.exitCode,
    durationMs: end.durationMs,
    artifactRefs: end.artifactRefs,
    evidenceRefs: end.evidenceRefs,
  }
}

export function buildBackboneTerminalEndView(
  session: BackboneSession,
  end: {
    readonly artifact_refs?: readonly string[]
    readonly evidence_refs?: readonly string[]
    readonly terminal_state?: string | null
    readonly exit_code?: number | null
    readonly duration_ms?: number | null
  },
): HostTerminalEndView {
  return buildTerminalEndView(session.workspace.shapeTerminalSessionEnd(end))
}

export function buildTerminalCleanupView(
  result: TerminalCleanupResultV1,
): HostTerminalCleanupView {
  return {
    cleanupId: result.cleanup_id,
    scope: result.scope,
    cleanedCount: result.cleaned_session_ids.length,
    failedCount: result.failed_session_ids?.length ?? 0,
    cleanedSessionIds: result.cleaned_session_ids,
    failedSessionIds: result.failed_session_ids ?? [],
  }
}

/**
 * Project an effective tool surface into a stable host/product-facing view.
 */
export function buildEffectiveToolSurfaceView(
  surface: EffectiveToolSurfaceV1,
): EffectiveToolSurfaceView {
  return {
    surfaceId: surface.surface_id,
    visibleToolIds: surface.tool_ids,
    hiddenToolIds: surface.hidden_tool_ids ?? [],
    bindingIds: surface.binding_ids,
  }
}

function buildEffectiveToolEntryHostView(
  entry: EffectiveToolSurfaceAnalysisView["visibleEntries"][number],
): EffectiveToolSurfaceEntryHostView {
  return {
    toolId: entry.toolId,
    bindingId: entry.bindingId,
    bindingKind: entry.bindingKind,
    level: entry.level,
    summary: entry.summary,
    exposedToModel: entry.exposedToModel,
    selectedViaFallback: entry.selectedViaFallback,
    hiddenReason: entry.hiddenReason,
    resolutionPath: entry.resolutionPath,
  }
}

/**
 * Project a support-rich effective tool surface into a stable host/product-facing analysis view.
 */
export function buildEffectiveToolSurfaceAnalysisView(
  analysis: EffectiveToolSurfaceAnalysisView,
): EffectiveToolSurfaceAnalysisHostView {
  return {
    surface: buildEffectiveToolSurfaceView(analysis.surface),
    visibleEntries: analysis.visibleEntries.map(buildEffectiveToolEntryHostView),
    hiddenEntries: analysis.hiddenEntries.map(buildEffectiveToolEntryHostView),
    unsupportedEntries: analysis.unsupportedEntries.map(buildEffectiveToolEntryHostView),
  }
}

/**
 * Convenience helper for hosts that want to reduce a terminal registry through Backbone and
 * immediately receive a stable host-facing view.
 */
export function buildBackboneTerminalRegistryView(
  session: BackboneSession,
  events: readonly import("@breadboard/kernel-contracts").KernelEventV1[],
): HostTerminalRegistryView {
  return buildTerminalRegistryView(session.terminals.reduceRegistry(events))
}

export async function buildBackboneLiveTerminalRegistryView(
  session: BackboneSession,
  input?: { executionProfileId?: import("@breadboard/workspace").ExecutionProfileId },
): Promise<HostTerminalRegistryView | null> {
  const result = await session.terminals.list(input)
  return result.snapshot ? buildTerminalRegistryView(result.snapshot) : null
}

/**
 * Convenience helper for hosts that want to build a cleanup result through Backbone while keeping
 * the call site on the product-facing Host Kit surface.
 */
export function buildBackboneTerminalCleanupResult(
  session: BackboneSession,
  input: TerminalCleanupInput,
): TerminalCleanupResultV1 {
  return session.terminals.buildCleanupResult(input)
}

export function buildBackboneTerminalCleanupView(
  session: BackboneSession,
  input: TerminalCleanupInput,
): HostTerminalCleanupView {
  return buildTerminalCleanupView(session.terminals.buildCleanupResult(input))
}

/**
 * Convenience helper for hosts that want to resolve a product-facing effective tool surface via
 * Backbone without reaching into kernel-core directly.
 */
export function buildBackboneEffectiveToolSurfaceView(
  session: BackboneSession,
  input: EffectiveToolSurfaceInput,
): EffectiveToolSurfaceView {
  return buildEffectiveToolSurfaceView(session.tools.buildEffectiveSurface(input))
}

/**
 * Convenience helper for hosts that want the richer support/fallback explanation behind an
 * effective tool surface instead of only the visible tool id list.
 */
export function buildBackboneEffectiveToolSurfaceAnalysisView(
  session: BackboneSession,
  input: Parameters<BackboneSession["tools"]["analyzeEffectiveSurface"]>[0],
): EffectiveToolSurfaceAnalysisHostView {
  return buildEffectiveToolSurfaceAnalysisView(session.tools.analyzeEffectiveSurface(input))
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
