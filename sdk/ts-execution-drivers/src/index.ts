import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"

export interface ExecutionDriverV1 {
  driverId: string
  supportedPlacements: ExecutionPlacementV1["placement_class"][]
  supportsCapability(capability: ExecutionCapabilityV1, placementClass: ExecutionPlacementV1["placement_class"]): boolean
  buildSandboxRequest?(input: {
    requestId: string
    capability: ExecutionCapabilityV1
    placement: ExecutionPlacementV1
    command: string[]
    workspaceRef?: string | null
    imageRef?: string | null
    metadata?: Record<string, unknown>
  }): SandboxRequestV1
  execute?(request: SandboxRequestV1): Promise<SandboxResultV1>
}

export function isPlacementCompatible(
  capability: ExecutionCapabilityV1,
  placementClass: ExecutionPlacementV1["placement_class"],
): boolean {
  const allowedByIsolation: Record<ExecutionCapabilityV1["isolation_class"], ExecutionPlacementV1["placement_class"][]> = {
    none: ["inline_ts"],
    process: ["local_process"],
    oci: ["local_oci"],
    gvisor: ["local_oci_gvisor"],
    kata: ["local_oci_kata"],
    microvm: ["local_microvm"],
    remote_service: ["remote_worker", "delegated_python", "delegated_oci", "delegated_microvm"],
  }
  return allowedByIsolation[capability.isolation_class].includes(placementClass)
}

export function buildExecutionDriverUnsupportedCase(input: {
  capability: ExecutionCapabilityV1
  placementClass: ExecutionPlacementV1["placement_class"]
  fallbackAllowed?: boolean
  fallbackTaken?: boolean
  summary?: string
  metadata?: Record<string, unknown>
}): UnsupportedCaseV1 {
  return {
    schema_version: "bb.unsupported_case.v1",
    reason_code: "unsupported_execution_driver",
    summary:
      input.summary ??
      `No execution driver supports ${input.placementClass} for isolation ${input.capability.isolation_class}`,
    contract_family: "bb.execution_placement.v1",
    fallback_allowed: input.fallbackAllowed ?? false,
    fallback_taken: input.fallbackTaken ?? false,
    required_capability_id: input.capability.capability_id,
    unavailable_placement: input.placementClass,
    evidence_refs: [],
    metadata: input.metadata ?? {},
  }
}
