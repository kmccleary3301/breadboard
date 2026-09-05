import { spawn } from "node:child_process"
import { createHash } from "node:crypto"
import { chmod, copyFile, mkdir, readFile, stat, unlink } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { dirname, join, relative, resolve } from "node:path"

import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type SandboxRequestV1,
  type SandboxResultV1,
} from "@breadboard/kernel-contracts"
import {
  buildAndPersistCanonicalSandboxEvidence,
  canonicalSandboxArtifactUri,
  type ExecutionDriverExecutionContextV1,
} from "@breadboard/execution-drivers"
import {
  canonicalScheduledRequestKey,
  makeSshSlurmBackend,
  type ScheduledExecutionBackendV1,
  type ScheduledExecutionDriverRegistrationV1,
  type ScheduledExecutionObservationV1,
} from "@breadboard/execution-driver-remote"
import { createKernelExecutionWorld } from "./default-world.js"
import { buildExecutionPlacement } from "./contracts.js"

const WORKER_TOKEN = "@breadboard/research-world-worker/v1"
const WORKER_BINDING_ENV = "BREADBOARD_RESEARCH_WORLD_WORKER"
const WORKER_HELPER_ENV = "BREADBOARD_RESEARCH_WORLD_HELPER"
const WORKER_MODE_ENV = "BREADBOARD_RESEARCH_WORLD_MODE"
const VERIFIED_CLOSURE_ENV = "BREADBOARD_VERIFIED_ENGINE_ROOT"
const MAX_TASK_BYTES = 1024 * 1024
const MAX_RESULT_BYTES = 4 * 1024 * 1024
const WORLD_MASK = ["/occurred_at", "/timestamp"] as const

type WorldKind = "local" | "container" | "ray" | "slurm"
type WorldFieldMask = readonly ["/occurred_at", "/timestamp"]

type LocalWorld = {
  readonly kind: "local"
  readonly field_mask: WorldFieldMask
  readonly python: string
}
type ContainerWorld = {
  readonly kind: "container"
  readonly field_mask: WorldFieldMask
  readonly python: string
  readonly image_ref: string
  readonly runtime_command: string
  readonly workspace_mount_target: string
}
type RayWorld = {
  readonly kind: "ray"
  readonly field_mask: WorldFieldMask
  readonly python: string
  readonly ray_address: string
  readonly ray_namespace: string
  readonly max_output_bytes: number
}
type SlurmWorld = {
  readonly kind: "slurm"
  readonly field_mask: WorldFieldMask
  readonly python: string
  readonly ssh_target: string
  readonly remote_evidence_directory: string
  readonly ssh_program: string
  readonly command_timeout_ms: number
  readonly max_output_bytes: number
}
type ResearchWorld = LocalWorld | ContainerWorld | RayWorld | SlurmWorld

type ResearchWorldTask = {
  readonly world: ResearchWorld
  readonly request_id: string
  readonly workspace: string
  readonly command: readonly string[]
}

type WorkerProblem = { readonly code: string; readonly message: string }
type WorkerResult = {
  readonly status: "completed" | "failed" | "unsupported"
  readonly exit_code: number | null
  readonly stdout: string
  readonly stderr: string
  readonly problem: WorkerProblem | null
}

type HelperOperation = "submit" | "observe" | "cancel" | "release"
type HelperState = "accepted" | "running" | "completed" | "failed" | "cancelled" | "timed_out"
type HelperResponse = {
  readonly state: HelperState
  readonly execution_id?: string
  readonly exit_code?: number | null
  readonly stdout?: string
  readonly stderr?: string
  readonly error?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${label} must be an object`)
  return value
}

function requireText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "" || value.includes("\0")) {
    throw new Error(`${label} must be a non-empty string`)
  }
  return value
}

function requireSingleLineText(value: unknown, label: string): string {
  const text = requireText(value, label)
  if (text.includes("\r") || text.includes("\n")) {
    throw new Error(`${label} must be a single-line string`)
  }
  return text
}

function requirePositiveSafeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${label} must be a positive safe integer`)
  }
  return value
}

