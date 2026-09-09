import { createHash } from "node:crypto"
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { lstat, realpath } from "node:fs/promises"
import { dirname } from "node:path"
import type {
  AuthorWorkerChannelV1,
  AuthorWorkerCleanupResultV1,
  AuthorWorkerLaunchInputV1,
  AuthorWorkerResourceIdentityV1,
} from "@breadboard/execution-drivers"
import { OWNER_LABEL, EXECUTION_LABEL, TOKEN_LABEL, parseDockerImage, parseDockerContainer, type DockerContainerInspect } from "./author-management.js"
import { AuthorWorkerLaunchError } from "@breadboard/execution-drivers"

export const AUTHOR_FRAME_MAX_BYTES = 262_144
const COMMAND_TIMEOUT_MS = 2_000
const COMMAND_OUTPUT_BYTES = 64 * 1024
const OCI_CONFIG_ID = /^sha256:[0-9a-f]{64}$/
const OCI_REGISTRY_REF = /^[^\s@]+@sha256:[0-9a-f]{64}$/



export interface OciAuthorChannelOptions {
  readonly runtimeCommand?: string
  readonly onIntent?: (intent: {
    readonly ownerRef: string
    readonly executionId: string
    readonly resourceId: string
    readonly containerName: string
  }) => void | Promise<void>
  readonly onReceipt?: (identity: AuthorWorkerResourceIdentityV1) => void | Promise<void>
}

interface RuntimeCommandResult {
  readonly exitCode: number
  readonly stdout: string
  readonly stderr: string
}
interface ContainerIdentity {
  readonly containerId: string
  readonly imageId: string
  readonly imageRef: string
  readonly ownerRef: string
  readonly executionId: string
  readonly executionToken: string
  readonly state: string
}
interface ImageIdentity {
  readonly imageId: string
  readonly platform: string
  readonly variant: string
}

export function dockerManagementEnvironment(): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {}
  for (const key of ["PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "XDG_RUNTIME_DIR"]) {
    const value = process.env[key]
    if (value !== undefined) environment[key] = value
  }
  return environment
}

export function encodeAuthorFrame(body: Uint8Array): Buffer {
  if (body.byteLength === 0 || body.byteLength > AUTHOR_FRAME_MAX_BYTES) throw new Error("Invalid author frame length")
  const frame = Buffer.allocUnsafe(4 + body.byteLength)
  frame.writeUInt32BE(body.byteLength, 0)
  frame.set(body, 4)
  return frame
}

export class AuthorFrameReader {
  private pending: Buffer = Buffer.alloc(0)
  append(chunk: Buffer): void {
    if (this.pending.byteLength + chunk.byteLength > AUTHOR_FRAME_MAX_BYTES + 4 + 64 * 1024) {
      throw new Error("Author transport buffer exceeds its limit")
    }
    this.pending = this.pending.byteLength === 0 ? chunk : Buffer.concat([this.pending, chunk])
  }
  take(): Buffer | null {
    if (this.pending.byteLength < 4) return null
    const size = this.pending.readUInt32BE(0)
    if (size === 0 || size > AUTHOR_FRAME_MAX_BYTES) throw new Error("Invalid author frame length")
    if (this.pending.byteLength < size + 4) return null
    const body = this.pending.subarray(4, size + 4)
    this.pending = this.pending.subarray(size + 4)
    return body
  }
  finish(): void {
    if (this.pending.byteLength !== 0) throw new Error("Author channel closed inside a frame")
  }
}

function positiveInteger(value: number | undefined, fallback: number, label: string): number {
  if (value === undefined) return fallback
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) throw new Error(`${label} must be a positive integer`)
  return value
}

