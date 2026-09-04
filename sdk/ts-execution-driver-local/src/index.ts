import { spawn } from "node:child_process"

import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
} from "@breadboard/kernel-contracts"
import type { TerminalSessionDriverV1 } from "@breadboard/execution-drivers"
import {
  buildAndPersistCanonicalSandboxEvidence,
  assertExecutionProgramAllowed,
  canonicalSandboxArtifactRoot,
  isPlacementCompatible,
} from "@breadboard/execution-drivers"
import { LocalTerminalSessionManager, trustedLocalTerminalSessionDriver } from "./terminals.js"

export function buildLocalProcessSandboxRequest(input: {
  requestId: string
  capability: ExecutionCapabilityV1
  command: string[]
  workspaceRef?: string | null
}): SandboxRequestV1 {
  assertExecutionProgramAllowed(input.capability, input.command)
  return {
    schema_version: "bb.sandbox_request.v1",
    request_id: input.requestId,
    capability_id: input.capability.capability_id,
    placement_class: "local_process",
    workspace_ref: input.workspaceRef ?? null,
    rootfs_ref: null,
    image_ref: null,
    snapshot_ref: null,
    command: [input.command[0] ?? "", ...input.command.slice(1)],
    network_policy: input.capability.allow_net_hosts === undefined
      ? null
      : { allow: input.capability.allow_net_hosts },
    secret_refs: [],
    timeout_seconds: null,
    evidence_mode: input.capability.evidence_mode,
    metadata: { driver: "local-process" },
  }
}

export interface LocalCommandExecutionResult {
  exitCode: number
  stdout: string
  stderr: string
}

export type LocalCommandExecutor = (input: {
  command: string[]
  cwd?: string | null
  signal?: AbortSignal
}) => Promise<LocalCommandExecutionResult>

export function defaultLocalCommandExecutor(input: {
  command: string[]
  cwd?: string | null
  signal?: AbortSignal
}): Promise<LocalCommandExecutionResult> {
  return new Promise((resolve, reject) => {
    let killed = false
    const isPosix = process.platform !== "win32"
    const child = spawn(input.command[0]!, input.command.slice(1), {
      cwd: input.cwd ?? undefined,
      stdio: ["ignore", "pipe", "pipe"],
      detached: isPosix,
    })
    const stdoutChunks: Buffer[] = []
    const stderrChunks: Buffer[] = []
    child.stdout?.on("data", (chunk: Buffer) => {
      stdoutChunks.push(Buffer.from(chunk))
    })
    child.stderr?.on("data", (chunk: Buffer) => {
      stderrChunks.push(Buffer.from(chunk))
    })
    let abortListener: (() => void) | null = null
    const removeAbortListener = () => {
      if (input.signal && abortListener) {
        input.signal.removeEventListener("abort", abortListener)
        abortListener = null
      }
    }
    child.on("error", (err) => {
      removeAbortListener()
      if (!killed) reject(err)
    })
    child.on("close", (exitCode) => {
      removeAbortListener()
      resolve({
        exitCode: exitCode ?? 1,
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
      })
    })

    const killProcessGroup = (sig: NodeJS.Signals) => {
      if (child.pid == null) return
      if (isPosix) {
        try {
          process.kill(-child.pid, sig)
        } catch {
          try {
            child.kill(sig)
          } catch {}
        }
      } else {
        try {
          child.kill(sig)
        } catch {}
      }
    }

    if (input.signal) {
      const onAbort = () => {
        killed = true
        killProcessGroup("SIGTERM")
        const escalateTimer = setTimeout(() => {
          killProcessGroup("SIGKILL")
        }, 500)
        child.once("close", () => {
          clearTimeout(escalateTimer)
        })
      }
      if (input.signal.aborted) {
        onAbort()
      } else {
        abortListener = onAbort
        input.signal.addEventListener("abort", onAbort, { once: true })
      }
    }
  })
}

