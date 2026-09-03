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
import { makeOciTerminalSessionDriver, type OciTerminalSessionAdapter } from "./terminals.js"

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
    command: [input.command[0] ?? "", ...input.command.slice(1)],
    network_policy: { allow: input.capability.allow_net_hosts ?? [] },
    secret_refs: [],
    timeout_seconds: null,
    evidence_mode: input.capability.evidence_mode,
    metadata: { driver: "oci" },
  }
}

export interface OciCommandExecutionResult {
  exitCode: number
  stdout: string
  stderr: string
}

export type OciCommandExecutor = (input: {
  runtimeCommand: string
  runtimeArgs: string[]
  signal?: AbortSignal
}) => Promise<OciCommandExecutionResult>
export function buildOciContainerName(requestId: string): string {
  const hash = createHash("sha256").update(requestId).digest("hex").slice(0, 16)
  const sanitized = requestId.replace(/[^a-zA-Z0-9_.-]/g, "-").slice(0, 32)
  return `bb-oci-${sanitized}-${hash}`
}

export function buildOciRuntimeInvocation(
  request: SandboxRequestV1,
  options: {
    runtimeCommand?: string
    workspaceMountTarget?: string
    containerName?: string
  } = {},
): { runtimeCommand: string; runtimeArgs: string[]; containerName: string } {
  if (!request.image_ref) {
    throw new Error("OCI sandbox request requires image_ref")
  }
  const runtimeCommand = options.runtimeCommand ?? "docker"
  const containerName = options.containerName ?? buildOciContainerName(request.request_id)
  const runtimeArgs: string[] = ["run", "--rm", "--name", containerName]
  if (request.placement_class === "local_oci_gvisor") {
    runtimeArgs.push("--runtime=runsc")
  } else if (request.placement_class === "local_oci_kata") {
    runtimeArgs.push("--runtime=io.containerd.kata.v2")
  }
  const mountTarget = options.workspaceMountTarget ?? "/workspace"
  if (request.workspace_ref && request.workspace_ref.startsWith("/")) {
    runtimeArgs.push("-v", `${request.workspace_ref}:${mountTarget}`)
    runtimeArgs.push("-w", mountTarget)
  }
  runtimeArgs.push(request.image_ref)
  runtimeArgs.push(...request.command)
  return { runtimeCommand, runtimeArgs, containerName }
}

