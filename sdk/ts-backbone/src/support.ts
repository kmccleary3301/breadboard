import type { ExecutionCapabilityV1, RunRequestV1 } from "@breadboard/kernel-contracts"
import { buildExecutionCapabilityFromRunRequest } from "@breadboard/kernel-core"
import type {
  BackboneTurnResult,
  ProjectionProfile,
  ProjectionProfileId,
  SupportClaim,
  ToolTurnInput,
} from "./types.js"
import type { ExecutionProfileId, Workspace } from "@breadboard/workspace"

export function buildProjectionProfile(id: ProjectionProfileId): ProjectionProfile {
  switch (id) {
    case "host_callbacks":
      return { id, summary: "Project turns for host callback/event consumers." }
    case "raw_kernel_events":
      return { id, summary: "Expose raw kernel events without additional host shaping." }
    case "ai_sdk_transport":
      return { id, summary: "Project turns for AI SDK-compatible transport streams." }
  }
}

export function buildExecutionProfileIdFromCapability(capability: ExecutionCapabilityV1): ExecutionProfileId {
  if (capability.security_tier === "multi_tenant") return "remote_isolated"
  if (["oci", "gvisor", "kata"].includes(capability.isolation_class)) return "sandboxed_local"
  if (capability.security_tier === "shared_host") return "constrained_local"
  return "trusted_local"
}

export function buildSupportClaim(options: {
  workspace: Workspace
  request: RunRequestV1
  executionProfileId?: ExecutionProfileId
  unsupportedFields?: readonly string[]
  summary?: string
  recommendedHostMode?: "inline" | "streaming" | "background"
  confidence?: "high" | "medium" | "low"
}): SupportClaim {
  const capability = buildExecutionCapabilityFromRunRequest(options.request, {
    capabilityId: `${options.request.request_id}:backbone-support`,
  })
  const executionProfileId = options.executionProfileId ?? buildExecutionProfileIdFromCapability(capability)
  const executionProfile = options.workspace.getExecutionProfile(executionProfileId)
  const unsupportedFields = [...(options.unsupportedFields ?? [])]
  const supported = options.workspace.supportsProfile(executionProfileId) && unsupportedFields.length === 0
  return {
    level: supported ? "supported" : "unsupported",
    summary:
      options.summary ??
      (supported
        ? `Supported on execution profile ${executionProfileId}.`
        : `Unsupported on execution profile ${executionProfileId}.`),
    executionProfileId,
    executionProfile,
    fallbackAvailable: !supported,
    unsupportedFields,
    evidenceMode: capability.evidence_mode,
    recommendedHostMode:
      options.recommendedHostMode ??
      (executionProfile.backendHint === "remote" ? "background" : capability.isolation_class === "none" ? "inline" : "streaming"),
    confidence: options.confidence ?? (supported ? "high" : "medium"),
  }
}

export function buildToolTurnSupportClaim(workspace: Workspace, input: ToolTurnInput): SupportClaim {
  const executionProfileId: ExecutionProfileId =
    input.driverIdHint === "remote"
      ? "remote_isolated"
      : input.driverIdHint === "oci"
        ? "sandboxed_local"
        : "trusted_local"
  return buildSupportClaim({
    workspace,
    request: input.request,
    executionProfileId,
    summary: `Tool turn requested on ${executionProfileId}.`,
    recommendedHostMode: executionProfileId === "remote_isolated" ? "background" : "streaming",
  })
}

export function buildBackboneTurnResult(input: {
  supportClaim: SupportClaim
  projectionProfile: ProjectionProfile
  transcript: BackboneTurnResult["transcript"]
  events: BackboneTurnResult["events"]
  runContextId: string
  providerTurn?: BackboneTurnResult["providerTurn"]
  driverTurn?: BackboneTurnResult["driverTurn"]
  unsupportedCase?: BackboneTurnResult["unsupportedCase"]
}): BackboneTurnResult {
  return {
    supportClaim: input.supportClaim,
    projectionProfile: input.projectionProfile,
    transcript: input.transcript,
    events: input.events,
    runContextId: input.runContextId,
    providerTurn: input.providerTurn,
    driverTurn: input.driverTurn,
    unsupportedCase: input.unsupportedCase,
  }
}
