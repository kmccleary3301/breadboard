import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
} from "@breadboard/kernel-contracts"
import { assertValid } from "@breadboard/kernel-contracts"
import type {
  ExecutionDriverExecutionContextV1,
  ExecutionDriverTerminationContextV1,
  ExecutionDriverV1,
  TerminalSessionDriverV1,
} from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"
import {
  makeRemoteTerminalSessionDriver,
  type RemoteTerminalExecutionHttpOptions,
} from "./terminals.js"

export interface RemoteSandboxExecutor {
  (
    request: SandboxRequestV1,
    context?: ExecutionDriverExecutionContextV1,
  ): Promise<SandboxResultV1>
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
  timeoutMs?: number
  signal?: AbortSignal
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
    command: [input.command[0] ?? "", ...input.command.slice(1)],
    network_policy: input.capability.allow_net_hosts === undefined
      ? null
      : { allow: input.capability.allow_net_hosts },
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

export function buildRemoteExecutionResponseEnvelope(input: {
  result: SandboxResultV1
  metadata?: Record<string, unknown>
}): RemoteExecutionResponseEnvelopeV1 {
  return {
    schema_version: "bb.remote_execution_response.v1",
    result: assertValid<SandboxResultV1>("sandboxResult", input.result),
    metadata: input.metadata ?? {},
  }
}

export function buildRemoteExecutionErrorSummary(payload: unknown, statusCode: number): string {
  if (typeof payload === "object" && payload !== null) {
    const objectPayload = payload as {
      metadata?: { summary?: unknown }
      error?: { message?: unknown }
      message?: unknown
    }
    if (typeof objectPayload.metadata?.summary === "string") {
      return objectPayload.metadata.summary
    }
    if (typeof objectPayload.error?.message === "string") {
      return objectPayload.error.message
    }
    if (typeof objectPayload.message === "string") {
      return objectPayload.message
    }
  }
  return `Remote execution endpoint returned HTTP ${statusCode}`
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
  const internalController = new AbortController()
  const forwardAbort = () => {
    if (!internalController.signal.aborted) {
      internalController.abort(options.signal?.reason)
    }
  }
  if (options.signal) {
    if (options.signal.aborted) {
      forwardAbort()
    } else {
      options.signal.addEventListener("abort", forwardAbort, { once: true })
    }
  }
  let timeoutHandle: NodeJS.Timeout | undefined
  const timeoutPromise =
    options.timeoutMs != null
      ? new Promise<never>((_, reject) => {
          timeoutHandle = setTimeout(() => {
            if (!internalController.signal.aborted) {
              internalController.abort(new Error(`Remote execution timed out after ${options.timeoutMs}ms`))
            }
            const err = new Error(`Remote execution timed out after ${options.timeoutMs}ms`)
            err.name = "TimeoutError"
            reject(err)
          }, options.timeoutMs)
        })
      : null
  try {
    const responseAndPayloadPromise = Promise.resolve(
      fetchImpl(options.endpointUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(options.headers ?? {}),
        },
        body: JSON.stringify(envelope),
        signal: internalController.signal,
      }),
    ).then(async (response) => ({
      response,
      payload: (await response.json()) as RemoteExecutionResponseEnvelopeV1 | SandboxResultV1,
    }))
    const { response, payload } = timeoutPromise
      ? await Promise.race([responseAndPayloadPromise, timeoutPromise])
      : await responseAndPayloadPromise
    if (!response.ok) {
      throw new Error(buildRemoteExecutionErrorSummary(payload, response.status))
    }
    if ((payload as RemoteExecutionResponseEnvelopeV1).schema_version === "bb.remote_execution_response.v1") {
      return assertValid<SandboxResultV1>("sandboxResult", (payload as RemoteExecutionResponseEnvelopeV1).result)
    }
    return assertValid<SandboxResultV1>("sandboxResult", payload as SandboxResultV1)
  } catch (error) {
    if (options.signal?.aborted && options.signal.reason) {
      throw options.signal.reason instanceof Error ? options.signal.reason : new Error(String(options.signal.reason))
    }
    if (
      (error instanceof Error && error.name === "AbortError") ||
      (error instanceof Error && error.message.includes("timed out")) ||
      (typeof internalController.signal.reason === "string" && internalController.signal.reason.includes("timed out")) ||
      (internalController.signal.reason instanceof Error && internalController.signal.reason.message.includes("timed out"))
    ) {
      const err = new Error(`Remote execution timed out after ${options.timeoutMs ?? "unknown"}ms`)
      err.name = "TimeoutError"
      throw err
    }
    throw error
  } finally {
    clearTimeout(timeoutHandle)
    if (options.signal) {
      options.signal.removeEventListener("abort", forwardAbort)
    }
  }
}

