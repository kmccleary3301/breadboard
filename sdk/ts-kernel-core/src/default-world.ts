import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
} from "@breadboard/kernel-contracts"
import {
  createExecutionWorld,
  type ExecutionWorldV1,
  type TerminalSessionDriverV1,
} from "@breadboard/execution-drivers"
import { makeTrustedLocalExecutionDriver, type LocalCommandExecutor } from "@breadboard/execution-driver-local"
import {
  makeConfiguredOciExecutionDriver,
  type OciCommandExecutor,
  type OciTerminalSessionAdapter,
} from "@breadboard/execution-driver-oci"
import {
  makeRemoteExecutionDriver,
  type RemoteExecutionHttpOptions,
  type RemoteSandboxExecutor,
} from "@breadboard/execution-driver-remote"

export interface KernelExecutionWorldOptions {
  readonly executeSandbox?: (
    request: SandboxRequestV1,
    context: {
      capability: ExecutionCapabilityV1
      placement: ExecutionPlacementV1
      driverId: string
      signal?: AbortSignal
      deadlineAtMs?: number | null
    },
  ) => Promise<SandboxResultV1>
  readonly localCommandExecutor?: LocalCommandExecutor
  readonly ociCommandExecutor?: OciCommandExecutor
  readonly ociRuntimeCommand?: string
  readonly ociWorkspaceMountTarget?: string
  readonly ociTerminalAdapter?: OciTerminalSessionAdapter
  readonly remoteExecutor?: RemoteSandboxExecutor
  readonly remoteHttp?: RemoteExecutionHttpOptions
  readonly defaultDeadlineMs?: number | null
  readonly terminationGraceMs?: number
}

function withSandboxOverride(
  driver: TerminalSessionDriverV1,
  executeSandbox: NonNullable<KernelExecutionWorldOptions["executeSandbox"]>,
): TerminalSessionDriverV1 {
  return {
    ...driver,
    execute(request, context) {
      const sandboxPromise = executeSandbox(request, {
        capability: context?.capability ?? driverCapability(request),
        placement: context?.placement ?? driverPlacement(request),
        driverId: driver.driverId,
        signal: context?.signal,
        deadlineAtMs: context?.deadlineAtMs ?? null,
      })
      if (!context?.signal) {
        return sandboxPromise
      }
      if (context.signal.aborted) {
        return Promise.reject(context.signal.reason ?? new Error("Execution aborted"))
      }
      return new Promise<SandboxResultV1>((resolve, reject) => {
        const onAbort = () => {
          reject(context.signal.reason ?? new Error("Execution aborted"))
        }
        context.signal.addEventListener("abort", onAbort, { once: true })
        sandboxPromise
          .then(resolve, reject)
          .finally(() => {
            context.signal.removeEventListener("abort", onAbort)
          })
      })
    },
    terminate(request, context) {
      driver.terminate?.(request, context)
    },
  }
}

function driverCapability(request: SandboxRequestV1): ExecutionCapabilityV1 {
  return {
    schema_version: "bb.execution_capability.v1",
    capability_id: request.capability_id ?? "unknown",
    security_tier: "trusted_dev",
    isolation_class:
      request.placement_class === "local_process"
        ? "process"
        : request.placement_class === "local_oci"
          ? "oci"
          : "remote_service",
    secret_mode: "ref_only",
    evidence_mode: request.evidence_mode,
  }
}

function driverPlacement(request: SandboxRequestV1): ExecutionPlacementV1 {
  return {
    schema_version: "bb.execution_placement.v1",
    placement_id: request.request_id,
    placement_class: (request.placement_class as ExecutionPlacementV1["placement_class"]) ?? "local_process",
    runtime_id: request.metadata?.driver === "oci" ? "oci" : "execution-world",
    capability_id: request.capability_id ?? "unknown",
  }
}

export function createKernelExecutionWorld(options: KernelExecutionWorldOptions = {}): ExecutionWorldV1 {
  const localDriver = makeTrustedLocalExecutionDriver(options.localCommandExecutor)
  const ociDriver = makeConfiguredOciExecutionDriver({
    commandExecutor: options.ociCommandExecutor,
    runtimeCommand: options.ociRuntimeCommand,
    workspaceMountTarget: options.ociWorkspaceMountTarget,
    terminalAdapter: options.ociTerminalAdapter,
  })
  const remoteDriver = makeRemoteExecutionDriver(options.remoteExecutor, options.remoteHttp)
  const baseDrivers = [localDriver, ociDriver, remoteDriver]
  const drivers = options.executeSandbox
    ? baseDrivers.map((driver) => withSandboxOverride(driver, options.executeSandbox!))
    : baseDrivers
  return createExecutionWorld({
    drivers,
    defaultDeadlineMs: options.defaultDeadlineMs,
    terminationGraceMs: options.terminationGraceMs,
  })
}
