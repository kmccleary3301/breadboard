import { createHash } from "node:crypto"
import { mkdtemp, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { spawn } from "node:child_process"

import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
} from "@breadboard/kernel-contracts"
import type { TerminalSessionDriverV1 } from "@breadboard/execution-drivers"
import { isPlacementCompatible } from "@breadboard/execution-drivers"
import { LocalTerminalSessionManager, trustedLocalTerminalSessionDriver } from "./terminals.js"

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
    command: [input.command[0] ?? "", ...input.command.slice(1)],
    network_policy: { allow: input.capability.allow_net_hosts ?? [] },
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
    let stdout = ""
    let stderr = ""
    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk)
    })
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk)
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
        stdout,
        stderr,
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
function buildLocalSideEffectDigest(request: SandboxRequestV1, result: LocalCommandExecutionResult): string {
  return `sha256:${createHash("sha256")
    .update(
      JSON.stringify({
        request_id: request.request_id,
        placement_class: request.placement_class,
        command: request.command,
        workspace_ref: request.workspace_ref,
        exit_code: result.exitCode,
      }),
    )
    .digest("hex")}`
}
export async function executeLocalProcessSandboxRequest(
  request: SandboxRequestV1,
  options: {
    commandExecutor?: LocalCommandExecutor
    tempDirRoot?: string
    signal?: AbortSignal
  } = {},
): Promise<SandboxResultV1> {
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
  const captureDir = await mkdtemp(join(options.tempDirRoot ?? tmpdir(), "breadboard-local-exec-"))
  const stdoutPath = join(captureDir, "stdout.log")
  const stderrPath = join(captureDir, "stderr.log")
  await writeFile(stdoutPath, result.stdout, "utf8")
  await writeFile(stderrPath, result.stderr, "utf8")
  const status: SandboxResultV1["status"] = isAborted
    ? isDeadline
      ? "timed_out"
      : "cancelled"
    : result.exitCode === 0
      ? "completed"
      : "failed"
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status,
    placement_id: `local-process:${request.request_id}`,
    stdout_ref: `file://${stdoutPath}`,
    stderr_ref: `file://${stderrPath}`,
    artifact_refs: [],
    side_effect_digest: buildLocalSideEffectDigest(request, result),
    usage: { exit_code: result.exitCode },
    evidence_refs: [],
    error: isAborted
      ? {
          message: isDeadline ? "Local process exceeded its deadline" : "Execution was cancelled",
          reason: isDeadline ? "deadline_exceeded" : "execution_cancelled",
          exit_code: result.exitCode,
        }
      : result.exitCode === 0
        ? null
        : {
            message: `Local process exited with code ${result.exitCode}`,
            exit_code: result.exitCode,
          },
  }
}

export function makeTrustedLocalExecutionDriver(commandExecutor?: LocalCommandExecutor): TerminalSessionDriverV1 {
  const terminalDriver = new LocalTerminalSessionManager()
  interface ActiveLocalExecution {
    controller: AbortController
    completion: Promise<SandboxResultV1>
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
      activeExecutions.set(request.request_id, {
        controller,
        completion: executionPromise,
      })
      return executionPromise.finally(() => {
        activeExecutions.delete(request.request_id)
        context?.signal.removeEventListener("abort", forwardAbort)
      })
    },
    async terminate(request, context) {
      const active = activeExecutions.get(request.request_id)
      if (!active) return
      active.controller.abort(context.reason)
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
