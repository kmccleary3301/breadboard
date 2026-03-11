import type {
  EffectiveToolSurfaceV1,
  KernelEventV1,
  TerminalInteractionV1,
  TerminalOutputDeltaV1,
  TerminalSessionDescriptorV1,
  ProviderExchangeV1,
  RunRequestV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
  TerminalCleanupResultV1,
  TerminalRegistrySnapshotV1,
  ToolBindingV1,
  ToolSupportClaimV1,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"
import type {
  DriverMediatedToolTurnResult,
  ProviderTextTurnResult,
} from "@breadboard/kernel-core"
import type { ExecutionProfileId, Workspace } from "@breadboard/workspace"

export type ProjectionProfileId = "host_callbacks" | "raw_kernel_events" | "ai_sdk_transport"

export interface SupportClaim {
  readonly level: "supported" | "fallback" | "unsupported"
  readonly summary: string
  readonly executionProfileId: ExecutionProfileId
  readonly executionProfile: Workspace["defaultExecutionProfile"]
  readonly fallbackAvailable: boolean
  readonly unsupportedFields: readonly string[]
  readonly evidenceMode: string | null
  readonly recommendedHostMode: "inline" | "streaming" | "background"
  readonly confidence: "high" | "medium" | "low"
  readonly terminalSupport?: {
    readonly canStart: boolean
    readonly canInteract: boolean
    readonly canPoll: boolean
    readonly canList: boolean
    readonly canCleanup: boolean
    readonly streamMode: "pty" | "pipes"
  }
}

export interface ProjectionProfile {
  readonly id: ProjectionProfileId
  readonly summary: string
}

export interface HostSessionDescriptor {
  readonly sessionId: string
  readonly workspaceRoot?: string | null
  readonly requestedModel?: string | null
  readonly requestedProvider?: string | null
  readonly projectionProfileId?: ProjectionProfileId
}

export interface ProviderTurnInput {
  readonly request: RunRequestV1
  readonly providerExchange: ProviderExchangeV1
  readonly assistantText: string
  readonly existingTranscript?: SessionTranscriptV1 | Array<Record<string, unknown> | SessionTranscriptV1Item>
}

export interface ToolTurnInput {
  readonly request: RunRequestV1
  readonly toolName: string
  readonly command: string[]
  readonly assistantText?: string | null
  readonly driverIdHint?: "trusted_local" | "oci" | "remote"
}

export interface BackboneTurnResult {
  readonly supportClaim: SupportClaim
  readonly projectionProfile: ProjectionProfile
  readonly runContextId: string
  readonly transcript: SessionTranscriptV1
  readonly events: readonly KernelEventV1[]
  readonly providerTurn?: ProviderTextTurnResult
  readonly driverTurn?: DriverMediatedToolTurnResult
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface TerminalCleanupInput {
  readonly cleanupId: string
  readonly scope: TerminalCleanupResultV1["scope"]
  readonly cleanedSessionIds: string[]
  readonly failedSessionIds?: string[]
}

export interface BackboneTerminalStartInput {
  readonly command: string[]
  readonly cwd?: string | null
  readonly executionProfileId?: ExecutionProfileId
  readonly terminalSessionId?: string
  readonly startupCallId?: string | null
  readonly ownerTaskId?: string | null
  readonly publicHandles?: TerminalSessionDescriptorV1["public_handles"]
  readonly persistenceScope?: TerminalSessionDescriptorV1["persistence_scope"]
  readonly continuationScope?: TerminalSessionDescriptorV1["continuation_scope"]
  readonly streamMode?: TerminalSessionDescriptorV1["stream_mode"]
  readonly streamSplit?: TerminalSessionDescriptorV1["stream_split"]
}

export interface BackboneTerminalStartResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly descriptor: TerminalSessionDescriptorV1 | null
  readonly outputDeltas: readonly TerminalOutputDeltaV1[]
  readonly end?: import("@breadboard/kernel-contracts").TerminalSessionEndV1
  readonly session: BackboneTerminalSessionView | null
}

export interface BackboneTerminalInteractionInput {
  readonly terminalSessionId: string
  readonly interactionKind: TerminalInteractionV1["interaction_kind"]
  readonly executionProfileId?: ExecutionProfileId
  readonly causingCallId?: string | null
  readonly inputText?: string | null
  readonly inputB64?: string | null
  readonly signal?: string | null
  readonly settleMs?: number
}

export interface BackboneTerminalInteractionResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly interaction: TerminalInteractionV1 | null
  readonly outputDeltas: readonly TerminalOutputDeltaV1[]
  readonly end?: import("@breadboard/kernel-contracts").TerminalSessionEndV1
}

export interface BackboneTerminalSnapshotResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly snapshot: TerminalRegistrySnapshotV1 | null
}

export interface BackboneTerminalGetResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly snapshot: TerminalRegistrySnapshotV1 | null
  readonly session: BackboneTerminalSessionView | null
}

export interface BackboneTerminalCleanupInput {
  readonly scope: TerminalCleanupResultV1["scope"]
  readonly executionProfileId?: ExecutionProfileId
  readonly sessionIds?: string[]
  readonly signal?: string | null
  readonly cleanupId?: string
}

export interface BackboneTerminalCleanupResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly result: TerminalCleanupResultV1 | null
}

export interface BackboneTerminalListViewResult {
  readonly supportClaim: SupportClaim
  readonly unsupportedCase?: UnsupportedCaseV1
  readonly snapshot: TerminalRegistrySnapshotV1 | null
  readonly sessions: readonly BackboneTerminalSessionView[]
}

export interface BackboneTerminalSessionSummary {
  readonly terminalSessionId: string
  readonly commandSummary: string
  readonly status: "running" | "ended"
  readonly publicHandles: readonly NonNullable<TerminalSessionDescriptorV1["public_handles"]>[number][]
  readonly outputPreview: string
  readonly outputChunkCount: number
  readonly persistenceScope: TerminalSessionDescriptorV1["persistence_scope"]
  readonly continuationScope: TerminalSessionDescriptorV1["continuation_scope"]
  readonly lastSnapshotId: string | null
  readonly lastEndState: import("@breadboard/kernel-contracts").TerminalSessionEndV1["terminal_state"] | null
  readonly exitCode: number | null
  readonly durationMs: number | null
  readonly artifactRefCount: number
  readonly evidenceRefCount: number
}

export interface BackboneTerminalSessionView {
  readonly descriptor: TerminalSessionDescriptorV1
  readonly supportClaim: SupportClaim
  readonly executionProfileId: ExecutionProfileId
  readonly status: "running" | "ended"
  readonly lastSnapshot: TerminalRegistrySnapshotV1 | null
  readonly lastEnd: import("@breadboard/kernel-contracts").TerminalSessionEndV1 | null
  summary(): BackboneTerminalSessionSummary
  refresh(): Promise<BackboneTerminalSnapshotResult>
  poll(options?: { settleMs?: number; causingCallId?: string | null }): Promise<BackboneTerminalInteractionResult>
  writeStdin(
    inputText: string,
    options?: { causingCallId?: string | null; settleMs?: number },
  ): Promise<BackboneTerminalInteractionResult>
  sendSignal(signal: string, options?: { causingCallId?: string | null }): Promise<BackboneTerminalInteractionResult>
  snapshot(): Promise<BackboneTerminalSnapshotResult>
  cleanup(options?: { signal?: string | null }): Promise<BackboneTerminalCleanupResult>
}

export interface EffectiveToolSurfaceInput {
  readonly surfaceId: string
  readonly bindings: ToolBindingV1[]
  readonly claims: ToolSupportClaimV1[]
  readonly projectionProfileId?: string | null
}

