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
      terminationGraceMs?: number
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
  const activeOverrides = new Map<string, { promise: Promise<unknown>; controller: AbortController }>()
  return {
    ...driver,
    execute(request, context) {
      if (context?.signal?.aborted) {
        const isCancelled =
          context.signal.reason === "cancelled" ||
          (typeof context.signal.reason?.message === "string" &&
            context.signal.reason.message.toLowerCase().includes("cancel"))
        const status: SandboxResultV1["status"] = isCancelled ? "cancelled" : "timed_out"
        return Promise.resolve({
          schema_version: "bb.sandbox_result.v1",
          request_id: request.request_id,
          status,
          placement_id: `${driver.driverId}:${request.request_id}`,
          stdout_ref: null,
          stderr_ref: null,
          artifact_refs: [],
          side_effect_digest: null,
          error: {
            message: isCancelled ? "Execution was cancelled" : "Execution timed out",
            reason: isCancelled ? "execution_cancelled" : "execution_timed_out",
          },
        })
      }
      const controller = new AbortController()
      const forwardAbort = () => {
        if (!controller.signal.aborted) {
          controller.abort(context?.signal?.reason)
        }
      }
      if (context?.signal) {
        context.signal.addEventListener("abort", forwardAbort, { once: true })
      }
      const sandboxPromise = Promise.resolve().then(() =>
        executeSandbox(request, {
          capability: context?.capability ?? driverCapability(request),
          placement: context?.placement ?? driverPlacement(request),
          driverId: driver.driverId,
          signal: controller.signal,
          deadlineAtMs: context?.deadlineAtMs ?? null,
          terminationGraceMs: context?.terminationGraceMs ?? 2000,
        }),
      )

      activeOverrides.set(request.request_id, { promise: sandboxPromise, controller })
      const cleanup = () => {
        activeOverrides.delete(request.request_id)
        context?.signal?.removeEventListener("abort", forwardAbort)
      }

      return sandboxPromise.finally(cleanup)
    },
    async terminate(request, context) {
      const active = activeOverrides.get(request.request_id)
      if (!active) {
        if (driver.terminate) {
          await driver.terminate(request, context)
        }
        return
      }
      const reason = context?.signal?.reason ?? new Error(`Execution ${context?.reason ?? "cancelled"}`)
      if (!active.controller.signal.aborted) {
        active.controller.abort(reason)
      }
      await active.promise.catch(() => {})
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