function isDeadlineAbort(signal?: AbortSignal): boolean {
  if (!signal?.aborted) return false
  const reason = signal.reason
  return (
    reason === "deadline" ||
    (reason instanceof Error &&
      (reason.name === "TimeoutError" || reason.message.toLowerCase().includes("deadline")))
  )
}
export async function executeLocalProcessSandboxRequest(
  request: SandboxRequestV1,
  options: {
    commandExecutor?: LocalCommandExecutor
    tempDirRoot?: string
    signal?: AbortSignal
  } = {},
): Promise<SandboxResultV1> {
  if (request.network_policy !== null && request.network_policy !== undefined) {
    throw new Error(
      "trusted local process cannot enforce the requested network policy",
    )
  }
  const executeCommand = options.commandExecutor ?? defaultLocalCommandExecutor
  const cwd = request.workspace_ref && request.workspace_ref.startsWith("/") ? request.workspace_ref : null
  const preAborted = options.signal?.aborted === true
  const deadlineBeforeExecution = isDeadlineAbort(options.signal)
  const result = preAborted
    ? {
        exitCode: deadlineBeforeExecution ? 124 : 130,
        stdout: "",
        stderr: "",
      }
    : await executeCommand({
        command: request.command,
        cwd,
        signal: options.signal,
      })
  const isAborted = preAborted || options.signal?.aborted === true
  const isDeadline = deadlineBeforeExecution || isDeadlineAbort(options.signal)
  const status: SandboxResultV1["status"] = isAborted
    ? isDeadline
      ? "timed_out"
      : "cancelled"
    : result.exitCode === 0
      ? "completed"
      : "failed"
  const errorReason = status === "timed_out"
    ? "execution_timed_out"
    : status === "cancelled"
      ? "execution_cancelled"
      : "execution_failed"
  const artifactRoot = options.tempDirRoot === undefined
    ? undefined
    : canonicalSandboxArtifactRoot(options.tempDirRoot)
  const evidence = await buildAndPersistCanonicalSandboxEvidence({
    command: request.command,
    status,
    exitCode: result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    evidenceMode: request.evidence_mode,
  }, artifactRoot)
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status,
    ...evidence,
    error: status === "completed" ? null : { reason: errorReason },
  }
}

export function makeTrustedLocalExecutionDriver(commandExecutor?: LocalCommandExecutor): TerminalSessionDriverV1 {
  const terminalDriver = new LocalTerminalSessionManager()
  interface ActiveLocalExecution {
    controller: AbortController
    completion: Promise<SandboxResultV1>
    cleanup: () => void
    detach: () => void
  }
  const activeExecutions = new Map<string, ActiveLocalExecution>()
  return {
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
    execute(request, context) {
      if (activeExecutions.has(request.request_id)) {
        return Promise.reject(
          new Error(`Local execution request '${request.request_id}' is already active`),
        )
      }
      const controller = new AbortController()
      const forwardAbort = () => controller.abort(context?.signal.reason)
      if (context?.signal) {
        if (context.signal.aborted) {
          forwardAbort()
        } else {
          context.signal.addEventListener("abort", forwardAbort, { once: true })
        }
      }
      const executionPromise = executeLocalProcessSandboxRequest(request, {
        commandExecutor,
        signal: controller.signal,
      })
      const detach = () => context?.signal.removeEventListener("abort", forwardAbort)
      const cleanup = () => {
        if (activeExecutions.get(request.request_id)?.controller === controller) {
          activeExecutions.delete(request.request_id)
        }
        detach()
      }
      activeExecutions.set(request.request_id, {
        controller,
        completion: executionPromise,
        cleanup,
        detach,
      })
      return executionPromise.then((result) => {
        cleanup()
        return result
      })
    },
    async terminate(request, context) {
      const active = activeExecutions.get(request.request_id)
      if (!active) return
      active.controller.abort(context.reason)
      active.detach()
      let timer: ReturnType<typeof setTimeout> | undefined
      const timeout = new Promise<false>((resolve) => {
        timer = setTimeout(() => resolve(false), 2000)
      })
      try {
        const terminationObserved = await Promise.race([
          active.completion.then(
            () => true,
            () => true,
          ),
          timeout,
        ])
        if (!terminationObserved) {
          throw new Error("Local process termination was not observed within 2000ms")
        }
        active.cleanup()
      } finally {
        clearTimeout(timer)
      }
    },
    supportsTerminalSessions(capability, placementClass) {
      return terminalDriver.supportsTerminalSessions(capability, placementClass)
    },
    startTerminalSession: terminalDriver.startSession.bind(terminalDriver),
    interactTerminalSession: terminalDriver.interactSession.bind(terminalDriver),
    snapshotTerminalRegistry: terminalDriver.snapshotRegistry.bind(terminalDriver),
    cleanupTerminalSessions: terminalDriver.cleanupSessions.bind(terminalDriver),
  }
}

export const trustedLocalExecutionDriver: TerminalSessionDriverV1 = makeTrustedLocalExecutionDriver()

export function chooseTrustedLocalPlacement(
  capability: ExecutionCapabilityV1,
): ExecutionPlacementV1["placement_class"] {
  return capability.isolation_class === "none" ? "inline_ts" : "local_process"
}

export * from "./terminals.js"