function assertLaunchProfile(input: AuthorWorkerLaunchInputV1): void {
  if (!input.ownerRef || !input.executionId || input.executionToken.length < 32) throw new Error("An owner-minted execution identity is required")
  if (!OCI_CONFIG_ID.test(input.imageRef) && !OCI_REGISTRY_REF.test(input.imageRef)) throw new Error("An immutable OCI digest or local image config ID is required")
  if (!/^linux\/[a-z0-9_]+(?:\/[a-z0-9_.-]+)?$/.test(input.platform)) throw new Error("An explicit Linux platform is required")
  if (input.command.length === 0 || input.command.some(part => !part || part.includes("\0"))) throw new Error("An explicit worker command is required")
  const profile = input.profile
  const cpu = positiveInteger(profile?.cpuCount, 1, "cpuCount")
  const memory = positiveInteger(profile?.memoryBytes, 64 * 1024 * 1024, "memoryBytes")
  const processes = positiveInteger(profile?.processCount, 1, "processCount")
  const scratch = positiveInteger(profile?.scratchBytes, 8 * 1024 * 1024, "scratchBytes")
  if (cpu !== 1 || memory !== 64 * 1024 * 1024 || processes !== 1 || scratch !== 8 * 1024 * 1024) {
    if (!input.capacityAuthorization) throw new Error("A non-default worker profile requires owner capacity authorization")
  }
  if (input.packageMountTarget !== undefined && input.packageMountTarget !== "/breadboard-captured") throw new Error("The captured package mount target is fixed")
}

async function validateStaging(input: AuthorWorkerLaunchInputV1): Promise<void> {
  const root = input.capturedStagingRoot
  if (!root || !input.stagingOwnerRef) throw new Error("Captured staging and its owner reference are required")
  if (await realpath(root) !== root) throw new Error("Captured staging must be canonical and not a symlink")
  const [directory, enclosing] = await Promise.all([lstat(root), lstat(dirname(root))])
  const uid = process.getuid?.()
  if (!directory.isDirectory() || !enclosing.isDirectory() || directory.isSymbolicLink() || enclosing.isSymbolicLink() || uid === undefined || directory.uid !== uid || enclosing.uid !== uid || (enclosing.mode & 0o077) !== 0) {
    throw new Error("Captured staging requires an owner-private enclosing directory")
  }
  if ((directory.mode & 0o222) !== 0 || (directory.mode & 0o005) !== 0o005) throw new Error("Captured staging must be read-only and readable by the nonroot receiver")
}

function createArgs(input: AuthorWorkerLaunchInputV1, name: string): string[] {
  const memory = input.profile?.memoryBytes ?? 64 * 1024 * 1024
  return [
    "create", "--interactive", "--name", name, "--platform", input.platform,
    "--label", `${OWNER_LABEL}=${input.ownerRef}`,
    "--label", `${EXECUTION_LABEL}=${input.executionId}`,
    "--label", `${TOKEN_LABEL}=${input.executionToken}`,
    "--user", "65532:65532", "--read-only",
    "--tmpfs", `/tmp:rw,noexec,nosuid,mode=1777,size=${input.profile?.scratchBytes ?? 8 * 1024 * 1024}`,
    "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
    "--cpus", String(input.profile?.cpuCount ?? 1),
    "--memory", String(memory), "--memory-swap", String(memory),
    "--pids-limit", String(input.profile?.processCount ?? 1),
    "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
    "--workdir", "/tmp",
    "--mount", `type=bind,src=${input.capturedStagingRoot},dst=/breadboard-captured,readonly`,
    "--entrypoint", input.command[0]!, input.imageRef, ...input.command.slice(1),
  ]
}

async function command(runtime: string, args: readonly string[]): Promise<RuntimeCommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(runtime, [...args], { stdio: ["ignore", "pipe", "pipe"], env: dockerManagementEnvironment() })
    const stdout: Buffer[] = [], stderr: Buffer[] = []
    let size = 0, settled = false
    const fail = (error: Error): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      child.kill("SIGKILL")
      reject(error)
    }
    const timer = setTimeout(() => fail(new Error(`Docker ${args[0]} exceeded its deadline`)), COMMAND_TIMEOUT_MS)
    const collect = (chunks: Buffer[], chunk: Buffer): void => {
      size += chunk.byteLength
      if (size > COMMAND_OUTPUT_BYTES) fail(new Error("Docker management output exceeded its limit"))
      else chunks.push(chunk)
    }
    child.stdout.on("data", (chunk: Buffer) => collect(stdout, chunk))
    child.stderr.on("data", (chunk: Buffer) => collect(stderr, chunk))
    child.once("error", fail)
    child.once("close", (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ exitCode: code ?? 1, stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") })
    })
  })
}

