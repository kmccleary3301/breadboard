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
import { trustedLocalTerminalSessionDriver } from "./terminals.js"

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
    command: input.command,
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
}) => Promise<LocalCommandExecutionResult>

export function defaultLocalCommandExecutor(input: {
  command: string[]
  cwd?: string | null
}): Promise<LocalCommandExecutionResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(input.command[0]!, input.command.slice(1), {
      cwd: input.cwd ?? undefined,
      stdio: ["ignore", "pipe", "pipe"],
    })
    let stdout = ""
    let stderr = ""
    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk)
    })
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk)
    })
    child.on("error", reject)
    child.on("close", (exitCode) => {
      resolve({
        exitCode: exitCode ?? 1,
        stdout,
        stderr,
      })
    })
  })
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
  } = {},
): Promise<SandboxResultV1> {
  const executeCommand = options.commandExecutor ?? defaultLocalCommandExecutor
  const cwd = request.workspace_ref && request.workspace_ref.startsWith("/") ? request.workspace_ref : null
  const result = await executeCommand({
    command: request.command,
    cwd,
  })
  const captureDir = await mkdtemp(join(options.tempDirRoot ?? tmpdir(), "breadboard-local-exec-"))
  const stdoutPath = join(captureDir, "stdout.log")
  const stderrPath = join(captureDir, "stderr.log")
  await writeFile(stdoutPath, result.stdout, "utf8")
  await writeFile(stderrPath, result.stderr, "utf8")
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status: result.exitCode === 0 ? "completed" : "failed",
    placement_id: `local-process:${request.request_id}`,
    stdout_ref: `file://${stdoutPath}`,
    stderr_ref: `file://${stderrPath}`,
    artifact_refs: [],
    side_effect_digest: buildLocalSideEffectDigest(request, result),
    usage: { exit_code: result.exitCode },
    evidence_refs: [],
    error:
      result.exitCode === 0
        ? null
        : {
            message: `Local process exited with code ${result.exitCode}`,
            exit_code: result.exitCode,
          },
  }
}

export const trustedLocalExecutionDriver: TerminalSessionDriverV1 = {
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
  execute(request) {
    return executeLocalProcessSandboxRequest(request)
  },
  supportsTerminalSessions: trustedLocalTerminalSessionDriver.supportsTerminalSessions,
  startTerminalSession: trustedLocalTerminalSessionDriver.startTerminalSession,
  interactTerminalSession: trustedLocalTerminalSessionDriver.interactTerminalSession,
  snapshotTerminalRegistry: trustedLocalTerminalSessionDriver.snapshotTerminalRegistry,
  cleanupTerminalSessions: trustedLocalTerminalSessionDriver.cleanupTerminalSessions,
}

export function chooseTrustedLocalPlacement(
  capability: ExecutionCapabilityV1,
): ExecutionPlacementV1["placement_class"] {
  return capability.isolation_class === "none" ? "inline_ts" : "local_process"
}

export * from "./terminals.js"
