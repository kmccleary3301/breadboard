import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
} from "@breadboard/kernel-contracts"
import type { ExecutionDriverV1 } from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"

export function buildLocalProcessSandboxRequest(input: {
  requestId: string
  capability: ExecutionCapabilityV1
  command: string[]
  workspaceRef?: string | null
}): SandboxRequestV1 {
  return {
    schema_version: "bb.sandbox_request.v1",
    request_id: input.requestId,
    capability_id: input.capability.capability_id,
    placement_class: "local_process",
    workspace_ref: input.workspaceRef ?? null,
    rootfs_ref: null,
    image_ref: null,
    snapshot_ref: null,
    command: input.command,
    network_policy: { allow: input.capability.allow_net_hosts ?? [] },
    secret_refs: [],
    timeout_seconds: null,
    evidence_mode: input.capability.evidence_mode,
    metadata: { driver: "local-process" },
  }
}

export const trustedLocalExecutionDriver: ExecutionDriverV1 = {
  driverId: "local-process",
  supportedPlacements: ["inline_ts", "local_process"],
  supportsCapability(capability, placementClass) {
    if (placementClass === "inline_ts") return capability.isolation_class === "none"
    if (placementClass === "local_process") return isPlacementCompatible(capability, placementClass)
    return false
  },
  buildSandboxRequest({ requestId, capability, command, workspaceRef }) {
    if (command.length === 0) {
      throw new Error("trustedLocalExecutionDriver requires a non-empty command for local_process placement")
    }
    return buildLocalProcessSandboxRequest({
      requestId,
      capability,
      command,
      workspaceRef,
    })
  },
}

export function chooseTrustedLocalPlacement(
  capability: ExecutionCapabilityV1,
): ExecutionPlacementV1["placement_class"] {
  return capability.isolation_class === "none" ? "inline_ts" : "local_process"
}