export function makeRemoteExecutionDriver(
  executor?: RemoteSandboxExecutor,
  httpOptions?: RemoteExecutionHttpOptions,
): TerminalSessionDriverV1 {
  const terminalDriver = httpOptions ? makeRemoteTerminalSessionDriver(httpOptions) : null
  interface ActiveRemoteExecution {
    controller: AbortController
    completion: Promise<unknown>
  }
  const activeExecutions = new Map<string, ActiveRemoteExecution>()
  return {
    driverId: "remote",
    supportedPlacements: ["remote_worker", "delegated_python", "delegated_oci", "delegated_microvm"],
    supportsCapability(capability, placementClass) {
      if (!executor && !httpOptions) {
        return false
      }
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
    execute(request, context) {
      const controller = new AbortController()
      const forwardContextAbort = () => {
        if (!controller.signal.aborted) {
          controller.abort(context?.signal?.reason)
        }
      }
      const forwardHttpAbort = () => {
        if (!controller.signal.aborted) {
          controller.abort(httpOptions?.signal?.reason)
        }
      }
      if (context?.signal) {
        if (context.signal.aborted) {
          forwardContextAbort()
        } else {
          context.signal.addEventListener("abort", forwardContextAbort, { once: true })
        }
      }
      if (httpOptions?.signal) {
        if (httpOptions.signal.aborted) {
          forwardHttpAbort()
        } else {
          httpOptions.signal.addEventListener("abort", forwardHttpAbort, { once: true })
        }
      }
      const cleanup = () => {
        activeExecutions.delete(request.request_id)
        context?.signal?.removeEventListener("abort", forwardContextAbort)
        httpOptions?.signal?.removeEventListener("abort", forwardHttpAbort)
      }
      let executionPromise: Promise<SandboxResultV1>
      if (executor) {
        const executionContext: ExecutionDriverExecutionContextV1 = {
          signal: controller.signal,
          deadlineAtMs: context?.deadlineAtMs ?? null,
          terminationGraceMs: context?.terminationGraceMs ?? 2000,
        }
        try {
          executionPromise = Promise.resolve(executor(request, executionContext))
        } catch (error) {
          cleanup()
          return Promise.reject(error)
        }
      } else if (!httpOptions) {
        cleanup()
        return Promise.reject(new Error("remote driver has no configured executor or http endpoint"))
      } else {
        const deadlineTimeoutMs =
          context?.deadlineAtMs != null ? Math.max(0, context.deadlineAtMs - Date.now()) : undefined
        const effectiveTimeoutMs =
          deadlineTimeoutMs != null && httpOptions.timeoutMs != null
            ? Math.min(deadlineTimeoutMs, httpOptions.timeoutMs)
            : deadlineTimeoutMs ?? httpOptions.timeoutMs
        executionPromise = executeRemoteSandboxRequest(request, {
          ...httpOptions,
          signal: controller.signal,
          timeoutMs: effectiveTimeoutMs,
        })
      }
      activeExecutions.set(request.request_id, {
        controller,
        completion: executionPromise,
      })
      return executionPromise.finally(cleanup)
    },
    async terminate(request, context) {
      const active = activeExecutions.get(request.request_id)
      if (!active) return
      if (!active.controller.signal.aborted) {
        const reason =
          context?.signal?.reason ??
          new Error(`Remote execution ${context?.reason ?? "cancelled"} termination requested`)
        active.controller.abort(reason)
      }
      await active.completion.catch(() => {})
    },
    ...(terminalDriver
      ? {
          supportsTerminalSessions: terminalDriver.supportsTerminalSessions,
          startTerminalSession: terminalDriver.startTerminalSession,
          interactTerminalSession: terminalDriver.interactTerminalSession,
          snapshotTerminalRegistry: terminalDriver.snapshotTerminalRegistry,
          cleanupTerminalSessions: terminalDriver.cleanupTerminalSessions,
        }
      : {
          supportsTerminalSessions: () => false,
        }),
  }
}

export const remoteExecutionDriver: ExecutionDriverV1 = makeRemoteExecutionDriver()

export type { RemoteTerminalExecutionHttpOptions }
export * from "./terminals.js"
export * from "./scheduled.js"
export * from "./slurm.js"
