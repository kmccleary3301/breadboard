import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
} from "@breadboard/kernel-contracts"
import { assertValid } from "@breadboard/kernel-contracts"
import type { ExecutionDriverV1 } from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"

export interface RemoteSandboxExecutor {
  (request: SandboxRequestV1): Promise<SandboxResultV1>
}

export interface RemoteExecutionRequestEnvelopeV1 {
  schema_version: "bb.remote_execution_request.v1"
  request: SandboxRequestV1
  metadata?: Record<string, unknown>
}

export interface RemoteExecutionResponseEnvelopeV1 {
  schema_version: "bb.remote_execution_response.v1"
  result: SandboxResultV1
  metadata?: Record<string, unknown>
}

export interface RemoteExecutionHttpOptions {
  endpointUrl: string
  headers?: Record<string, string>
  fetchImpl?: typeof fetch
  metadata?: Record<string, unknown>
}

export function chooseRemotePlacement(
  capability: ExecutionCapabilityV1,
): ExecutionPlacementV1["placement_class"] {
  switch (capability.isolation_class) {
    case "remote_service":
      return "remote_worker"
    case "microvm":
      return "delegated_microvm"
    case "oci":
    case "gvisor":
    case "kata":
      return "delegated_oci"
    default:
      return "delegated_python"
  }
}

export function buildRemoteSandboxRequest(input: {
  requestId: string
  capability: ExecutionCapabilityV1
  command: string[]
  workspaceRef?: string | null
  imageRef?: string | null
  placementClass?: ExecutionPlacementV1["placement_class"]
  metadata?: Record<string, unknown>
}): SandboxRequestV1 {
  const placementClass = input.placementClass ?? chooseRemotePlacement(input.capability)
  return {
    schema_version: "bb.sandbox_request.v1",
    request_id: input.requestId,
    capability_id: input.capability.capability_id,
    placement_class: placementClass,
    workspace_ref: input.workspaceRef ?? null,
    rootfs_ref: null,
    image_ref: input.imageRef ?? null,
    snapshot_ref: null,
    command: input.command,
    network_policy: { allow: input.capability.allow_net_hosts ?? [] },
    secret_refs: [],
    timeout_seconds: null,
    evidence_mode: input.capability.evidence_mode,
    metadata: {
      driver: "remote",
      ...(input.metadata ?? {}),
    },
  }
}

export function buildRemoteExecutionRequestEnvelope(input: {
  request: SandboxRequestV1
  metadata?: Record<string, unknown>
}): RemoteExecutionRequestEnvelopeV1 {
  return {
    schema_version: "bb.remote_execution_request.v1",
    request: assertValid<SandboxRequestV1>("sandboxRequest", input.request),
    metadata: input.metadata ?? {},
  }
}

export async function executeRemoteSandboxRequest(
  request: SandboxRequestV1,
  options: RemoteExecutionHttpOptions,
): Promise<SandboxResultV1> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch
  if (typeof fetchImpl !== "function") {
    throw new Error("Remote execution requires a fetch-compatible implementation")
  }
  const envelope = buildRemoteExecutionRequestEnvelope({
    request,
    metadata: options.metadata,
  })
  const response = await fetchImpl(options.endpointUrl, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(options.headers ?? {}),
    },
    body: JSON.stringify(envelope),
  })
  const payload = (await response.json()) as RemoteExecutionResponseEnvelopeV1 | SandboxResultV1
  if (!response.ok) {
    const summary =
      typeof (payload as { metadata?: { summary?: unknown } }).metadata?.summary === "string"
        ? String((payload as { metadata?: { summary?: unknown } }).metadata?.summary)
        : `Remote execution endpoint returned HTTP ${response.status}`
    throw new Error(summary)
  }
  if ((payload as RemoteExecutionResponseEnvelopeV1).schema_version === "bb.remote_execution_response.v1") {
    return assertValid<SandboxResultV1>("sandboxResult", (payload as RemoteExecutionResponseEnvelopeV1).result)
  }
  return assertValid<SandboxResultV1>("sandboxResult", payload as SandboxResultV1)
}

export function makeRemoteExecutionDriver(
  executor?: RemoteSandboxExecutor,
  httpOptions?: RemoteExecutionHttpOptions,
): ExecutionDriverV1 {
  const executeRemote =
    executor ??
    (httpOptions
      ? (request: SandboxRequestV1) =>
          executeRemoteSandboxRequest(request, httpOptions)
      : undefined)
  return {
    driverId: "remote",
    supportedPlacements: ["remote_worker", "delegated_python", "delegated_oci", "delegated_microvm"],
    supportsCapability(capability, placementClass) {
      return isPlacementCompatible(capability, placementClass)
    },
    buildSandboxRequest({ requestId, capability, command, workspaceRef, imageRef, placement, metadata }) {
      if (command.length === 0) {
        throw new Error("remote execution driver requires a non-empty command")
      }
      return buildRemoteSandboxRequest({
        requestId,
        capability,
        command,
        workspaceRef,
        imageRef,
        placementClass: placement.placement_class,
        metadata,
      })
    },
    execute: executeRemote,
  }
}

export const remoteExecutionDriver: ExecutionDriverV1 = makeRemoteExecutionDriver()