async function imageIdentity(runtime: string, imageRef: string): Promise<ImageIdentity> {
  const result = await command(runtime, ["image", "inspect", imageRef])
  if (result.exitCode !== 0) throw new Error("The pinned worker image is not available in the configured world")
  const image = parseDockerImage(result.stdout)
  if (!OCI_CONFIG_ID.test(image.Id)) throw new Error("Docker returned an invalid image config ID")
  if (OCI_CONFIG_ID.test(imageRef) && image.Id !== imageRef) throw new Error("Docker inspect image ID does not match the requested local config ID")
  return { imageId: image.Id, platform: `${image.Os}/${image.Architecture}`, variant: image.Variant ?? "" }
}

async function inspectContainer(runtime: string, id: string): Promise<{ identity: ContainerIdentity; record: DockerContainerInspect } | null> {
  const result = await command(runtime, ["container", "inspect", id])
  if (result.exitCode !== 0) {
    if (/No such (?:container|object):/.test(result.stderr)) return null
    throw new Error("Docker could not establish container presence")
  }
  const inspected = parseDockerContainer(result.stdout)
  return { identity: {
    containerId: inspected.Id, imageId: inspected.Image,
    imageRef: inspected.Config.Image, state: inspected.State.Status,
    ownerRef: inspected.Config.Labels[OWNER_LABEL],
    executionId: inspected.Config.Labels[EXECUTION_LABEL],
    executionToken: inspected.Config.Labels[TOKEN_LABEL],
  }, record: inspected }
}

function requireOwner(identity: ContainerIdentity, input: AuthorWorkerLaunchInputV1): void {
  if (identity.ownerRef !== input.ownerRef || identity.executionId !== input.executionId || identity.executionToken !== input.executionToken) throw new Error("Container ownership does not match its retained intent")
}
function requireContainment(container: DockerContainerInspect, input: AuthorWorkerLaunchInputV1): void {
  const host = container.HostConfig
  if (host.Privileged || !host.ReadonlyRootfs || host.NetworkMode !== "none" || container.Config.User !== "65532:65532" || !host.CapDrop?.includes("ALL") || !host.SecurityOpt?.some(option => option === "no-new-privileges" || option === "no-new-privileges:true")) throw new Error("Docker did not enforce the worker security profile")
  const memory = input.profile?.memoryBytes ?? 64 * 1024 * 1024
  if (host.Memory !== memory || host.MemorySwap !== memory || host.PidsLimit !== (input.profile?.processCount ?? 1) || host.NanoCpus !== (input.profile?.cpuCount ?? 1) * 1_000_000_000) throw new Error("Docker did not enforce worker resource limits")
  const mount = container.Mounts[0]
  if (container.Mounts.length !== 1 || !mount || mount.Type !== "bind" || mount.Source !== input.capturedStagingRoot || mount.Destination !== "/breadboard-captured" || mount.RW) throw new Error("Worker has undeclared mounts")
  const tmpfs = host.Tmpfs
  if (!tmpfs || Object.keys(tmpfs).length !== 1 || !tmpfs["/tmp"]?.split(",").includes(`size=${input.profile?.scratchBytes ?? 8 * 1024 * 1024}`)) throw new Error("Worker scratch differs from its admitted bound")
}