function assertKeys(value: Record<string, unknown>, allowed: readonly string[], label: string): void {
  const allowedSet = new Set(allowed)
  const unknown = Object.keys(value).filter((key) => !allowedSet.has(key))
  if (unknown.length > 0) throw new Error(`${label} contains unsupported field '${unknown[0]}'`)
}

function parseFieldMask(value: unknown): WorldFieldMask {
  if (
    !Array.isArray(value)
    || value.length !== WORLD_MASK.length
    || value[0] !== WORLD_MASK[0]
    || value[1] !== WORLD_MASK[1]
  ) {
    throw new Error("world.field_mask must be exactly ['/occurred_at','/timestamp']")
  }
  return [...WORLD_MASK]
}

function parseWorld(value: unknown): ResearchWorld {
  const raw = requireRecord(value, "world")
  const kind = requireText(raw.kind, "world.kind") as WorldKind
  const field_mask = parseFieldMask(raw.field_mask)
  const python = requireSingleLineText(raw.python, "world.python")
  switch (kind) {
    case "local":
      assertKeys(raw, ["kind", "field_mask", "python"], "local world")
      return { kind, field_mask, python }
    case "container":
      assertKeys(raw, ["kind", "field_mask", "python", "image_ref", "runtime_command", "workspace_mount_target"], "container world")
      return {
        kind,
        field_mask,
        python,
        image_ref: requireSingleLineText(raw.image_ref, "world.image_ref"),
        runtime_command: requireSingleLineText(raw.runtime_command, "world.runtime_command"),
        workspace_mount_target: requireSingleLineText(raw.workspace_mount_target, "world.workspace_mount_target"),
      }
    case "ray":
      assertKeys(raw, ["kind", "field_mask", "python", "ray_address", "ray_namespace", "max_output_bytes"], "Ray world")
      return {
        kind,
        field_mask,
        python,
        ray_address: requireSingleLineText(raw.ray_address, "world.ray_address"),
        ray_namespace: requireSingleLineText(raw.ray_namespace, "world.ray_namespace"),
        max_output_bytes: requirePositiveSafeInteger(raw.max_output_bytes, "world.max_output_bytes"),
      }
    case "slurm":
      assertKeys(raw, ["kind", "field_mask", "python", "ssh_target", "remote_evidence_directory", "ssh_program", "command_timeout_ms", "max_output_bytes"], "Slurm world")
      return {
        kind,
        field_mask,
        python,
        ssh_target: requireSingleLineText(raw.ssh_target, "world.ssh_target"),
        remote_evidence_directory: requireSingleLineText(raw.remote_evidence_directory, "world.remote_evidence_directory"),
        ssh_program: requireSingleLineText(raw.ssh_program, "world.ssh_program"),
        command_timeout_ms: requirePositiveSafeInteger(raw.command_timeout_ms, "world.command_timeout_ms"),
        max_output_bytes: requirePositiveSafeInteger(raw.max_output_bytes, "world.max_output_bytes"),
      }
    default:
      throw new Error(`unsupported world.kind '${kind}'`)
  }
}

function parseTask(value: unknown): ResearchWorldTask {
  const raw = requireRecord(value, "task")
  assertKeys(raw, ["world", "request_id", "workspace", "command"], "task")
  const commandValue = raw.command
  if (!Array.isArray(commandValue) || commandValue.length === 0 || commandValue.some((part) => typeof part !== "string" || part.length === 0 || part.includes("\0"))) {
    throw new Error("task.command must be a non-empty string array")
  }
  const workspace = requireText(raw.workspace, "task.workspace")
  if (!workspace.startsWith("/")) throw new Error("task.workspace must be an absolute path")
  return {
    world: parseWorld(raw.world),
    request_id: requireSingleLineText(raw.request_id, "task.request_id"),
    workspace: resolve(workspace),
    command: [...commandValue],
  }
}

function problem(code: string, message: string): WorkerProblem {
  return { code, message }
}

function failure(code: string, message: string, stdout = "", stderr = ""): WorkerResult {
  return { status: "failed", exit_code: null, stdout, stderr, problem: problem(code, message) }
}

