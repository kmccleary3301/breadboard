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
    command: input.command,
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
}) => Promise<OciCommandExecutionResult>

export function buildOciRuntimeInvocation(
  request: SandboxRequestV1,
  options: {
    runtimeCommand?: string
    workspaceMountTarget?: string
  } = {},
): { runtimeCommand: string; runtimeArgs: string[] } {
  if (!request.image_ref) {
    throw new Error("OCI sandbox request requires image_ref")
  }
  const runtimeCommand = options.runtimeCommand ?? "docker"
  const runtimeArgs: string[] = ["run", "--rm"]
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
  return { runtimeCommand, runtimeArgs }
}

export function defaultOciCommandExecutor(input: {
  runtimeCommand: string
  runtimeArgs: string[]
}): Promise<OciCommandExecutionResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(input.runtimeCommand, input.runtimeArgs, {
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
    tempDirRoot?: string
  } = {},
): Promise<SandboxResultV1> {
  const commandExecutor = options.commandExecutor ?? defaultOciCommandExecutor
  const invocation = buildOciRuntimeInvocation(request, {
    runtimeCommand: options.runtimeCommand,
    workspaceMountTarget: options.workspaceMountTarget,
  })
  const result = await commandExecutor(invocation)
  const captureDir = await mkdtemp(join(options.tempDirRoot ?? tmpdir(), "breadboard-oci-exec-"))
  const stdoutPath = join(captureDir, "stdout.log")
  const stderrPath = join(captureDir, "stderr.log")
  await writeFile(stdoutPath, result.stdout, "utf8")
  await writeFile(stderrPath, result.stderr, "utf8")
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status: result.exitCode === 0 ? "completed" : "failed",
    placement_id: `oci:${request.request_id}`,
    stdout_ref: `file://${stdoutPath}`,
    stderr_ref: `file://${stderrPath}`,
    artifact_refs: [],
    side_effect_digest: buildOciSideEffectDigest(request, result),
    usage: { exit_code: result.exitCode, runtime: invocation.runtimeCommand },
    evidence_refs: [],
    error:
      result.exitCode === 0
        ? null
        : {
            message: `OCI runtime exited with code ${result.exitCode}`,
            exit_code: result.exitCode,
          },
  }
}

export const ociExecutionDriver: TerminalSessionDriverV1 = {
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
    return buildOciSandboxRequest({
      requestId,
      capability,
      command,
      workspaceRef,
      imageRef,
    })
  },
  execute(request) {
    return executeOciSandboxRequest(request)
  },
}

export function makeOciExecutionDriver(adapter?: OciTerminalSessionAdapter): TerminalSessionDriverV1 {
  const terminalDriver = adapter ? makeOciTerminalSessionDriver(adapter) : null
  return {
    ...ociExecutionDriver,
    supportsTerminalSessions: terminalDriver?.supportsTerminalSessions,
    startTerminalSession: terminalDriver?.startTerminalSession,
    interactTerminalSession: terminalDriver?.interactTerminalSession,
    snapshotTerminalRegistry: terminalDriver?.snapshotTerminalRegistry,
    cleanupTerminalSessions: terminalDriver?.cleanupTerminalSessions,
  }
}

export * from "./terminals.js"