async function cleanup(runtime: string, reference: string, expectedId: string | null, input: AuthorWorkerLaunchInputV1, resourceId: string, reason: string): Promise<AuthorWorkerCleanupResultV1> {
  const result = (status: "confirmed_absent" | "unknown", evidence: string[]): AuthorWorkerCleanupResultV1 => ({ status, resourceId, containerId: expectedId ?? reference, ownerRef: input.ownerRef, reason, evidence })
  try {
    const observed = await inspectContainer(runtime, reference)
    if (observed === null) return result("confirmed_absent", ["container_absence_observed"])
    requireOwner(observed.identity, input)
    if (expectedId !== null && observed.identity.containerId !== expectedId) return result("unknown", ["container_identity_changed"])
    const removed = await command(runtime, ["rm", "--force", "--volumes", observed.identity.containerId])
    if (removed.exitCode !== 0) return result("unknown", ["container_removal_failed"])
    return await inspectContainer(runtime, observed.identity.containerId) === null
      ? result("confirmed_absent", ["owned_container_removed", "container_absence_observed"])
      : result("unknown", ["container_still_present"])
  } catch (error) {
    return result("unknown", [error instanceof Error ? error.message : "container_cleanup_unknown"])
  }
}


export class OciAuthorChannel implements AuthorWorkerChannelV1 {
  readonly channelId: string
  private readonly iterator: AsyncIterator<Buffer>
  private readonly decoder = new AuthorFrameReader()
  private readonly exited: Promise<void>
  private closePromise: Promise<AuthorWorkerCleanupResultV1> | null = null
  private stderrBytes = 0
  private readonly stderr: Buffer[] = []
  private readInFlight = false
  private writeInFlight = false
  private constructor(
    private readonly runtime: string,
    private readonly process: ChildProcessWithoutNullStreams,
    private readonly input: AuthorWorkerLaunchInputV1,
    readonly identity: AuthorWorkerResourceIdentityV1,
  ) {
    this.channelId = identity.resourceId
    this.iterator = process.stdout[Symbol.asyncIterator]()
    this.exited = new Promise(resolve => process.once("close", () => resolve()))
    process.stderr.on("data", (chunk: Buffer) => {
      const available = Math.max(0, COMMAND_OUTPUT_BYTES - this.stderrBytes)
      if (available !== 0) this.stderr.push(chunk.subarray(0, available))
      this.stderrBytes += chunk.byteLength
    })
  }

  static async open(input: AuthorWorkerLaunchInputV1, options: OciAuthorChannelOptions): Promise<OciAuthorChannel> {
    assertLaunchProfile(input)
    if (!options.onIntent || !options.onReceipt) throw new Error("Author launch requires durable intent and receipt callbacks")
    await validateStaging(input)
    const runtime = options.runtimeCommand ?? "docker"
    const token = createHash("sha256").update(`${input.ownerRef}\0${input.executionId}\0${input.executionToken}`).digest("hex")
    const name = `bb-author-${token.slice(0, 40)}`
    const resourceId = `${runtime}:${name}`
    await options.onIntent({ ownerRef: input.ownerRef, executionId: input.executionId, resourceId, containerName: name })
    let containerId: string | null = null
    try {
      const image = await imageIdentity(runtime, input.imageRef)
      if (input.platform !== image.platform && input.platform !== `${image.platform}/${image.variant}`) throw new Error("Pinned image does not match the admitted platform")
      if (await inspectContainer(runtime, name) !== null) throw new Error("Execution already owns a container; reconcile it instead of relaunching")
      const created = await command(runtime, createArgs(input, name))
      if (created.exitCode !== 0) throw new Error(`Docker author create failed: ${created.stderr.trim()}`)
      containerId = created.stdout.trim()
      if (!/^[0-9a-f]{64}$/.test(containerId)) throw new Error("Docker returned an invalid container identity")
      const observed = await inspectContainer(runtime, containerId)
      if (observed === null) throw new Error("Created author container is absent")
      requireOwner(observed.identity, input)
      if (observed.identity.containerId !== containerId || observed.identity.imageId !== image.imageId || observed.identity.imageRef !== input.imageRef) throw new Error("Receiver image differs from the pinned closure")
      requireContainment(observed.record, input)
      const identity: AuthorWorkerResourceIdentityV1 = { resourceId, ownerRef: input.ownerRef, executionId: input.executionId, containerId, containerName: name, imageId: image.imageId, imageRef: input.imageRef, platform: input.platform, receiverIdentity: `${containerId}:${image.imageId}`, state: observed.identity.state }
      await options.onReceipt(identity)
      const process = spawn(runtime, ["start", "--attach", "--interactive", containerId], { stdio: ["pipe", "pipe", "pipe"], env: dockerManagementEnvironment() })
      const channel = new OciAuthorChannel(runtime, process, input, identity)
      await new Promise<void>((resolve, reject) => { process.once("spawn", resolve); process.once("error", reject) })
      return channel
    } catch (error) {
      const settled: AuthorWorkerCleanupResultV1 = containerId === null
        ? { status: "unknown", resourceId, containerId: name, ownerRef: input.ownerRef, reason: "launch_failed", evidence: ["no_new_container_identity_observed"] }
        : await cleanup(runtime, containerId, containerId, input, resourceId, "launch_failed")
      throw new AuthorWorkerLaunchError(error instanceof Error ? error.message : "Author launch failed", settled)
    }
  }

