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

export interface ExecutionDriverSideEffectExpectationV1 {
  filesystem_scope: "none" | "workspace_scoped" | "container_scoped" | "remote_scoped"
  network_scope: "disabled" | "restricted" | "backend_policy"
  process_scope: "none" | "local_process" | "sandbox_process" | "remote_process"
  persistence_scope: "none" | "workspace_artifacts" | "backend_artifacts"
}

export interface ExecutionDriverEvidenceExpectationV1 {
  require_stdout_ref: boolean
  require_stderr_ref: boolean
  require_artifact_refs: boolean
  require_side_effect_digest: boolean
  require_evidence_refs: boolean
  require_usage_metadata: boolean
}

export interface PlannedExecutionV1 {
  driver: ExecutionDriverV1
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  sandboxRequest: SandboxRequestV1 | null
  sideEffectExpectation: ExecutionDriverSideEffectExpectationV1
  evidenceExpectation: ExecutionDriverEvidenceExpectationV1
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

export function buildExecutionDriverSideEffectExpectation(
  placementClass: ExecutionPlacementV1["placement_class"],
): ExecutionDriverSideEffectExpectationV1 {
  const mapping: Record<ExecutionPlacementV1["placement_class"], ExecutionDriverSideEffectExpectationV1> = {
    inline_ts: {
      filesystem_scope: "none",
      network_scope: "disabled",
      process_scope: "none",
      persistence_scope: "none",
    },
    local_process: {
      filesystem_scope: "workspace_scoped",
      network_scope: "restricted",
      process_scope: "local_process",
      persistence_scope: "workspace_artifacts",
    },
    local_oci: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_oci_gvisor: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_oci_kata: {
      filesystem_scope: "container_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    local_microvm: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "sandbox_process",
      persistence_scope: "backend_artifacts",
    },
    remote_worker: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_python: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_oci: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
    delegated_microvm: {
      filesystem_scope: "remote_scoped",
      network_scope: "backend_policy",
      process_scope: "remote_process",
      persistence_scope: "backend_artifacts",
    },
  }
  return mapping[placementClass]
}

export function buildExecutionDriverEvidenceExpectation(input: {
  capability: ExecutionCapabilityV1
  placementClass: ExecutionPlacementV1["placement_class"]
}): ExecutionDriverEvidenceExpectationV1 {
  const requireStrictEvidence = input.capability.evidence_mode === "replay_strict"
  const requireAuditedEvidence = input.capability.evidence_mode === "audit_full"
  const requiresSandboxRefs = input.placementClass !== "inline_ts"
  return {
    require_stdout_ref: requiresSandboxRefs,
    require_stderr_ref: requiresSandboxRefs,
    require_artifact_refs: requiresSandboxRefs,
    require_side_effect_digest: requiresSandboxRefs || requireStrictEvidence,
    require_evidence_refs: requireStrictEvidence || requireAuditedEvidence,
    require_usage_metadata: input.capability.evidence_mode !== "minimal",
  }
}

export function selectExecutionDriver(input: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  drivers: ExecutionDriverV1[]
}): ExecutionDriverV1 | null {
  return (
    input.drivers.find((driver) => {
      if (!driver.supportedPlacements.includes(input.placement.placement_class)) {
        return false
      }
      return driver.supportsCapability(input.capability, input.placement.placement_class)
    }) ?? null
  )
}

export function buildPlannedExecution(input: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  drivers: ExecutionDriverV1[]
  requestId: string
  command?: string[] | null
  workspaceRef?: string | null
  imageRef?: string | null
  metadata?: Record<string, unknown>
}): PlannedExecutionV1 | null {
  const driver = selectExecutionDriver({
    capability: input.capability,
    placement: input.placement,
    drivers: input.drivers,
  })
  if (!driver) {
    return null
  }
  const sideEffectExpectation = buildExecutionDriverSideEffectExpectation(input.placement.placement_class)
  const evidenceExpectation = buildExecutionDriverEvidenceExpectation({
    capability: input.capability,
    placementClass: input.placement.placement_class,
  })
  const sandboxRequest =
    driver.buildSandboxRequest && input.command
      ? driver.buildSandboxRequest({
          requestId: input.requestId,
          capability: input.capability,
          placement: input.placement,
          command: input.command,
          workspaceRef: input.workspaceRef ?? null,
          imageRef: input.imageRef ?? null,
          metadata: input.metadata ?? {},
        })
      : null
  return {
    driver,
    capability: input.capability,
    placement: input.placement,
    sandboxRequest,
    sideEffectExpectation,
    evidenceExpectation,
  }
}
