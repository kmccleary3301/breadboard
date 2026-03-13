import type {
  CoordinationInspectionSnapshotV1,
  KernelEventV1,
  ProviderExchangeV1,
  RunRequestV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
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
  readonly coordinationInspection: CoordinationInspectionSnapshotV1
  readonly providerTurn?: ProviderTextTurnResult
  readonly driverTurn?: DriverMediatedToolTurnResult
  readonly unsupportedCase?: UnsupportedCaseV1
}

export interface CoordinationInspectionInput {
  readonly snapshot?: CoordinationInspectionSnapshotV1 | null
  readonly events?: readonly KernelEventV1[]
}

export interface BackboneSession {
  readonly descriptor: HostSessionDescriptor
  readonly workspace: Workspace
  readonly projectionProfile: ProjectionProfile
  classifyProviderTurn(input: ProviderTurnInput): SupportClaim
  classifyToolTurn(input: ToolTurnInput): SupportClaim
  runProviderTurn(input: ProviderTurnInput): Promise<BackboneTurnResult>
  runToolTurn(input: ToolTurnInput): Promise<BackboneTurnResult>
  inspectCoordination(input?: CoordinationInspectionInput): CoordinationInspectionSnapshotV1
}

export interface BackboneOptions {
  readonly workspace: Workspace
  readonly defaultProjectionProfileId?: ProjectionProfileId
}

export interface Backbone {
  readonly workspace: Workspace
  openSession(descriptor: HostSessionDescriptor): BackboneSession
}
