import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
} from "@breadboard/kernel-contracts"
import type { ExecutionDriverV1 } from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"

export function chooseOciPlacement(
  capability: ExecutionCapabilityV1,
): ExecutionPlacementV1["placement_class"] {
  switch (capability.isolation_class) {
    case "gvisor":
      return "local_oci_gvisor"
    case "kata":
      return "local_oci_kata"
    default:
      return "local_oci"
  }
}

export function buildOciSandboxRequest(input: {
  requestId: string
  capability: ExecutionCapabilityV1
  command: string[]
  workspaceRef?: string | null
  imageRef: string
}): SandboxRequestV1 {
  return {
    schema_version: "bb.sandbox_request.v1",
    request_id: input.requestId,
    capability_id: input.capability.capability_id,
    placement_class: chooseOciPlacement(input.capability),
    workspace_ref: input.workspaceRef ?? null,
    rootfs_ref: null,
    image_ref: input.imageRef,
    snapshot_ref: null,
    command: input.command,
    network_policy: { allow: input.capability.allow_net_hosts ?? [] },
    secret_refs: [],
    timeout_seconds: null,
    evidence_mode: input.capability.evidence_mode,
    metadata: { driver: "oci" },
  }
}

export const ociExecutionDriver: ExecutionDriverV1 = {
  driverId: "oci",
  supportedPlacements: ["local_oci", "local_oci_gvisor", "local_oci_kata"],
  supportsCapability(capability, placementClass) {
    return isPlacementCompatible(capability, placementClass)
  },
}
