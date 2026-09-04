import {
  assertValid,
  type ExecutionPlacementV1,
  type SandboxRequestV1,
  type SandboxResultV1,
} from "@breadboard/kernel-contracts"
import type {
  ExecutionDriverExecutionContextV1,
  ExecutionDriverTerminationContextV1,
  ExecutionDriverV1,
} from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"
import { buildRemoteSandboxRequest } from "./index.js"

export type ScheduledExecutionStateV1 =
  | "accepted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out"

export interface ScheduledExecutionHandleV1 {
  readonly executionId: string
  readonly evidenceRefs?: readonly string[]
}

export interface ScheduledExecutionObservationV1 {
  readonly state: ScheduledExecutionStateV1
  readonly result?: SandboxResultV1
  readonly evidenceRefs?: readonly string[]
}

export interface ScheduledExecutionBackendV1 {
  readonly backendId: string
  submit(
    request: SandboxRequestV1,
    context: ExecutionDriverExecutionContextV1,
  ): Promise<ScheduledExecutionHandleV1>
  observe(executionId: string): Promise<ScheduledExecutionObservationV1>
  cancel(
    executionId: string,
    context: ExecutionDriverTerminationContextV1,
  ): Promise<void>
}

export interface ScheduledExecutionDriverOptionsV1 {
  readonly driverId: string
  readonly placementClass: ExecutionPlacementV1["placement_class"]
  readonly pollIntervalMs?: number
  readonly cancellationObservationTimeoutMs?: number
}

interface ActiveScheduledExecution {
  readonly handle: Promise<ScheduledExecutionHandleV1>
}

const TERMINAL_STATES = new Set<ScheduledExecutionStateV1>([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
])

function requireNonempty(value: string, name: string): string {
  if (!value.trim()) throw new Error(`${name} must be non-empty`)
  return value
}

function evidenceRefs(...groups: (readonly string[] | undefined)[]): string[] {
  const refs = groups.flatMap((group) => group ?? [])
  if (refs.some((ref) => typeof ref !== "string" || !ref.trim())) {
    throw new Error("scheduled execution evidence refs must be non-empty strings")
  }
  return [...new Set(refs)].sort()
}

function waitForPoll(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(signal.reason)
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, milliseconds)
    function done(): void {
      signal.removeEventListener("abort", aborted)
      resolve()
    }
    function aborted(): void {
      clearTimeout(timer)
      reject(signal.reason)
    }
    signal.addEventListener("abort", aborted, { once: true })
  })
}

function terminalResult(
  request: SandboxRequestV1,
  observation: ScheduledExecutionObservationV1,
  refs: string[],
): SandboxResultV1 {
  const expectedStatus = observation.state === "timed_out"
    ? "timed_out"
    : observation.state === "cancelled"
      ? "cancelled"
      : observation.state === "completed"
        ? "completed"
        : "failed"
  if (observation.result) {
    const result = assertValid<SandboxResultV1>("sandboxResult", observation.result)
    if (result.request_id !== request.request_id) {
      throw new Error("scheduled execution result request_id does not match request")
    }
    if (result.status !== expectedStatus) {
      throw new Error("scheduled execution result status does not match terminal observation")
    }
    return {
      ...result,
      evidence_refs: evidenceRefs(result.evidence_refs, refs),
    }
  }
  if (observation.state === "completed") {
    throw new Error("completed scheduled execution has no sandbox result")
  }
  return assertValid<SandboxResultV1>("sandboxResult", {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status: expectedStatus,
    evidence_refs: refs,
    error: {
      reason: expectedStatus === "failed"
        ? "scheduled_execution_failed"
        : `execution_${expectedStatus}`,
    },
  })
}

export function makeScheduledExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: ScheduledExecutionDriverOptionsV1,
): ExecutionDriverV1 {
  requireNonempty(backend.backendId, "backendId")
  const driverId = requireNonempty(options.driverId, "driverId")
  const pollIntervalMs = Math.max(1, options.pollIntervalMs ?? 250)
  const cancellationObservationTimeoutMs = Math.max(
    pollIntervalMs,
    options.cancellationObservationTimeoutMs ?? 2_000,
  )
  const active = new Map<string, ActiveScheduledExecution>()

  return {
    driverId,
    supportedPlacements: [options.placementClass],
    supportsCapability(capability, placementClass) {
      return placementClass === options.placementClass && isPlacementCompatible(capability, placementClass)
    },
    buildSandboxRequest({ requestId, capability, command, workspaceRef, imageRef, placement, metadata }) {
      if (command.length === 0) {
        throw new Error(`${driverId} execution driver requires a non-empty command`)
      }
      const request = buildRemoteSandboxRequest({
        requestId,
        capability,
        command,
        workspaceRef,
        imageRef,
        placementClass: placement.placement_class,
        metadata,
      })
      return {
        ...request,
        metadata: {
          ...(request.metadata ?? {}),
          driver: driverId,
        },
      }
    },
    async execute(request, context) {
      if (!context) throw new Error(`${driverId} execution requires a lifecycle context`)
      const handlePromise = Promise.resolve(backend.submit(request, context)).then((handle) => ({
        executionId: requireNonempty(handle.executionId, "executionId"),
        evidenceRefs: evidenceRefs(handle.evidenceRefs),
      }))
      const entry = { handle: handlePromise }
      active.set(request.request_id, entry)
      try {
        const handle = await handlePromise
        while (true) {
          const observation = await backend.observe(handle.executionId)
          if (TERMINAL_STATES.has(observation.state)) {
            return terminalResult(
              request,
              observation,
              evidenceRefs(handle.evidenceRefs, observation.evidenceRefs),
            )
          }
          await waitForPoll(pollIntervalMs, context.signal)
        }
      } finally {
        if (active.get(request.request_id) === entry) active.delete(request.request_id)
      }
    },
    async terminate(request, context) {
      const entry = active.get(request.request_id)
      if (!entry) return
      const handle = await entry.handle
      await backend.cancel(handle.executionId, context)
      const deadline = Date.now() + cancellationObservationTimeoutMs
      while (true) {
        const observation = await backend.observe(handle.executionId)
        if (TERMINAL_STATES.has(observation.state)) return
        if (Date.now() >= deadline) {
          throw new Error(`${driverId} cancellation was not observed before the cleanup deadline`)
        }
        await new Promise((resolve) => setTimeout(resolve, pollIntervalMs))
      }
    },
  }
}

export function makeRayExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: Omit<ScheduledExecutionDriverOptionsV1, "driverId" | "placementClass"> = {},
): ExecutionDriverV1 {
  return makeScheduledExecutionDriver(backend, {
    ...options,
    driverId: "ray",
    placementClass: "delegated_python",
  })
}

export function makeSlurmExecutionDriver(
  backend: ScheduledExecutionBackendV1,
  options: Omit<ScheduledExecutionDriverOptionsV1, "driverId" | "placementClass"> = {},
): ExecutionDriverV1 {
  return makeScheduledExecutionDriver(backend, {
    ...options,
    driverId: "slurm",
    placementClass: "remote_worker",
  })
}