  async readFrame(signal?: AbortSignal): Promise<Uint8Array | null> {
    if (this.readInFlight) throw new Error("Concurrent reads from one author channel are not permitted")
    this.readInFlight = true
    const abort = (): void => { this.process.stdout.destroy(new Error("Author channel read cancelled")) }
    signal?.addEventListener("abort", abort, { once: true })
    try {
      if (signal?.aborted) throw signal.reason ?? new Error("Author channel read cancelled")
      for (;;) {
        const body = this.decoder.take()
        if (body !== null) return body
        const next = await this.iterator.next()
        if (next.done) { this.decoder.finish(); return null }
        this.decoder.append(next.value)
      }
    } finally {
      signal?.removeEventListener("abort", abort)
      this.readInFlight = false
    }
  }

  async writeFrame(body: Uint8Array, signal?: AbortSignal): Promise<void> {
    if (this.writeInFlight || this.closePromise) throw new Error("Author channel is busy or closing")
    if (signal?.aborted) throw signal.reason ?? new Error("Author channel write cancelled")
    this.writeInFlight = true
    const abort = (): void => { this.process.stdin.destroy(new Error("Author channel write cancelled")) }
    signal?.addEventListener("abort", abort, { once: true })
    try {
      const frame = encodeAuthorFrame(body)
      await new Promise<void>((resolve, reject) => {
        const failed = (error: Error): void => { this.process.stdin.removeListener("error", failed); reject(error) }
        this.process.stdin.once("error", failed)
        this.process.stdin.write(frame, error => {
          this.process.stdin.removeListener("error", failed)
          if (error) reject(error)
          else resolve()
        })
      })
    } finally {
      signal?.removeEventListener("abort", abort)
      this.writeInFlight = false
    }
  }
  cancel(reason: string): Promise<AuthorWorkerCleanupResultV1> { return this.close(`cancelled:${reason}`) }
  close(reason = "closed"): Promise<AuthorWorkerCleanupResultV1> {
    if (this.closePromise) return this.closePromise
    this.closePromise = this.dispose(reason).then(result => {
      if (result.status === "unknown") this.closePromise = null
      return result
    })
    return this.closePromise
  }
  private async dispose(reason: string): Promise<AuthorWorkerCleanupResultV1> {
    const result = await cleanup(this.runtime, this.identity.containerId, this.identity.containerId, this.input, this.identity.resourceId, reason)
    this.process.stdin.destroy()
    this.process.stdout.destroy()
    if (this.process.exitCode === null && this.process.signalCode === null) this.process.kill("SIGTERM")
    let timer: ReturnType<typeof setTimeout> | undefined
    const exited = await Promise.race([this.exited.then(() => true), new Promise<boolean>(resolve => { timer = setTimeout(() => resolve(false), COMMAND_TIMEOUT_MS) })])
    clearTimeout(timer)
    if (!exited) {
      this.process.kill("SIGKILL")
      return { ...result, status: "unknown", evidence: [...result.evidence, "docker_attach_exit_unconfirmed"] }
    }
    return result
  }
}

export function openOciAuthorChannel(input: AuthorWorkerLaunchInputV1, options: OciAuthorChannelOptions): Promise<OciAuthorChannel> {
  return OciAuthorChannel.open(input, options)
}
