/// <reference lib="es2024.promise" />
import { spawn } from "node:child_process"
import type { ChildProcessWithoutNullStreams } from "node:child_process"
import { createHash } from "node:crypto"
import { readFile, stat } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { relative, resolve } from "node:path"
import { createInterface } from "node:readline"
import type { ExecutionWorldV1 } from "@breadboard/execution-drivers"
import type { ScheduledExecutionEvidenceV1 } from "@breadboard/execution-driver-remote"

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
  type ScheduledExecutionHandleV1,
  type ScheduledExecutionObservationV1,
} from "@breadboard/execution-driver-remote"
import { createKernelExecutionWorld } from "./default-world.js"
import { buildExecutionPlacement } from "./contracts.js"

const WORKER_HELPER_ENV = "BREADBOARD_RESEARCH_WORLD_HELPER"
const WORKER_MODE_ENV = "BREADBOARD_RESEARCH_WORLD_MODE"
const VERIFIED_CLOSURE_ENV = "BREADBOARD_VERIFIED_ENGINE_ROOT"
const MAX_TASK_BYTES = 1024 * 1024
const MAX_RESULT_BYTES = 4 * 1024 * 1024
const WORLD_DEADLINE_MS = 120_000
const WORLD_MASK = ["/occurred_at", "/timestamp"] as const

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
  readonly execution_evidence: readonly ScheduledExecutionEvidenceV1[]
}

type HelperOperation = "submit" | "resolve" | "observe" | "cancel" | "release"
type HelperState = "accepted" | "running" | "completed" | "failed" | "cancelled" | "timed_out"
type HelperResponse = {
  readonly state: HelperState
  readonly execution_id?: string
  readonly evidence_refs: readonly string[]
  readonly request_digest: string
  readonly receiver_identity: string
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
  const kind = requireText(raw.kind, "world.kind")
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
  return { status: "failed", exit_code: null, stdout, stderr, problem: problem(code, message), execution_evidence: [] }
}