export function defaultOciCommandExecutor(input: {
  runtimeCommand: string
  runtimeArgs: string[]
  signal?: AbortSignal
}): Promise<OciCommandExecutionResult> {
  return new Promise((resolve, reject) => {
    let killed = false
    const isPosix = process.platform !== "win32"
    const child = spawn(input.runtimeCommand, input.runtimeArgs, {
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
    child.on("error", (err) => {
      if (!killed) reject(err)
    })
    child.on("close", (exitCode) => {
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
        input.signal.addEventListener("abort", onAbort, { once: true })
      }
    }
  })
}

function buildOciSideEffectDigest(request: SandboxRequestV1, result: OciCommandExecutionResult): string {
  return `sha256:${createHash("sha256")
    .update(
      JSON.stringify({
        request_id: request.request_id,
        placement_class: request.placement_class,
        image_ref: request.image_ref,
        command: request.command,
        workspace_ref: request.workspace_ref,
        exit_code: result.exitCode,
      }),
    )
    .digest("hex")}`
}

export async function executeOciSandboxRequest(
  request: SandboxRequestV1,
  options: {
    commandExecutor?: OciCommandExecutor
    runtimeCommand?: string
    workspaceMountTarget?: string
    containerName?: string
    tempDirRoot?: string
    signal?: AbortSignal
  } = {},
): Promise<SandboxResultV1> {
  const commandExecutor = options.commandExecutor ?? defaultOciCommandExecutor
  const invocation = buildOciRuntimeInvocation(request, {
    runtimeCommand: options.runtimeCommand,
    workspaceMountTarget: options.workspaceMountTarget,
    containerName: options.containerName,
  })
  const result = await commandExecutor({
    runtimeCommand: invocation.runtimeCommand,
    runtimeArgs: invocation.runtimeArgs,
    signal: options.signal,
  })
  const captureDir = await mkdtemp(join(options.tempDirRoot ?? tmpdir(), "breadboard-oci-exec-"))
  const stdoutPath = join(captureDir, "stdout.log")
  const stderrPath = join(captureDir, "stderr.log")
  await writeFile(stdoutPath, result.stdout, "utf8")
  await writeFile(stderrPath, result.stderr, "utf8")
  const isAborted = options.signal?.aborted === true
  const isDeadline = isAborted && options.signal?.reason === "deadline"
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
    placement_id: `oci:${request.request_id}`,
    stdout_ref: `file://${stdoutPath}`,
    stderr_ref: `file://${stderrPath}`,
    artifact_refs: [],
    side_effect_digest: buildOciSideEffectDigest(request, result),
    usage: { exit_code: result.exitCode, runtime: invocation.runtimeCommand },
    evidence_refs: [],
    error: isAborted
      ? {
          message: isDeadline ? "OCI runtime exceeded its deadline" : "Execution was cancelled",
          reason: isDeadline ? "deadline_exceeded" : "execution_cancelled",
          exit_code: result.exitCode,
        }
      : result.exitCode === 0
        ? null
        : {
            message: `OCI runtime exited with code ${result.exitCode}`,
            exit_code: result.exitCode,
          },
  }
}

async function runBoundedCleanupCommand(
  executor: OciCommandExecutor,
  runtimeCommand: string,
  runtimeArgs: string[],
  timeoutMs: number,
): Promise<{ ok: boolean; exitCode?: number }> {
  const controller = new AbortController()
  let timer: NodeJS.Timeout | undefined
  const timeoutPromise = new Promise<{ ok: boolean }>((resolve) => {
    timer = setTimeout(() => {
      controller.abort(new Error(`OCI command timeout: ${runtimeArgs[0]}`))
      resolve({ ok: false })
    }, timeoutMs)
  })
  try {
    const execPromise = Promise.resolve()
      .then(() =>
        executor({
          runtimeCommand,
          runtimeArgs,
          signal: controller.signal,
        }),
      )
      .then(
        (result) => ({ ok: result.exitCode === 0, exitCode: result.exitCode }),
        () => ({ ok: false }),
      )
    return await Promise.race([execPromise, timeoutPromise])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

function createOciExecutionDriver(options: {
  commandExecutor?: OciCommandExecutor
  runtimeCommand?: string
  workspaceMountTarget?: string
  terminalAdapter?: OciTerminalSessionAdapter
} = {}): TerminalSessionDriverV1 {
  const terminalDriver = options.terminalAdapter ? makeOciTerminalSessionDriver(options.terminalAdapter) : null
  interface ActiveOciExecution {
    controller: AbortController
    completion: Promise<SandboxResultV1>
    containerName: string
    runtimeCommand: string
  }
  const activeExecutions = new Map<string, ActiveOciExecution>()
  return {
    driverId: "oci",
    supportedPlacements: ["local_oci", "local_oci_gvisor", "local_oci_kata"],
    supportsCapability(capability, placementClass) {
      return isPlacementCompatible(capability, placementClass)
    },
    buildSandboxRequest({ requestId, capability, command, workspaceRef, imageRef }) {
      if (!imageRef) {
        throw new Error("ociExecutionDriver requires imageRef for OCI-backed execution")
      }
      if (command.length === 0) {
        throw new Error("ociExecutionDriver requires a non-empty command")
      }
      return buildOciSandboxRequest({ requestId, capability, command, workspaceRef, imageRef })
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
      const containerName = buildOciContainerName(request.request_id)
      const runtimeCommand = options.runtimeCommand ?? "docker"
      const executionPromise = executeOciSandboxRequest(request, {
        commandExecutor: options.commandExecutor,
        runtimeCommand,
        workspaceMountTarget: options.workspaceMountTarget,
        containerName,
        signal: controller.signal,
      })
      activeExecutions.set(request.request_id, {
        controller,
        completion: executionPromise,
        containerName,
        runtimeCommand,
      })
      return executionPromise.finally(() => {
        activeExecutions.delete(request.request_id)
        context?.signal.removeEventListener("abort", forwardAbort)
      })
    },
    async terminate(request, context) {
      const active = activeExecutions.get(request.request_id)
      if (!active) return
      const executor = options.commandExecutor ?? defaultOciCommandExecutor
      active.controller.abort(context.reason)

      // Step 1: Attempt graceful stop with fresh bounded signal
      const stopResult = await runBoundedCleanupCommand(
        executor,
        active.runtimeCommand,
        ["stop", "-t", "2", active.containerName],
        2500,
      )

      // Step 2: If stop failed (nonzero, timeout, abort, or threw), attempt kill with fresh bounded signal
      if (!stopResult.ok) {
        await runBoundedCleanupCommand(
          executor,
          active.runtimeCommand,
          ["kill", active.containerName],
          2000,
        )
      }

      // Step 3: Always attempt rm -f with fresh bounded signal
      await runBoundedCleanupCommand(
        executor,
        active.runtimeCommand,
        ["rm", "-f", active.containerName],
        2000,
      )

      const boundedCompletion = new Promise<void>((resolve) => {
        const t = setTimeout(resolve, 2000)
        active.completion.catch(() => {}).finally(() => {
          clearTimeout(t)
          resolve()
        })
      })
      await boundedCompletion
    },
    ...(terminalDriver
      ? {
          supportsTerminalSessions: terminalDriver.supportsTerminalSessions,
          startTerminalSession: terminalDriver.startTerminalSession,
          interactTerminalSession: terminalDriver.interactTerminalSession,
          snapshotTerminalRegistry: terminalDriver.snapshotTerminalRegistry,
          cleanupTerminalSessions: terminalDriver.cleanupTerminalSessions,
        }
      : {}),
  }
}

export const ociExecutionDriver: TerminalSessionDriverV1 = createOciExecutionDriver()

export function makeConfiguredOciExecutionDriver(options: {
  commandExecutor?: OciCommandExecutor
  runtimeCommand?: string
  workspaceMountTarget?: string
  terminalAdapter?: OciTerminalSessionAdapter
} = {}): TerminalSessionDriverV1 {
  return createOciExecutionDriver(options)
}

export function makeOciExecutionDriver(adapter?: OciTerminalSessionAdapter): TerminalSessionDriverV1 {
  return createOciExecutionDriver({ terminalAdapter: adapter })
}

export * from "./terminals.js"