export interface EffectiveToolSurfaceEntryView {
  readonly toolId: string
  readonly bindingId: string | null
  readonly bindingKind: ToolBindingV1["binding_kind"] | null
  readonly level: ToolSupportClaimV1["level"] | "unbound"
  readonly summary: string
  readonly exposedToModel: boolean
  readonly selectedViaFallback: boolean
  readonly hiddenReason: string | null
  readonly resolutionPath: readonly string[]
}

export interface EffectiveToolSurfaceAnalysisView {
  readonly surface: EffectiveToolSurfaceV1
  readonly visibleEntries: readonly EffectiveToolSurfaceEntryView[]
  readonly hiddenEntries: readonly EffectiveToolSurfaceEntryView[]
  readonly unsupportedEntries: readonly EffectiveToolSurfaceEntryView[]
}

export interface EffectiveToolSurfaceResolutionInput extends EffectiveToolSurfaceInput {
  readonly profileId?: string | null
  readonly providerFamily?: string | null
  readonly driverClass?: string | null
  readonly imageId?: string | null
  readonly serviceIds?: readonly string[]
  readonly features?: readonly string[]
  readonly toolPacks?: readonly import("@breadboard/kernel-core").ToolPackDefinition[]
  readonly activePackIds?: readonly string[]
}

export interface BackboneTerminalApi {
  reduceRegistry(events: readonly KernelEventV1[]): TerminalRegistrySnapshotV1
  buildCleanupResult(input: TerminalCleanupInput): TerminalCleanupResultV1
  classify(input: { executionProfileId?: ExecutionProfileId }): SupportClaim
  start(input: BackboneTerminalStartInput): Promise<BackboneTerminalStartResult>
  interact(input: BackboneTerminalInteractionInput): Promise<BackboneTerminalInteractionResult>
  get(input: { terminalSessionId: string; executionProfileId?: ExecutionProfileId }): Promise<BackboneTerminalGetResult>
  snapshot(input?: { executionProfileId?: ExecutionProfileId }): Promise<BackboneTerminalSnapshotResult>
  list(input?: { executionProfileId?: ExecutionProfileId }): Promise<BackboneTerminalSnapshotResult>
  listViews(input?: { executionProfileId?: ExecutionProfileId }): Promise<BackboneTerminalListViewResult>
  cleanup(input: BackboneTerminalCleanupInput): Promise<BackboneTerminalCleanupResult>
}

export interface BackboneToolSurfaceApi {
  buildEffectiveSurface(input: EffectiveToolSurfaceInput): EffectiveToolSurfaceV1
  resolveEffectiveSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceV1
  analyzeEffectiveSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceAnalysisView
  resolveBindings(input: EffectiveToolSurfaceResolutionInput): readonly import("@breadboard/kernel-core").ResolvedToolBinding[]
}

export interface BackboneSession {
  readonly descriptor: HostSessionDescriptor
  readonly workspace: Workspace
  readonly projectionProfile: ProjectionProfile
  readonly terminals: BackboneTerminalApi
  readonly tools: BackboneToolSurfaceApi
  classifyProviderTurn(input: ProviderTurnInput): SupportClaim
  classifyToolTurn(input: ToolTurnInput): SupportClaim
  runProviderTurn(input: ProviderTurnInput): Promise<BackboneTurnResult>
  runToolTurn(input: ToolTurnInput): Promise<BackboneTurnResult>
}

export interface BackboneOptions {
  readonly workspace: Workspace
  readonly defaultProjectionProfileId?: ProjectionProfileId
  readonly remoteHttp?: import("@breadboard/execution-driver-remote").RemoteExecutionHttpOptions
  readonly remoteExecutor?: import("@breadboard/execution-driver-remote").RemoteSandboxExecutor
  readonly ociTerminalAdapter?: import("@breadboard/execution-driver-oci").OciTerminalSessionAdapter
}

export interface Backbone {
  readonly workspace: Workspace
  openSession(descriptor: HostSessionDescriptor): BackboneSession
}