function decodeUtf8(bytes: Buffer, label: string): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes)
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8`, { cause: error })
  }
}

async function verifyBinding(path: string, label: string): Promise<string> {
  const mode = process.env[WORKER_MODE_ENV]
  if (mode !== "source" && mode !== "frozen") throw new Error(`${WORKER_MODE_ENV} must be source or frozen`)
  if (!path.startsWith("/")) throw new Error(`${label} must be an absolute path`)
  const resolved = resolve(path)
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


function helperState(value: unknown): HelperState {
  if (value === "accepted" || value === "running" || value === "completed" || value === "failed" || value === "cancelled" || value === "timed_out") return value
  throw new Error("Ray helper returned an unsupported state")
}

function helperResponse(value: unknown): HelperResponse {
  const raw = requireRecord(value, "Ray helper response")
  const state = helperState(raw.state)
  const execution_id = raw.execution_id === undefined ? undefined : requireSingleLineText(raw.execution_id, "Ray helper execution_id")
  const request_digest = requireSingleLineText(raw.request_digest, "Ray helper request_digest")
  const receiver_identity = requireSingleLineText(raw.receiver_identity, "Ray helper receiver_identity")
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
  if (!Array.isArray(raw.evidence_refs)) throw new Error("Ray helper evidence_refs must be an array")
  const evidence_refs = raw.evidence_refs.map((ref: unknown) => requireSingleLineText(ref, "Ray helper evidence reference"))
  return { state, execution_id, request_digest, receiver_identity, stdout, stderr, error, exit_code, evidence_refs }
}

class RayHelperBackend implements ScheduledExecutionBackendV1 {
  readonly backendId = "ray-helper"
  private readonly child: ChildProcessWithoutNullStreams
  private readonly responses: AsyncIterator<string>
  private readonly exited: Promise<Error | null>
  private queue: Promise<void> = Promise.resolve()
  private readonly requests = new Map<string, SandboxRequestV1>()
  private readonly receiverIdentities = new Map<string, string>()

  constructor(helperPath: string, workspace: string, private readonly world: RayWorld) {
    this.child = spawn(helperPath, ["--research-world-helper"], { cwd: workspace, stdio: ["pipe", "pipe", "pipe"] })
    this.responses = createInterface({ input: this.child.stdout, crlfDelay: Infinity })[Symbol.asyncIterator]()
    const stderr: Buffer[] = []
    let stderrBytes = 0
    let overflow = false
    const deadline = setTimeout(() => this.child.kill("SIGTERM"), WORLD_DEADLINE_MS)
    const force = setTimeout(() => this.child.kill("SIGKILL"), WORLD_DEADLINE_MS + 10_000)
    const completion = Promise.withResolvers<Error | null>()
    this.exited = completion.promise
    this.child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.length
      if (stderrBytes > MAX_RESULT_BYTES) {
        overflow = true
        this.child.kill("SIGTERM")
      } else {
        stderr.push(chunk)
      }
    })
    this.child.on("error", error => completion.resolve(error))
    this.child.on("close", code => {
      clearTimeout(deadline)
      clearTimeout(force)
      completion.resolve(overflow ? new Error("Ray helper output exceeded its bound") :
        code === 0 ? null : new Error(decodeUtf8(Buffer.concat(stderr), "Ray helper stderr") || `Ray helper exited ${code}`))
    })
  }

  private invoke(payload: Record<string, unknown>): Promise<HelperResponse> {
    const operation = this.queue.then(async () => {
      const encoded = JSON.stringify(payload) + "\n"
      if (Buffer.byteLength(encoded) > MAX_TASK_BYTES) throw new Error("Ray helper request exceeds its bound")
      const written = Promise.withResolvers<void>()
      this.child.stdin.write(encoded, error => error ? written.reject(error) : written.resolve())
      await written.promise
      const response = await Promise.race([
        this.responses.next(),
        this.exited.then(error => { throw error ?? new Error("Ray helper closed before its response") }),
      ])
      if (response.done || Buffer.byteLength(response.value) > MAX_RESULT_BYTES) throw new Error("Ray helper response is absent or oversized")
      return helperResponse(JSON.parse(response.value))
    })
    // Keep cleanup available after a failed RPC; the caller retains the rejection.
    this.queue = operation.then(() => undefined, () => undefined)
    return operation
  }

  async close(): Promise<void> {
    const terminate = setTimeout(() => this.child.kill("SIGTERM"), 5000)
    const force = setTimeout(() => this.child.kill("SIGKILL"), 10_000)
    try {
      for (const [executionId, request] of this.requests) {
        if (!this.receiverIdentities.has(executionId)) {
          const resolution = await this.invoke(this.payload("resolve", request, executionId))
          this.assertResponseIdentity(resolution, request, executionId)
          if (resolution.state === "completed") {
            this.requests.delete(executionId)
            this.receiverIdentities.delete(executionId)
            continue
          }
        }
        const response = await this.invoke(this.payload("release", request, executionId))
        this.assertResponseIdentity(response, request, executionId)
        if (response.state !== "completed") throw new Error("Ray helper did not confirm release")
        this.requests.delete(executionId)
        this.receiverIdentities.delete(executionId)
      }
      this.child.stdin.end()
      const error = await this.exited
      if (error) throw error
    } finally {
      this.child.stdin.end()
      await this.exited
      clearTimeout(terminate)
      clearTimeout(force)
    }
  }

  private requestDigest(request: SandboxRequestV1): string {
    return createHash("sha256").update(canonicalScheduledRequestKey(request)).digest("hex")
  }

  private assertResponseIdentity(
    response: HelperResponse,
    request: SandboxRequestV1,
    executionId: string,
  ): void {
    if (response.execution_id !== executionId) throw new Error("Ray helper execution identity changed")
    if (response.request_digest !== this.requestDigest(request)) throw new Error("Ray helper request digest changed")
    const retainedReceiver = this.receiverIdentities.get(executionId)
    if (retainedReceiver !== undefined && response.receiver_identity !== retainedReceiver) {
      throw new Error("Ray helper receiver identity changed")
    }
    this.receiverIdentities.set(executionId, response.receiver_identity)
  }

  private payload(operation: HelperOperation, request: SandboxRequestV1, executionId: string): Record<string, unknown> {
    const receiverIdentity = this.receiverIdentities.get(executionId)
    const requestBytes = canonicalScheduledRequestKey(request)
    return {
      operation,
      execution_id: executionId,
      request_bytes: Buffer.from(requestBytes, "utf8").toString("base64"),
      request_digest: createHash("sha256").update(requestBytes).digest("hex"),
      ...(receiverIdentity === undefined ? {} : { receiver_identity: receiverIdentity }),
      ray_address: this.world.ray_address,
      ray_namespace: this.world.ray_namespace,
      max_output_bytes: this.world.max_output_bytes,
    }
  }

  async submit(request: SandboxRequestV1, _context: ExecutionDriverExecutionContextV1): Promise<ScheduledExecutionHandleV1> {
    const executionId = `ray:${request.request_id}`
    this.requests.set(executionId, request)
    const response = await this.invoke(this.payload("submit", request, executionId))
    this.assertResponseIdentity(response, request, executionId)
    if (response.state !== "accepted" && response.state !== "running") throw new Error("Ray helper did not accept execution")
    return { executionId, evidenceRefs: response.evidence_refs }
  }

  async observe(executionId: string): Promise<ScheduledExecutionObservationV1> {
    const request = this.requests.get(executionId)
    if (!request) throw new Error("Ray execution request is unavailable after worker restart")
    const response = await this.invoke(this.payload("observe", request, executionId))
    this.assertResponseIdentity(response, request, executionId)
    if (response.state === "accepted" || response.state === "running") return { state: response.state, evidenceRefs: response.evidence_refs }
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
    return { state: response.state, result, evidenceRefs: response.evidence_refs }
  }

  async cancel(executionId: string, _context: { readonly reason: "deadline" | "cancelled"; readonly signal: AbortSignal; readonly deadlineAtMs: number | null }): Promise<void> {
    const request = this.requests.get(executionId)
    if (!request) throw new Error("Ray execution request is unavailable for cancellation")
    const response = await this.invoke(this.payload("cancel", request, executionId))
    this.assertResponseIdentity(response, request, executionId)
    if (response.state !== "cancelled" && response.state !== "completed" && response.state !== "failed") throw new Error("Ray helper did not confirm cancellation")
  }
}

function makeWorld(task: ResearchWorldTask, backend: RayHelperBackend | null, evidence: ScheduledExecutionEvidenceV1[]): { world: ExecutionWorldV1; capability: ExecutionCapabilityV1; placement: ExecutionPlacementV1; imageRef: string | null } {
  const { world: config, command, request_id: requestId } = task
  const workspace = config.kind === "slurm" ? config.remote_evidence_directory : task.workspace
  const isolationClass: ExecutionCapabilityV1["isolation_class"] = config.kind === "local" ? "process" : config.kind === "container" ? "oci" : "remote_service"
  const placementClass: ExecutionPlacementV1["placement_class"] = config.kind === "local" ? "local_process" : config.kind === "container" ? "local_oci" : config.kind === "ray" ? "delegated_python" : "remote_worker"
  const driverId = config.kind === "local" ? "local-process" : config.kind === "container" ? "oci" : config.kind
  const capability = assertValid<ExecutionCapabilityV1>("executionCapability", {
    schema_version: "bb.execution_capability.v1",
    capability_id: `research-world:${requestId}`,
    security_tier: config.kind === "local" ? "trusted_dev" : config.kind === "slurm" ? "shared_host" : "single_tenant",
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
    if (!backend) throw new Error(`${WORKER_HELPER_ENV} is required for Ray worlds`)
    const ray: ScheduledExecutionDriverRegistrationV1 = {
      backend,
      options: { pollIntervalMs: 50, cancellationObservationTimeoutMs: 5000, recordEvidence: record => { evidence.push(record) } },
    }
    return { world: createKernelExecutionWorld({ ray, defaultDeadlineMs: WORLD_DEADLINE_MS }), capability, placement, imageRef: null }
  }
  if (config.kind === "slurm") {
    const backend = makeSshSlurmBackend({
      sshTarget: config.ssh_target,
      remoteEvidenceDirectory: config.remote_evidence_directory,
      resourceProfile: {
        cpuCount: 1,
        memoryBytes: 128 * 1024 * 1024,
        timeSeconds: 120,
        gpuCount: 0,
      },
      sshProgram: config.ssh_program,
      commandTimeoutMs: config.command_timeout_ms,
      maxOutputBytes: config.max_output_bytes,
    })
    const slurm: ScheduledExecutionDriverRegistrationV1 = {
      backend,
      options: { pollIntervalMs: 500, cancellationObservationTimeoutMs: 5000, recordEvidence: record => { evidence.push(record) } },
    }
    return { world: createKernelExecutionWorld({ slurm, defaultDeadlineMs: WORLD_DEADLINE_MS }), capability, placement, imageRef: null }
  }
  if (config.kind === "container") {
    return {
      world: createKernelExecutionWorld({ ociRuntimeCommand: config.runtime_command, ociWorkspaceMountTarget: config.workspace_mount_target, defaultDeadlineMs: WORLD_DEADLINE_MS }),
      capability,
      placement,
      imageRef: config.image_ref,
    }
  }
  return { world: createKernelExecutionWorld({ defaultDeadlineMs: WORLD_DEADLINE_MS }), capability, placement, imageRef: null }
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
  let helper: RayHelperBackend | null = null
  const evidence: ScheduledExecutionEvidenceV1[] = []
  try {
    if (task.world.kind === "ray") {
      if (!helperRaw) throw new Error(`${WORKER_HELPER_ENV} is required for Ray worlds`)
      helper = new RayHelperBackend(await verifyBinding(helperRaw, WORKER_HELPER_ENV), task.workspace, task.world)
    }
    const configured = makeWorld(task, helper, evidence)
    const operation = await configured.world.execute({
      kind: "sandbox",
      requestId: task.request_id,
      capability: configured.capability,
      placement: configured.placement,
      command: [...task.command],
      workspaceRef: task.world.kind === "slurm" ? task.world.remote_evidence_directory : task.workspace,
      imageRef: configured.imageRef,
      driverId: task.world.kind === "local" ? "local-process" : task.world.kind === "container" ? "oci" : task.world.kind,
      driverIdHint: task.world.kind === "local" ? "trusted_local" : task.world.kind === "container" ? "oci" : task.world.kind,
    })
    if (operation.kind !== "sandbox") throw new Error("execution world returned a non-sandbox result")
    if (operation.sandboxResult === null) {
      const unsupported = operation.unsupportedCase
      return {
        status: "unsupported",
        exit_code: null,
        stdout: "",
        stderr: "",
        problem: problem(unsupported?.reason_code ?? "unsupported_world", unsupported?.summary ?? "The requested execution world is unsupported"),
        execution_evidence: evidence,
      }
    }
    const sandboxResult = operation.sandboxResult
    const stdout = await artifactText(sandboxResult.stdout_ref, "sandbox stdout")
    const stderr = await artifactText(sandboxResult.stderr_ref, "sandbox stderr")
    if (sandboxResult.status === "completed") {
      return { status: "completed", exit_code: exitCodeFromUsage(sandboxResult.usage), stdout, stderr, problem: null, execution_evidence: evidence }
    }
    const reason = typeof sandboxResult.error?.reason === "string" ? sandboxResult.error.reason : `execution_${sandboxResult.status}`
    const message = typeof sandboxResult.error?.message === "string" ? sandboxResult.error.message : reason
    return { status: "failed", exit_code: exitCodeFromUsage(sandboxResult.usage), stdout, stderr, problem: problem(reason, message), execution_evidence: evidence }
  } finally {
    await helper?.close()
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

async function main(): Promise<number> {
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

void main().then((exitCode) => { process.exitCode = exitCode })
