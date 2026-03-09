import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  ProviderExchangeV1,
  RunContextV1,
  SandboxRequestV1,
  SandboxResultV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
  ToolCallV1,
  ToolExecutionOutcomeV1,
  ToolModelRenderV1,
} from "@breadboard/kernel-contracts"
import type {
  ExecutionDriverEvidenceExpectationV1,
  ExecutionDriverSideEffectExpectationV1,
} from "@breadboard/execution-drivers"
import type { LocalCommandExecutor } from "@breadboard/execution-driver-local"
import type { OciCommandExecutor } from "@breadboard/execution-driver-oci"

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
  events: import("@breadboard/kernel-contracts").KernelEventV1[]
  transcript: SessionTranscriptV1
}

export interface ScriptedToolTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  executionMode?: string | null
  activeMode?: string | null
  toolCall: ToolCallV1
  toolOutcome: ToolExecutionOutcomeV1
  toolRender: ToolModelRenderV1
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
  transcriptContinuationPatch?: import("@breadboard/kernel-contracts").TranscriptContinuationPatchV1
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
  driverIdHint?: "trusted_local" | "oci"
  assistantText?: string | null
  executeSandbox?: (
    request: SandboxRequestV1,
    context: { capability: ExecutionCapabilityV1; placement: ExecutionPlacementV1; driverId: string },
  ) => Promise<SandboxResultV1>
  localCommandExecutor?: LocalCommandExecutor
  ociCommandExecutor?: OciCommandExecutor
  ociRuntimeCommand?: string
  ociWorkspaceMountTarget?: string
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
  existingTranscript: SessionTranscriptV1 | Array<Record<string, unknown> | SessionTranscriptV1Item>
}