function decodeUtf8(bytes: Buffer, label: string): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes)
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8`, { cause: error })
  }
}

async function readBounded(stream: NodeJS.ReadableStream, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = []
  let size = 0
  return await new Promise<Buffer>((resolvePromise, rejectPromise) => {
    stream.on("data", (chunk: Buffer | string) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      size += bytes.length
      if (size > limit) {
        rejectPromise(new Error("helper output exceeded the bounded result limit"))
        return
      }
      chunks.push(bytes)
    })
    stream.on("end", () => resolvePromise(Buffer.concat(chunks)))
    stream.on("error", rejectPromise)
  })
}

function ensureExecutableBinding(rawPath: string, label: string): string {
  if (!rawPath.startsWith("/")) throw new Error(`${label} must be an absolute path`)
  const path = resolve(rawPath)
  return path
}

async function verifyBinding(path: string, label: string, mode: "source" | "frozen"): Promise<string> {
  const resolved = ensureExecutableBinding(path, label)
  const info = await stat(resolved)
  if (!info.isFile()) throw new Error(`${label} must be a regular file`)
  if ((info.mode & 0o111) === 0) throw new Error(`${label} must be executable`)
  if (mode === "frozen") {
    const rootRaw = process.env[VERIFIED_CLOSURE_ENV]
    if (!rootRaw || !rootRaw.startsWith("/")) throw new Error(`${VERIFIED_CLOSURE_ENV} is required in frozen mode`)
    const root = resolve(rootRaw)
    const outside = relative(root, resolved)
    if (outside.startsWith("..") || outside === "") {
      throw new Error(`${label} is outside the verified engine closure`)
    }
  }
  return resolved
}

async function workerMode(): Promise<"source" | "frozen"> {
  const mode = process.env[WORKER_MODE_ENV]
  if (mode !== "source" && mode !== "frozen") {
    throw new Error(`${WORKER_MODE_ENV} must be explicitly set to source or frozen`)
  }
  return mode
}

async function bindWorkerExecutable(): Promise<string> {
  const binding = process.env[WORKER_BINDING_ENV]
  if (!binding) throw new Error(`${WORKER_BINDING_ENV} is required for the research worker`)
  return verifyBinding(binding, WORKER_BINDING_ENV, await workerMode())
}

async function materializeHelper(path: string, workspace: string): Promise<{ path: string; cleanup: () => Promise<void> }> {
  const source = await verifyBinding(path, WORKER_HELPER_ENV, await workerMode())
  const root = join(workspace, ".breadboard", "research-world")
  await mkdir(root, { recursive: true, mode: 0o700 })
  const identity = createHash("sha256").update(source).digest("hex")
  const destination = join(root, `helper-${identity}`)
  await copyFile(source, destination)
  await chmod(destination, 0o700)
  return {
    path: destination,
    cleanup: async () => {
      await unlink(destination).catch(() => {})
    },
  }
}

function helperState(value: unknown): HelperState {
  if (value === "accepted" || value === "running" || value === "completed" || value === "failed" || value === "cancelled" || value === "timed_out") return value
  throw new Error("Ray helper returned an unsupported state")
}

function helperResponse(value: unknown): HelperResponse {
  const raw = requireRecord(value, "Ray helper response")
  const state = helperState(raw.state)
  const execution_id = raw.execution_id === undefined ? undefined : requireSingleLineText(raw.execution_id, "Ray helper execution_id")
  const stdout = raw.stdout === undefined ? undefined : typeof raw.stdout === "string" ? raw.stdout : (() => { throw new Error("Ray helper stdout must be a string") })()
  const stderr = raw.stderr === undefined ? undefined : typeof raw.stderr === "string" ? raw.stderr : (() => { throw new Error("Ray helper stderr must be a string") })()
  const error = raw.error === undefined ? undefined : requireText(raw.error, "Ray helper error")
  const rawExitCode = raw.exit_code
  const exit_code =
    rawExitCode === undefined || rawExitCode === null
      ? rawExitCode ?? undefined
      : typeof rawExitCode === "number" && Number.isSafeInteger(rawExitCode)
        ? rawExitCode
        : (() => { throw new Error("Ray helper exit_code must be a safe integer or null") })()
  return { state, execution_id, stdout, stderr, error, exit_code }
}

async function invokeHelper(helperPath: string, operation: HelperOperation, payload: Record<string, unknown>, workspace: string): Promise<HelperResponse> {
  const encoded = Buffer.from(JSON.stringify(payload), "utf8")
  if (encoded.length > MAX_TASK_BYTES) throw new Error("Ray helper request exceeds the bounded task limit")
  return await new Promise<HelperResponse>((resolvePromise, rejectPromise) => {
    const child = spawn(helperPath, ["--research-world-helper", operation], {
      cwd: workspace,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env },
    })
    let stdout = Buffer.alloc(0)
    let stderr = Buffer.alloc(0)
    let overflow = false
    const collect = (chunk: Buffer, current: Buffer): Buffer => {
      const next = Buffer.concat([current, chunk])
      if (next.length > MAX_RESULT_BYTES) {
        overflow = true
        child.kill("SIGKILL")
        return current
      }
      return next
    }
    child.stdout.on("data", (chunk: Buffer) => { stdout = collect(chunk, stdout) })
    child.stderr.on("data", (chunk: Buffer) => { stderr = collect(chunk, stderr) })
    child.on("error", (error) => rejectPromise(error))
    child.on("close", (exitCode) => {
      if (overflow) {
        rejectPromise(new Error("Ray helper output exceeded the bounded result limit"))
        return
      }
      if (exitCode !== 0) {
        const detail = stderr.length > 0 ? decodeUtf8(stderr, "Ray helper stderr") : "Ray helper exited unsuccessfully"
        rejectPromise(new Error(detail))
        return
      }
      try {
        resolvePromise(helperResponse(JSON.parse(decodeUtf8(stdout, "Ray helper stdout"))))
      } catch (error) {
        rejectPromise(error)
      }
    })
    child.stdin.end(encoded)
  })
}

function requestDigest(request: SandboxRequestV1): string {
  return createHash("sha256").update(canonicalScheduledRequestKey(request)).digest("hex")
}

class RayHelperBackend implements ScheduledExecutionBackendV1 {
  readonly backendId = "ray-helper"
  private readonly helperPath: string
  private readonly workspace: string
  private readonly world: RayWorld
  private readonly requests = new Map<string, SandboxRequestV1>()

  constructor(helperPath: string, workspace: string, world: RayWorld) {
    this.helperPath = helperPath
    this.workspace = workspace
    this.world = world
  }

  private payload(operation: HelperOperation, request: SandboxRequestV1, executionId: string): Record<string, unknown> {
    return {
      operation,
      execution_id: executionId,
      request,
      request_digest: requestDigest(request),
      workspace: this.workspace,
      ray_address: this.world.ray_address,
      ray_namespace: this.world.ray_namespace,
      max_output_bytes: this.world.max_output_bytes,
    }
  }

  async submit(request: SandboxRequestV1, _context: ExecutionDriverExecutionContextV1): Promise<{ executionId: string }> {
    const executionId = `ray:${request.request_id}`
    const response = await invokeHelper(this.helperPath, "submit", this.payload("submit", request, executionId), this.workspace)
    if (response.execution_id !== undefined && response.execution_id !== executionId) throw new Error("Ray helper execution identity changed")
    if (response.state !== "accepted" && response.state !== "running") throw new Error("Ray helper did not accept execution")
    this.requests.set(executionId, request)
    return { executionId }
  }

  async observe(executionId: string): Promise<ScheduledExecutionObservationV1> {
    const request = this.requests.get(executionId)
    if (!request) throw new Error("Ray execution request is unavailable after worker restart")
    const response = await invokeHelper(this.helperPath, "observe", this.payload("observe", request, executionId), this.workspace)
    if (response.state === "accepted" || response.state === "running") return { state: response.state, evidenceRefs: [] }
    const status: SandboxResultV1["status"] = response.state === "completed" ? "completed" : response.state === "cancelled" ? "cancelled" : response.state === "timed_out" ? "timed_out" : "failed"
    const stdout = response.stdout ?? ""
    const stderr = response.stderr ?? ""
    const exitCode = response.exit_code === undefined ? null : response.exit_code
    const evidence = await buildAndPersistCanonicalSandboxEvidence({
      command: request.command,
      status,
      exitCode,
      stdout,
      stderr,
      evidenceMode: request.evidence_mode,
    })
    const result = assertValid<SandboxResultV1>("sandboxResult", {
      schema_version: "bb.sandbox_result.v1",
      request_id: request.request_id,
      status,
      ...evidence,
      error: status === "completed" ? null : { reason: response.error ?? `ray_${status}` },
    })
    return { state: response.state, result, evidenceRefs: [] }
  }

  async cancel(executionId: string, _context: { readonly reason: "deadline" | "cancelled"; readonly signal: AbortSignal; readonly deadlineAtMs: number | null }): Promise<void> {
    const request = this.requests.get(executionId)
    if (!request) throw new Error("Ray execution request is unavailable for cancellation")
    const response = await invokeHelper(this.helperPath, "cancel", this.payload("cancel", request, executionId), this.workspace)
    if (response.state !== "cancelled" && response.state !== "completed" && response.state !== "failed") throw new Error("Ray helper did not confirm cancellation")
  }
}

function makeWorld(task: ResearchWorldTask, helperPath: string | null): { world: ReturnType<typeof createKernelExecutionWorld>; capability: ExecutionCapabilityV1; placement: ExecutionPlacementV1; imageRef: string | null } {
  const { world: config, command, request_id: requestId, workspace } = task
  const isolationClass: ExecutionCapabilityV1["isolation_class"] = config.kind === "local" ? "process" : config.kind === "container" ? "oci" : "remote_service"
  const placementClass: ExecutionPlacementV1["placement_class"] = config.kind === "local" ? "local_process" : config.kind === "container" ? "local_oci" : config.kind === "ray" ? "delegated_python" : "remote_worker"
  const driverId = config.kind === "local" ? "local-process" : config.kind === "container" ? "oci" : config.kind
  const capability = assertValid<ExecutionCapabilityV1>("executionCapability", {
    schema_version: "bb.execution_capability.v1",
    capability_id: `research-world:${requestId}`,
    security_tier: "trusted_dev",
    isolation_class: isolationClass,
    allow_read_paths: [workspace],
    allow_write_paths: [workspace],
    allow_run_programs: [command[0]],
    ...(config.kind === "container" ? { allow_net_hosts: [] } : {}),
    allow_env_keys: [],
    secret_mode: "ref_only",
    tty_mode: "none",
    resource_budget: null,
    evidence_mode: "replay_strict",
  })
  const placement = buildExecutionPlacement(capability, {
    placementId: `${requestId}:placement:${placementClass}`,
    placementClass,
    runtimeId: driverId,
    metadata: { world_kind: config.kind, field_mask: [...WORLD_MASK] },
  })
  if (config.kind === "ray") {
    if (!helperPath) throw new Error(`${WORKER_HELPER_ENV} is required for Ray worlds`)
    const backend = new RayHelperBackend(helperPath, workspace, config)
    const ray: ScheduledExecutionDriverRegistrationV1 = {
      backend,
      options: { pollIntervalMs: 50, cancellationObservationTimeoutMs: 5000, recordEvidence: () => {} },
    }
    return { world: createKernelExecutionWorld({ ray }), capability, placement, imageRef: null }
  }
  if (config.kind === "slurm") {
    const backend = makeSshSlurmBackend({
      sshTarget: config.ssh_target,
      remoteEvidenceDirectory: config.remote_evidence_directory,
      sshProgram: config.ssh_program,
      commandTimeoutMs: config.command_timeout_ms,
      maxOutputBytes: config.max_output_bytes,
    })
    const slurm: ScheduledExecutionDriverRegistrationV1 = {
      backend,
      options: { pollIntervalMs: 500, cancellationObservationTimeoutMs: 5000, recordEvidence: () => {} },
    }
    return { world: createKernelExecutionWorld({ slurm }), capability, placement, imageRef: null }
  }
  if (config.kind === "container") {
    return {
      world: createKernelExecutionWorld({ ociRuntimeCommand: config.runtime_command, ociWorkspaceMountTarget: config.workspace_mount_target }),
      capability,
      placement,
      imageRef: config.image_ref,
    }
  }
  return { world: createKernelExecutionWorld(), capability, placement, imageRef: null }
}

function exitCodeFromUsage(value: SandboxResultV1["usage"]): number | null {
  if (!isRecord(value)) return null
  const exitCode = value.exit_code
  return exitCode === null || exitCode === undefined ? null : typeof exitCode === "number" && Number.isSafeInteger(exitCode) ? exitCode : null
}

async function artifactText(ref: string | null | undefined, label: string): Promise<string> {
  if (!ref) return ""
  const path = fileURLToPath(canonicalSandboxArtifactUri(ref))
  const bytes = await readFile(path)
  if (bytes.length > MAX_RESULT_BYTES) throw new Error(`${label} exceeds the bounded result limit`)
  return decodeUtf8(bytes, label)
}

async function executeTask(task: ResearchWorldTask): Promise<WorkerResult> {
  const info = await stat(task.workspace)
  if (!info.isDirectory()) throw new Error("task.workspace is not a directory")
  const helperRaw = process.env[WORKER_HELPER_ENV]
  let helperMaterialization: { path: string; cleanup: () => Promise<void> } | null = null
  try {
    if (task.world.kind === "ray") {
      if (!helperRaw) throw new Error(`${WORKER_HELPER_ENV} is required for Ray worlds`)
      helperMaterialization = await materializeHelper(helperRaw, task.workspace)
    }
    const configured = makeWorld(task, helperMaterialization?.path ?? null)
    const operation = await configured.world.execute({
      kind: "sandbox",
      requestId: task.request_id,
      capability: configured.capability,
      placement: configured.placement,
      command: [...task.command],
      workspaceRef: task.workspace,
      imageRef: configured.imageRef,
      driverId: task.world.kind === "local" ? "local-process" : task.world.kind === "container" ? "oci" : task.world.kind,
      driverIdHint: task.world.kind === "local" ? "trusted_local" : task.world.kind === "container" ? "oci" : task.world.kind,
    })
    if (operation.sandboxResult === null) {
      const unsupported = operation.unsupportedCase
      return {
        status: "unsupported",
        exit_code: null,
        stdout: "",
        stderr: "",
        problem: problem(unsupported?.reason_code ?? "unsupported_world", unsupported?.summary ?? "The requested execution world is unsupported"),
      }
    }
    const sandboxResult = operation.sandboxResult
    const stdout = await artifactText(sandboxResult.stdout_ref, "sandbox stdout")
    const stderr = await artifactText(sandboxResult.stderr_ref, "sandbox stderr")
    if (sandboxResult.status === "completed") {
      return { status: "completed", exit_code: exitCodeFromUsage(sandboxResult.usage), stdout, stderr, problem: null }
    }
    const reason = typeof sandboxResult.error?.reason === "string" ? sandboxResult.error.reason : `execution_${sandboxResult.status}`
    const message = typeof sandboxResult.error?.message === "string" ? sandboxResult.error.message : reason
    return { status: "failed", exit_code: exitCodeFromUsage(sandboxResult.usage), stdout, stderr, problem: problem(reason, message) }
  } finally {
    await helperMaterialization?.cleanup()
  }
}

async function readStdinBounded(): Promise<Buffer> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of process.stdin) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    size += bytes.length
    if (size > MAX_TASK_BYTES) throw new Error("worker task exceeds the bounded input limit")
    chunks.push(bytes)
  }
  return Buffer.concat(chunks)
}

function writeResult(value: WorkerResult): void {
  const encoded = JSON.stringify(value)
  const bytes = Buffer.from(encoded, "utf8")
  if (bytes.length > MAX_RESULT_BYTES) throw new Error("worker result exceeds the bounded output limit")
  process.stdout.write(encoded)
}

export async function main(): Promise<number> {
  try {
    const task = parseTask(JSON.parse(decodeUtf8(await readStdinBounded(), "worker task")))
    writeResult(await executeTask(task))
    return 0
  } catch (error) {
    const message = error instanceof Error ? error.message : "research world worker failed"
    try {
      writeResult(failure("worker_protocol_error", message))
      return 0
    } catch {
      process.stderr.write(`${message}\n`)
      return 1
    }
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  void main().then((exitCode) => { process.exitCode = exitCode })
}
