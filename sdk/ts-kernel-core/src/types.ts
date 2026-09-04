import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  KernelEventV2,
  ProviderExchangeV1,
  RunContextV1,
  SandboxRequestV1,
  SandboxResultV1,
  SessionTranscriptV1,
  SessionTranscriptV2,
  SessionTranscriptV1Item,
  ToolCallV2,
  ToolExecutionOutcomeV2,
  ToolModelRenderV2,
  TranscriptContinuationPatchV1,
} from "@breadboard/kernel-contracts"
import type {
  ExecutionDriverEvidenceExpectationV1,
  ExecutionDriverSideEffectExpectationV1,
  ExecutionLivenessEvidenceV1,
  ExecutionWorldV1,
} from "@breadboard/execution-drivers"
import type { LocalCommandExecutor } from "@breadboard/execution-driver-local"
import type { OciCommandExecutor } from "@breadboard/execution-driver-oci"
import type {
  RemoteExecutionHttpOptions,
  RemoteSandboxExecutor,
  ScheduledExecutionDriverRegistrationV1,
} from "@breadboard/execution-driver-remote"

export interface StaticTextTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  executionMode?: string | null
  activeMode?: string | null
  assistantText: string
  startedAt?: string
}

export interface StaticTextTurnResult {
  runContext: RunContextV1
  events: KernelEventV2[]
  transcript: SessionTranscriptV2
}

export interface ScriptedToolTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  executionMode?: string | null
  activeMode?: string | null
  toolCall: ToolCallV2
  toolOutcome: ToolExecutionOutcomeV2
  toolRender: ToolModelRenderV2
  assistantText?: string | null
  startedAt?: string
}

export interface ProviderTextTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  executionMode?: string | null
  activeMode?: string | null
  providerExchange: ProviderExchangeV1
  assistantText: string
  startedAt?: string
}

export interface ProviderTextTurnResult extends StaticTextTurnResult {
  providerExchange: ProviderExchangeV1
  transcriptContinuationPatch?: TranscriptContinuationPatchV1
}

export interface DriverMediatedToolTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  activeMode?: string | null
  toolName: string
  toolDescription?: string | null
  command: string[]
  workspaceRef?: string | null
  imageRef?: string | null
  isolationClass?: ExecutionCapabilityV1["isolation_class"]
  securityTier?: ExecutionCapabilityV1["security_tier"]
  evidenceMode?: ExecutionCapabilityV1["evidence_mode"]
  allowRunPrograms?: string[]
  allowNetHosts?: string[]
  driverIdHint?: "trusted_local" | "oci" | "remote" | "ray" | "slurm"
  deadlineMs?: number | null
  timeoutMs?: number | null
  signal?: AbortSignal
  terminationGraceMs?: number
  onTimeout?: (input: { driverId: string; request: SandboxRequestV1; liveness: ExecutionLivenessEvidenceV1 }) => void | Promise<void>
  executeSandbox?: (
    request: SandboxRequestV1,
    context: {
      capability: ExecutionCapabilityV1
      placement: ExecutionPlacementV1
      driverId: string
      signal?: AbortSignal
      deadlineAtMs?: number | null
      terminationGraceMs?: number
    },
  ) => Promise<SandboxResultV1>
  executionWorld?: ExecutionWorldV1
  assistantText?: string | null
  localCommandExecutor?: LocalCommandExecutor
  ociCommandExecutor?: OciCommandExecutor
  ociRuntimeCommand?: string
  ociWorkspaceMountTarget?: string
  remoteExecutor?: RemoteSandboxExecutor
  remoteHttp?: RemoteExecutionHttpOptions
  ray?: ScheduledExecutionDriverRegistrationV1
  slurm?: ScheduledExecutionDriverRegistrationV1
  startedAt?: string
}

export interface DriverMediatedToolTurnResult extends StaticTextTurnResult {
  executionCapability: ExecutionCapabilityV1
  executionPlacement: ExecutionPlacementV1
  driverId: string
  sandboxRequest: SandboxRequestV1
  sandboxResult: SandboxResultV1
  sideEffectExpectation: ExecutionDriverSideEffectExpectationV1
  evidenceExpectation: ExecutionDriverEvidenceExpectationV1
}

export interface ProviderTextContinuationTurnOptions extends ProviderTextTurnOptions {
  existingTranscript:
    | SessionTranscriptV1
    | SessionTranscriptV2
    | Array<Record<string, unknown> | SessionTranscriptV1Item | SessionTranscriptV2["items"][number]>
}
