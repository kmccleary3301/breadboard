import { createHash, randomBytes } from "node:crypto"
import { execFile } from "node:child_process"
import { posix as posixPath } from "node:path"
import { promisify } from "node:util"

import { assertValid, type SandboxRequestV1, type SandboxResultV1 } from "@breadboard/kernel-contracts"
import {
  buildAndPersistCanonicalSandboxEvidence,
  canonicalProcessExitCode,
} from "@breadboard/execution-drivers"
import {
  canonicalScheduledRequestKey,
  type ScheduledExecutionBackendV1,
  type ScheduledExecutionObservationV1,
} from "./scheduled.js"

export interface CommandResultV1 {
  readonly stdout: string
  readonly stderr: string
}

export interface CommandRunOptionsV1 {
  readonly signal?: AbortSignal
  readonly timeoutMs: number
  readonly maxOutputBytes: number
}

export type CommandRunnerV1 = (
  program: string,
  args: readonly string[],
  options: CommandRunOptionsV1,
) => Promise<CommandResultV1>

export interface SshSlurmBackendOptionsV1 {
  readonly sshTarget: string
  readonly remoteEvidenceDirectory: string
  readonly sshProgram?: string
  readonly runCommand?: CommandRunnerV1
  readonly commandTimeoutMs?: number
  readonly maxOutputBytes?: number
  readonly resourceProfile?: Partial<SlurmResourceProfileV1>
}

export interface SlurmResourceProfileV1 {
  readonly cpuCount: number
  readonly memoryBytes: number
  readonly timeSeconds: number
  readonly gpuCount: 0
}

const DEFAULT_RESOURCE_PROFILE: SlurmResourceProfileV1 = {
  cpuCount: 1,
  memoryBytes: 128 * 1024 * 1024,
  timeSeconds: 120,
  gpuCount: 0,
}

function resourceProfile(
  value: Partial<SlurmResourceProfileV1> | undefined,
): SlurmResourceProfileV1 {
  if (Object.keys(value ?? {}).some((key) => !["cpuCount", "memoryBytes", "timeSeconds", "gpuCount"].includes(key))) {
    throw new Error("Slurm resource profile contains an unsupported field")
  }
  const profile = { ...DEFAULT_RESOURCE_PROFILE, ...(value ?? {}) }
  if (!Number.isSafeInteger(profile.cpuCount) || profile.cpuCount < 1 || profile.cpuCount > 2) {
    throw new Error("Slurm cpuCount must be between 1 and 2")
  }
  if (!Number.isSafeInteger(profile.memoryBytes) || profile.memoryBytes < 30 * 1024 * 1024 || profile.memoryBytes > 256 * 1024 * 1024) {
    throw new Error("Slurm memoryBytes must be between 30 and 256 MiB")
  }
  if (!Number.isSafeInteger(profile.timeSeconds) || profile.timeSeconds < 1 || profile.timeSeconds > 120) {
    throw new Error("Slurm timeSeconds must be between 1 and 120")
  }
  if (profile.gpuCount !== 0) throw new Error("Slurm gpuCount must be zero")
  return profile
}

interface SubmittedSlurmExecution {
  readonly request: SandboxRequestV1
  readonly stdoutPath: string
  readonly requestDigest: string
  observedRunning?: boolean
  readonly stderrPath: string
  readonly resourceProfile: SlurmResourceProfileV1
  readonly attemptIdentity: string
  restartCount?: number
}

const execFileAsync = promisify(execFile)

async function defaultRunCommand(
  program: string,
  args: readonly string[],
  options: CommandRunOptionsV1,
): Promise<CommandResultV1> {
  const result = await execFileAsync(program, args, {
    maxBuffer: options.maxOutputBytes,
    signal: options.signal,
    timeout: options.timeoutMs,
    killSignal: "SIGKILL",
  })
  return { stdout: result.stdout, stderr: result.stderr }
}

function requireSafeValue(value: string, name: string): string {
  if (!value.trim() || value.includes("\0") || value.includes("\n") || value.includes("\r")) {
    throw new Error(`${name} must be a non-empty single-line value`)
  }
  return value
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

function remotePath(directory: string, name: string): string {
  return `${directory.replace(/\/+$/, "")}/${name}`
}

function uriPath(path: string): string {
  return path.split("/").map((part) => encodeURIComponent(part)).join("/")
}

function sha256(value: string): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`
}

function slurmTimeLimit(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return `00:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
}

function memoryBytes(value: string): number | null {
  const match = /^(\d+)([KMGTP]?)$/i.exec(value)
  if (!match) return null
  const amount = Number.parseInt(match[1]!, 10)
  const power = "KMGTP".indexOf((match[2] ?? "").toUpperCase()) + 1
  return Number.isSafeInteger(amount) ? amount * 1024 ** power : null
}

function slurmDurationSeconds(value: string): number | null {
  const match = /^(?:(\d+)-)?(\d{1,2}):(\d{2}):(\d{2})$/.exec(value)
  if (!match) return null
  const days = Number.parseInt(match[1] ?? "0", 10)
  const hours = Number.parseInt(match[2]!, 10)
  const minutes = Number.parseInt(match[3]!, 10)
  const seconds = Number.parseInt(match[4]!, 10)
  const total = days * 86_400 + hours * 3_600 + minutes * 60 + seconds
  return Number.isSafeInteger(total) ? total : null
}

function gpuResourcesAreZero(
  requestedTres: string,
  allocatedTres: string,
  schedulerRecord: string,
): boolean {
  for (const token of `${requestedTres},${allocatedTres}`.split(",")) {
    if (!/gpu/i.test(token)) continue
    const countMatch = /=(\d+)$/.exec(token)
    const count = countMatch ? Number.parseInt(countMatch[1]!, 10) : null
    if (count === null || !Number.isSafeInteger(count) || count !== 0) return false
  }
  const gres = /\bGres=([^\s]+)/i.exec(schedulerRecord)?.[1]
  if (!gres || /^(?:\(null\)|N\/A|None)$/i.test(gres)) return true
  for (const token of gres.split(",")) {
    if (!/gpu/i.test(token)) continue
    const countMatch = /:(\d+)$/.exec(token)
    const count = countMatch ? Number.parseInt(countMatch[1]!, 10) : null
    if (count === null || !Number.isSafeInteger(count) || count !== 0) return false
  }
  return true
}

function slurmJobName(
  sshTarget: string,
  evidenceDirectory: string,
  requestId: string,
): string {
  const ownershipKey = sha256(`${sshTarget}\0${evidenceDirectory}\0${requestId}`)
    .slice("sha256:".length)
  return `bb-${ownershipKey.slice(0, 32)}`
}


function requestDigest(request: SandboxRequestV1): string {
  return sha256(canonicalScheduledRequestKey(request))
}

function durableExecutionHandle(jobId: string, digest: string): string {
  return `${jobId}@${digest.slice("sha256:".length)}`
}

function slurmArtifactStem(jobId: string, digest: string): string {
  const digestHex = digest.slice("sha256:".length)
  if (!/^[0-9a-f]{64}$/.test(digestHex)) {
    throw new Error("invalid Slurm request digest")
  }
  return `slurm-${digestHex}-${jobId}`
}

function parseExecutionHandle(value: string): {
  readonly jobId: string
  readonly expectedRequestDigest: string | null
} {
  const match = /^(\d+)(?:@([0-9a-f]{64}))?$/.exec(value)
  if (!match) throw new Error("invalid Slurm execution id")
  return {
    jobId: match[1]!,
    expectedRequestDigest: match[2] ? `sha256:${match[2]}` : null,
  }
}


function retainedSubmission(
  request: SandboxRequestV1,
  profile: SlurmResourceProfileV1,
  attemptIdentity: string,
): string {
  return JSON.stringify({
    request,
    requestDigest: requestDigest(request),
    resourceProfile: profile,
    attemptIdentity,
  })
}

function genericFailureReason(status: SandboxResultV1["status"]): string {
  if (status === "timed_out") return "execution_timed_out"
  if (status === "cancelled") return "execution_cancelled"
  return "execution_failed"
}

async function resultFor(
  execution: SubmittedSlurmExecution,
  status: SandboxResultV1["status"],
  exitCode: number | null,
  stdout: string,
  stderr: string,
): Promise<SandboxResultV1> {
  const evidence = await buildAndPersistCanonicalSandboxEvidence({
    command: execution.request.command,
    status,
    exitCode,
    stdout,
    stderr,
    evidenceMode: execution.request.evidence_mode,
  })
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: execution.request.request_id,
    status,
    ...evidence,
    error: status === "completed" ? null : { reason: genericFailureReason(status) },
  }
}

function classifyState(
  state: string,
): Exclude<ScheduledExecutionObservationV1["state"], "accepted"> {
  const base = state.split(/[ +]/, 1)[0]?.toUpperCase() ?? ""
  if (["PENDING", "CONFIGURING", "REQUEUED", "RESIZING"].includes(base)) return "running"
  if (["RUNNING", "COMPLETING", "STAGE_OUT", "SUSPENDED"].includes(base)) return "running"
  if (base === "COMPLETED") return "completed"
  if (["CANCELLED", "PREEMPTED", "REVOKED"].includes(base)) return "cancelled"
  if (["TIMEOUT", "DEADLINE"].includes(base)) return "timed_out"
  return "failed"
}

function schedulerStateProvesExecution(state: string): boolean {
  const base = state.split(/[ +]/, 1)[0]?.toUpperCase() ?? ""
  return ["RUNNING", "COMPLETING", "STAGE_OUT", "SUSPENDED"].includes(base)
}

function attemptEvidenceRef(
  durableId: string,
  execution: SubmittedSlurmExecution,
): string {
  const attemptRef = `slurm://job/${durableId}/attempt/${encodeURIComponent(execution.attemptIdentity)}`
  return execution.restartCount === undefined
    ? attemptRef
    : `${attemptRef}/restart/${execution.restartCount}`
}

function schedulerEvidenceRefs(
  sshTarget: string,
  executionId: string,
  execution: SubmittedSlurmExecution,
  schedulerState: string,
  nodeList: string,
): string[] {
  const durableId = durableExecutionHandle(
    executionId,
    execution.requestDigest,
  )
  return [
    `slurm://job/${durableId}`,
    `slurm://job/${durableId}/state/${encodeURIComponent(schedulerState)}`,
    ...(nodeList && nodeList !== "(null)"
      ? [`slurm://job/${durableId}/node/${encodeURIComponent(nodeList)}`]
      : []),
    ...(execution.attemptIdentity === "legacy"
      ? []
      : [attemptEvidenceRef(durableId, execution)]),
    `ssh://${sshTarget}${uriPath(execution.stdoutPath)}`,
    `ssh://${sshTarget}${uriPath(execution.stderrPath)}`,
  ]
}

function parseExecution(
  encoded: string,
  evidenceDirectory: string,
  executionId: string,
): SubmittedSlurmExecution {
  let parsed: unknown
  try {
    parsed = JSON.parse(Buffer.from(encoded.trim(), "base64").toString("utf8"))
  } catch {
    throw new Error("Slurm submission metadata is invalid")
  }
  const retained = typeof parsed === "object" && parsed !== null
    && "request" in parsed && "requestDigest" in parsed
    ? parsed as {
      readonly request: unknown
      readonly requestDigest: unknown
      readonly resourceProfile?: Partial<SlurmResourceProfileV1>
      readonly attemptIdentity?: unknown
    }
    : null
  let request: SandboxRequestV1
  try {
    request = assertValid<SandboxRequestV1>(
      "sandboxRequest",
      retained?.request ?? parsed,
    )
  } catch {
    throw new Error("Slurm submission metadata is invalid")
  }
  const digest = requestDigest(request)
  if (retained !== null && retained.requestDigest !== digest) {
    throw new Error("Slurm submission metadata digest is invalid")
  }
  const profile = resourceProfile(retained?.resourceProfile)
  const retainedAttemptIdentity = retained?.attemptIdentity
  if (
    retainedAttemptIdentity !== undefined
    && (
      typeof retainedAttemptIdentity !== "string"
      || (
        retainedAttemptIdentity !== "legacy"
        && !/^[0-9a-f]{32}$/.test(retainedAttemptIdentity)
      )
    )
  ) {
    throw new Error("Slurm submission attempt identity is invalid")
  }
  const attemptIdentity = retainedAttemptIdentity ?? "legacy"
  return {
    request,
    requestDigest: digest,
    resourceProfile: profile,
    attemptIdentity,
    stdoutPath: remotePath(evidenceDirectory, `${slurmArtifactStem(executionId, digest)}.out`),
    stderrPath: remotePath(evidenceDirectory, `${slurmArtifactStem(executionId, digest)}.err`),
  }
}

export function makeSshSlurmBackend(
  options: SshSlurmBackendOptionsV1,
): ScheduledExecutionBackendV1 {
  const sshTarget = requireSafeValue(options.sshTarget, "sshTarget")
  if (sshTarget.startsWith("-")) {
    throw new Error("sshTarget must not begin with an option prefix")
  }
  const rawEvidenceDirectory = requireSafeValue(
    options.remoteEvidenceDirectory,
    "remoteEvidenceDirectory",
  )
  if (!rawEvidenceDirectory.startsWith("/")) {
    throw new Error("remoteEvidenceDirectory must be an absolute path")
  }
  const evidenceDirectory = posixPath.normalize(rawEvidenceDirectory).replace(/\/+$/, "") || "/"
  const launchCommandMaxBytes = 64 * 1024
  if (evidenceDirectory === "/") {
    throw new Error("remoteEvidenceDirectory must not be the filesystem root")
  }
  const admittedProfile = resourceProfile(options.resourceProfile)
  const enforceResourceProfile = options.resourceProfile !== undefined
  const sshProgram = options.sshProgram ?? "ssh"
  const runCommand = options.runCommand ?? defaultRunCommand
  const maxOutputBytes = options.maxOutputBytes ?? 4 * 1024 * 1024
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1) {
    throw new Error("maxOutputBytes must be a positive safe integer")
  }
  const framedOutputMaxBytes = Math.ceil(maxOutputBytes / 3) * 4 + 64
  const receiptOutputMaxBytes = 1024
  const controlOutputMaxBytes = 64 * 1024
  const metadataOutputMaxBytes = 8 * 1024 * 1024
  if (!Number.isSafeInteger(framedOutputMaxBytes)) {
    throw new Error("maxOutputBytes is too large for framed transport")
  }
  const commandTimeoutMs = options.commandTimeoutMs ?? 30_000
  if (!Number.isSafeInteger(commandTimeoutMs) || commandTimeoutMs < 1) {
    throw new Error("commandTimeoutMs must be a positive safe integer")
  }
  const submitted = new Map<string, SubmittedSlurmExecution>()
  const commandTimeoutSeconds = Math.max(1, Math.ceil(commandTimeoutMs / 1_000))
  const lockLeaseSeconds = commandTimeoutSeconds * 2 + 5

  async function ssh(
    remoteCommand: string,
    timeoutMs = commandTimeoutMs,
    signal?: AbortSignal,
    outputLimitBytes = controlOutputMaxBytes,
  ): Promise<CommandResultV1> {
    return runCommand(
      sshProgram,
      [sshTarget, remoteCommand],
      {
        timeoutMs: Math.max(1, Math.min(commandTimeoutMs, timeoutMs)),
        maxOutputBytes: outputLimitBytes,
        signal,
      },
    )
  }
  async function verifySlurmAllocation(
    jobId: string,
    profile: SlurmResourceProfileV1,
    releaseHeld: boolean,
    signal?: AbortSignal,
  ): Promise<number> {
    const output = (await ssh(
      `scontrol show job -o ${shellQuote(jobId)}`,
      commandTimeoutMs,
      signal,
    )).stdout.trim()
    const allocatedCpuMatch = /\bNumCPUs=(\d+)\b/.exec(output)
    const requestedTresMatch = /\bReqTRES=([^\s]+)/.exec(output)
    const allocatedTresMatch = /\bAllocTRES=([^\s]+)/.exec(output)
    const requestedTres = requestedTresMatch?.[1] ?? ""
    const allocatedTres = allocatedTresMatch?.[1] ?? ""
    const requestedCpuMatch = /(?:^|,)cpu=(\d+)(?:,|$)/.exec(requestedTres)
    const nodeMemoryMatch = /\bMinMemoryNode=(\d+[KMGTP]?)\b/i.exec(output)
    const cpuMemoryMatch = /\bMinMemoryCPU=(\d+[KMGTP]?)\b/i.exec(output)
    const timeMatch = /\bTimeLimit=([0-9:-]+)\b/.exec(output)
    const restartMatch = /\bRestarts=(\d+)\b/.exec(output)
    const requeueMatch = /\bRequeue=(\d+)\b/.exec(output)
    const stateMatch = /\bJobState=([A-Z_]+)\b/.exec(output)
    const reasonMatch = /\bReason=([A-Za-z0-9_]+)\b/.exec(output)
    const allocatedCpus = allocatedCpuMatch ? Number.parseInt(allocatedCpuMatch[1]!, 10) : null
    const requestedCpus = requestedCpuMatch ? Number.parseInt(requestedCpuMatch[1]!, 10) : null
    const perNode = nodeMemoryMatch ? memoryBytes(nodeMemoryMatch[1]!) : null
    const perCpu = cpuMemoryMatch ? memoryBytes(cpuMemoryMatch[1]!) : null
    const allocatedMemory = perNode ?? (perCpu === null || allocatedCpus === null ? null : perCpu * allocatedCpus)
    const requestedMemory = Math.ceil(profile.memoryBytes / (1024 * 1024)) * 1024 * 1024
    const timeLimitSeconds = timeMatch ? slurmDurationSeconds(timeMatch[1]!) : null
    const restartCount = restartMatch
      ? Number.parseInt(restartMatch[1]!, 10)
      : null
    const gpuAllocated = !gpuResourcesAreZero(
      requestedTres,
      allocatedTres,
      output,
    )
    if (
      requestedTresMatch === null
      || allocatedTresMatch === null
      || requestedCpus !== profile.cpuCount
      || allocatedCpus === null
      || allocatedCpus < profile.cpuCount
      || allocatedCpus > 2
      || allocatedMemory === null
      || allocatedMemory < requestedMemory
      || allocatedMemory > 256 * 1024 * 1024
      || timeLimitSeconds === null
      || timeLimitSeconds < profile.timeSeconds
      || timeLimitSeconds > 120
      || restartCount === null
      || !Number.isSafeInteger(restartCount)
      || gpuAllocated
      || requeueMatch?.[1] !== "1"
      || (releaseHeld && stateMatch?.[1] !== "PENDING")
      || (releaseHeld && reasonMatch?.[1] !== "JobHeldUser")
    ) {
      throw new Error("Slurm scheduler allocation does not match the admitted resource profile")
    }
    if (releaseHeld) {
      await ssh(`scontrol release ${shellQuote(jobId)}`, commandTimeoutMs, signal)
    }
    return restartCount
  }

  function assertConfiguredResourceProfile(
    execution: SubmittedSlurmExecution,
  ): void {
    if (!enforceResourceProfile) return
    if (
      execution.attemptIdentity === "legacy"
      || execution.resourceProfile.cpuCount !== admittedProfile.cpuCount
      || execution.resourceProfile.memoryBytes !== admittedProfile.memoryBytes
      || execution.resourceProfile.timeSeconds !== admittedProfile.timeSeconds
      || execution.resourceProfile.gpuCount !== admittedProfile.gpuCount
    ) {
      throw new Error("Slurm retained submission does not match the configured resource profile")
    }
  }

  function recordRestartCount(
    execution: SubmittedSlurmExecution,
    restartCount: number,
  ): void {
    if (
      !Number.isSafeInteger(restartCount)
      || restartCount < 0
      || restartCount < (execution.restartCount ?? 0)
    ) {
      throw new Error("Slurm restart count is invalid or moved backwards")
    }
    execution.restartCount = restartCount
  }

  function verifySlurmAccounting(
    profile: SlurmResourceProfileV1,
    schedulerState: string,
    observedRunning: boolean,
    allocatedCpuText: string,
    requestedMemoryText: string,
    timeLimitText: string,
    restartText: string,
    requestedTres: string,
    allocatedTres: string,
  ): number {
    const allocatedCpus = /^\d+$/.test(allocatedCpuText)
      ? Number.parseInt(allocatedCpuText, 10)
      : null
    const requestedCpuMatch = /(?:^|,)cpu=(\d+)(?:,|$)/.exec(requestedTres)
    const requestedCpus = requestedCpuMatch
      ? Number.parseInt(requestedCpuMatch[1]!, 10)
      : null
    const memoryMatch = /^(\d+[KMGTP]?)([cn]?)$/i.exec(requestedMemoryText)
    const memoryUnitBytes = memoryMatch ? memoryBytes(memoryMatch[1]!) : null
    const requestedReservationMemory =
      memoryUnitBytes === null || requestedCpus === null
        ? null
        : memoryMatch?.[2]?.toLowerCase() === "c"
          ? memoryUnitBytes * requestedCpus
          : memoryUnitBytes
    const allocatedTresHasMemory = /(?:^|,)mem=/.test(allocatedTres)
    const allocatedTresMemoryMatch =
      /(?:^|,)mem=(\d+[KMGTP]?)(?:,|$)/i.exec(allocatedTres)
    const allocatedTresMemory = allocatedTresMemoryMatch
      ? memoryBytes(allocatedTresMemoryMatch[1]!)
      : null
    const allocatedMemory =
      allocatedTresMemory ?? requestedReservationMemory
    const requestedMemory =
      Math.ceil(profile.memoryBytes / (1024 * 1024)) * 1024 * 1024
    const timeLimitSeconds = slurmDurationSeconds(timeLimitText)
    const restartCount = /^\d+$/.test(restartText)
      ? Number.parseInt(restartText, 10)
      : null
    const hasAllocation =
      allocatedCpus !== null
      && Number.isSafeInteger(allocatedCpus)
      && allocatedCpus > 0
    const allocationIsValid = hasAllocation
      ? allocatedCpus >= profile.cpuCount
        && allocatedCpus <= 2
        && allocatedTres.length > 0
        && !(allocatedTresHasMemory && allocatedTresMemory === null)
        && allocatedMemory !== null
        && Number.isSafeInteger(allocatedMemory)
        && allocatedMemory >= requestedMemory
        && allocatedMemory <= 256 * 1024 * 1024
        && gpuResourcesAreZero("", allocatedTres, "")
      : allocatedCpus === 0
        && !observedRunning
        && /^(?:CANCELLED|DEADLINE)/.test(schedulerState)
        && gpuResourcesAreZero("", allocatedTres, "")
    if (
      requestedCpus !== profile.cpuCount
      || requestedReservationMemory === null
      || !Number.isSafeInteger(requestedReservationMemory)
      || requestedReservationMemory !== requestedMemory
      || timeLimitSeconds !== profile.timeSeconds
      || restartCount === null
      || !Number.isSafeInteger(restartCount)
      || !requestedTres
      || !gpuResourcesAreZero(requestedTres, "", "")
      || !allocationIsValid
    ) {
      throw new Error("Slurm accounting does not match the admitted resource profile")
    }
    return restartCount
  }

  async function loadExecution(
    executionId: string,
    timeoutMs = commandTimeoutMs,
    signal?: AbortSignal,
  ): Promise<SubmittedSlurmExecution> {
    const identity = parseExecutionHandle(executionId)
    if (identity.expectedRequestDigest === null) {
      throw new Error("Slurm execution handle is not bound to a request digest")
    }
    const cached = submitted.get(identity.jobId)
    const metadataPath = remotePath(
      evidenceDirectory,
      `${slurmArtifactStem(identity.jobId, identity.expectedRequestDigest)}.request.b64`,
    )
    let metadata: CommandResultV1
    try {
      metadata = await ssh(
        `cat ${shellQuote(metadataPath)}`,
        timeoutMs,
        signal,
        metadataOutputMaxBytes,
      )
    } catch (error: unknown) {
      if (
        cached
        && identity.expectedRequestDigest !== null
        && cached.requestDigest === identity.expectedRequestDigest
      ) {
        return cached
      }
      throw error
    }
    const execution = parseExecution(
      metadata.stdout,
      evidenceDirectory,
      identity.jobId,
    )
    if (
      identity.expectedRequestDigest !== null
      && execution.requestDigest !== identity.expectedRequestDigest
    ) {
      throw new Error("Slurm execution handle no longer owns the scheduler job id")
    }
    if (cached && cached.requestDigest !== execution.requestDigest) {
      throw new Error("Slurm scheduler job identity changed")
    }
    execution.observedRunning = cached?.observedRunning
    execution.restartCount = cached?.restartCount
    submitted.set(identity.jobId, execution)
    return execution
  }

  return {
    backendId: `slurm:${sshTarget}`,
    async submit(request, context) {
      if (request.network_policy !== null && request.network_policy !== undefined) {
        throw new Error(
          "Slurm backend cannot enforce the requested network policy",
        )
      }
      if (request.image_ref !== null && request.image_ref !== undefined) {
        throw new Error(
          "Slurm backend cannot honor an image_ref without a configured container runtime",
        )
      }
      const expectedRequestDigest = requestDigest(request)
      const submissionKey = sha256(request.request_id).slice("sha256:".length)
      const jobName = slurmJobName(sshTarget, evidenceDirectory, request.request_id)
      const submissionAttemptToken = randomBytes(16).toString("hex")
      const receiptPath = remotePath(evidenceDirectory, `submission-${submissionKey}.receipt`)
      const cancelPath = remotePath(
        evidenceDirectory,
        `submission-${submissionKey}-${submissionAttemptToken}.cancel`,
      )
      const lockPath = remotePath(evidenceDirectory, `submission-${submissionKey}.lock`)
      const launchLogPath = remotePath(evidenceDirectory, `submission-${submissionKey}.log`)
      const submissionCommandPath = remotePath(
        evidenceDirectory,
        `submission-${submissionKey}.command.b64`,
      )
      const encodedRequest = Buffer.from(
        retainedSubmission(
          request,
          admittedProfile,
          enforceResourceProfile ? submissionAttemptToken : "legacy",
        ),
        "utf8",
      ).toString("base64")
      if (Buffer.byteLength(encodedRequest, "utf8") + 1 > metadataOutputMaxBytes) {
        throw new Error("Slurm retained request metadata exceeds the transport limit")
      }
      const metadataDigest = sha256(`${encodedRequest}\n`).slice("sha256:".length)
      const command = request.command.map(shellQuote).join(" ")
      const runtimeMetadataPrefix = remotePath(
        evidenceDirectory,
        `slurm-${expectedRequestDigest.slice("sha256:".length)}-`,
      )
      const verifiedCommand = (
        enforceResourceProfile
          ? [
            `metadata=${shellQuote(runtimeMetadataPrefix)}"$SLURM_JOB_ID"${shellQuote(".request.b64")}`,
            `actual=$(sha256sum "$metadata"); actual=\${actual%% *}`,
            `[ "$actual" = ${shellQuote(metadataDigest)} ] || exit 78`,
            `exec ${command}`,
          ]
          : [`exec ${command}`]
      ).join("; ")
      const submissionCommand = [
        `timeout ${commandTimeoutSeconds}s sbatch --parsable`,
        ...(enforceResourceProfile
          ? [
            "--hold",
            "--requeue",
            `--cpus-per-task=${admittedProfile.cpuCount}`,
            `--mem=${Math.ceil(admittedProfile.memoryBytes / (1024 * 1024))}M`,
            `--time=${slurmTimeLimit(admittedProfile.timeSeconds)}`,
          ]
          : []),
        `--job-name=${shellQuote(jobName)}`,
        `--output=${shellQuote(remotePath(evidenceDirectory, `${slurmArtifactStem("%j", expectedRequestDigest)}.out`))}`,
        ...(request.workspace_ref
          ? [`--chdir=${shellQuote(request.workspace_ref)}`]
          : []),
        `--error=${shellQuote(remotePath(evidenceDirectory, `${slurmArtifactStem("%j", expectedRequestDigest)}.err`))}`,
        `--wrap=${shellQuote(verifiedCommand)}`,
      ].join(" ")
      const encodedSubmissionCommand = Buffer.from(
        submissionCommand,
        "utf8",
      ).toString("base64")
      const cancelByNameScript = [
        "while true; do",
        `timeout 1s scancel --name ${shellQuote(jobName)} 2>/dev/null || true;`,
        "sleep 1;",
        "done",
      ].join(" ")
      const detachedScript = [
        "umask 077; set -eu;",
        `lock=${shellQuote(lockPath)};`,
        `receipt=${shellQuote(receiptPath)};`,
        `cancel=${shellQuote(cancelPath)};`,
        `owner="$lock/owner";`,
        "id='';",
        `attempt=${submissionAttemptToken};`,
        `digest=${expectedRequestDigest};`,
        `cancel_by_name() { touch "$cancel"; timeout ${commandTimeoutSeconds}s sh -c ${shellQuote(cancelByNameScript)} || true; };`,
        `cleanup() { if [ -f "$cancel" ] || [ ! -s "$receipt" ]; then cancel_by_name; active=$(timeout 1s squeue -h --name ${shellQuote(jobName)} -o '%j' 2>/dev/null) || return 0; [ -z "$active" ] || return 0; fi; rm -f "$cancel"; rm -rf "$lock"; };`,
        "trap cleanup EXIT;",
        `owner_start=$(awk '{print $22}' /proc/$$/stat 2>/dev/null || true);`,
        `printf '%s %s %s\\n' "$$" "$(date +%s)" "$owner_start" > "$owner.tmp";`,
        `mv "$owner.tmp" "$owner";`,
        `[ -s "$receipt" ] && exit 0;`,
        `[ ! -f "$cancel" ] || exit 75;`,
        `printf '%s\\n' ${shellQuote(encodedSubmissionCommand)} > ${shellQuote(`${submissionCommandPath}.tmp`)};`,
        `mv ${shellQuote(`${submissionCommandPath}.tmp`)} ${shellQuote(submissionCommandPath)};`,
        `job=$(${submissionCommand});`,
        `id=\${job%%;*};`,
        `case "$id" in ""|*[!0-9]*) exit 65;; esac;`,
        `printf '%s\\n' "$id" > "$lock/job.tmp";`,
        `mv "$lock/job.tmp" "$lock/job";`,
        `artifact_stem=${shellQuote(remotePath(evidenceDirectory, `slurm-${expectedRequestDigest.slice("sha256:".length)}-`))}"\${id}";`,
        `stdout="$artifact_stem.out";`,
        `stderr="$artifact_stem.err";`,
        `touch "$stdout" "$stderr"; chmod 600 "$stdout" "$stderr";`,
        `metadata="$artifact_stem.request.b64";`,
        `printf '%s\\n' ${shellQuote(encodedRequest)} > "$metadata.tmp";`,
        `mv "$metadata.tmp" "$metadata";`,
        `printf '%s\\n%s\\n%s\\n' "$job" "$attempt" "$digest" > "$receipt.tmp";`,
        `mv "$receipt.tmp" "$receipt";`,
        `[ ! -f ${shellQuote(cancelPath)} ] || timeout 1s scancel --name ${shellQuote(jobName)};`,
      ].join(" ")
      const launchCommand = [
        `umask 077; evidence=${shellQuote(evidenceDirectory)};`,
        `[ ! -L "$evidence" ] || exit 73;`,
        `mkdir -p "$evidence";`,
        `[ -d "$evidence" ] && [ "$(stat -c %u "$evidence")" = "$(id -u)" ] || exit 74;`,
        `chmod 700 "$evidence" || exit 75;`,
        `[ "$(stat -c %a "$evidence")" = "700" ] || exit 76;`,
        `lock=${shellQuote(lockPath)}; receipt=${shellQuote(receiptPath)}; now=$(date +%s);`,
        `if [ -d "$lock" ] && [ ! -s "$receipt" ]; then`,
        `lock_digest=$(cat "$lock/digest" 2>/dev/null || true);`,
        `[ "$lock_digest" = ${shellQuote(expectedRequestDigest)} ] || exit 77;`,
        `owner=$(cat "$lock/owner" 2>/dev/null || true);`,
        `pid=\${owner%% *}; owner_rest=\${owner#* }; created=\${owner_rest%% *}; owner_start=\${owner_rest#* };`,
        `case "$created" in ""|*[!0-9]*) created=$(stat -c %Y "$lock" 2>/dev/null || printf '0');; esac;`,
        `current_start=''; case "$pid" in ""|*[!0-9]*) :;; *) current_start=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true);; esac;`,
        `owner_live=0; case "$pid" in ""|*[!0-9]*) :;; *) if [ -d "/proc/$pid" ] && [ -n "$owner_start" ] && [ -n "$current_start" ] && [ "$current_start" = "$owner_start" ]; then owner_live=1; fi;; esac;`,
        `if [ $((now-created)) -ge ${lockLeaseSeconds} ] && [ "$owner_live" -eq 0 ]; then`,
        `if timeout 1s scancel --name ${shellQuote(jobName)} 2>/dev/null; then`,
        `active=$(timeout 1s squeue -h --name ${shellQuote(jobName)} -o '%j' 2>/dev/null) || active=unknown;`,
        `[ -z "$active" ] && rm -rf "$lock";`,
        `fi;`,
        `fi; fi;`,
        `if [ ! -s "$receipt" ] && mkdir "$lock" 2>/dev/null; then`,
        `owner_start=$(awk '{print $22}' /proc/$$/stat 2>/dev/null || true); printf '%s %s %s\\n' "$$" "$now" "$owner_start" > "$lock/owner";`,
        `printf '%s\n' ${shellQuote(submissionAttemptToken)} > "$lock/attempt";`,
        `printf '%s\n' ${shellQuote(expectedRequestDigest)} > "$lock/digest";`,
        `setsid sh -c ${shellQuote(detachedScript)}`,
        `</dev/null >${shellQuote(launchLogPath)} 2>&1 &`,
        "fi",
      ].join(" ")
      if (Buffer.byteLength(launchCommand, "utf8") > launchCommandMaxBytes) {
        throw new Error("Slurm submission launch command exceeds the safe argument limit")
      }
      const deadline = Math.min(
        Date.now() + commandTimeoutMs,
        context.deadlineAtMs ?? Number.POSITIVE_INFINITY,
      )
      let launchError: unknown
      try {
        await ssh(launchCommand, deadline - Date.now(), context.signal)
      } catch (error: unknown) {
        launchError = error
      }
      let receipt = ""
      while (!receipt && !context.signal.aborted && Date.now() < deadline) {
        try {
          receipt = (await ssh(
            `if [ -s ${shellQuote(receiptPath)} ]; then cat ${shellQuote(receiptPath)}; fi`,
            deadline - Date.now(),
            context.signal,
            receiptOutputMaxBytes,
          )).stdout.trim()
        } catch (error: unknown) {
          launchError = error
        }
        if (!receipt) await new Promise((resolve) => setTimeout(resolve, 25))
      }
      if (!receipt) {
        let cleanupError: unknown
        try {
          await ssh([
            `touch ${shellQuote(cancelPath)};`,
            `receipt_attempt=$(sed -n '2p' ${shellQuote(receiptPath)} 2>/dev/null || true);`,
            `lock_attempt=$(cat ${shellQuote(`${lockPath}/attempt`)} 2>/dev/null || true);`,
            `if [ "$receipt_attempt" = ${shellQuote(submissionAttemptToken)} ] || [ "$lock_attempt" = ${shellQuote(submissionAttemptToken)} ]; then`,
            `timeout ${commandTimeoutSeconds}s scancel --name ${shellQuote(jobName)} 2>/dev/null || true;`,
            "fi",
          ].join(" "), context.terminationGraceMs)
        } catch (error: unknown) {
          cleanupError = error
        }
        throw new Error(
          context.signal.aborted
            ? "Slurm submission was cancelled"
            : launchError
              ? "Slurm submission acknowledgement failed"
              : "Slurm submission acknowledgement timed out",
          { cause: cleanupError ?? launchError },
        )
      }
      const receiptLines = receipt.split(/\r?\n/, 3)
      const executionId = (receiptLines[0] ?? "").split(";", 1)[0] ?? ""
      const receiptAttemptToken = receiptLines[1] ?? ""
      const receiptRequestDigest = receiptLines[2] ?? ""
      if (!/^\d+$/.test(executionId)) {
        throw new Error("Slurm submission returned an invalid job id")
      }
      if (enforceResourceProfile && !/^[0-9a-f]{32}$/.test(receiptAttemptToken)) {
        throw new Error("Slurm receipt attempt identity is invalid")
      }
      if (submitted.get(executionId)?.requestDigest !== expectedRequestDigest) {
        submitted.delete(executionId)
      }
      let execution: SubmittedSlurmExecution
      try {
        execution = await loadExecution(
          durableExecutionHandle(executionId, expectedRequestDigest),
        )
      } catch (metadataError: unknown) {
        const receiptAuthorizesRecovery =
          receiptAttemptToken === submissionAttemptToken
          || receiptRequestDigest === expectedRequestDigest
        if (!receiptAuthorizesRecovery) {
          if (receiptRequestDigest) {
            throw new Error(
              "Slurm request_id collision; existing execution remains owned by its original request",
              { cause: metadataError },
            )
          }
          throw new Error(
            "Slurm execution metadata failed for a pre-existing receipt; job ownership is unproven",
            { cause: metadataError },
          )
        }
        execution = {
          request,
          requestDigest: expectedRequestDigest,
          resourceProfile: admittedProfile,
          attemptIdentity: enforceResourceProfile ? receiptAttemptToken : "legacy",
          stdoutPath: remotePath(
            evidenceDirectory,
            `${slurmArtifactStem(executionId, expectedRequestDigest)}.out`,
          ),
          stderrPath: remotePath(
            evidenceDirectory,
            `${slurmArtifactStem(executionId, expectedRequestDigest)}.err`,
          ),
        }
        submitted.set(executionId, execution)
      }
      if (execution.requestDigest !== expectedRequestDigest) {
        throw new Error(
          "Slurm request_id collision; existing execution remains owned by its original request",
        )
      }
      if (
        enforceResourceProfile
        && execution.attemptIdentity !== receiptAttemptToken
      ) {
        throw new Error("Slurm retained submission identity does not match its receipt")
      }
      assertConfiguredResourceProfile(execution)
      if (enforceResourceProfile && receiptAttemptToken === submissionAttemptToken) {
        try {
          recordRestartCount(
            execution,
            await verifySlurmAllocation(
              executionId,
              admittedProfile,
              true,
              context.signal,
            ),
          )
        } catch (error: unknown) {
          await ssh(
            `scancel ${shellQuote(executionId)} 2>/dev/null || true`,
            context.terminationGraceMs,
          ).catch(() => undefined)
          throw error
        }
      }
      return {
        executionId: durableExecutionHandle(executionId, execution.requestDigest),
        evidenceRefs: [
          `slurm://job/${durableExecutionHandle(executionId, execution.requestDigest)}/submitted`,
          ...(execution.attemptIdentity === "legacy"
            ? []
            : [attemptEvidenceRef(durableExecutionHandle(executionId, execution.requestDigest), execution)]),
          `ssh://${sshTarget}${uriPath(receiptPath)}`,
          `ssh://${sshTarget}${uriPath(submissionCommandPath)}`,
          `ssh://${sshTarget}${uriPath(launchLogPath)}`,
        ],
      }
    },
    async observe(executionId) {
      const identity = parseExecutionHandle(executionId)
      const execution = await loadExecution(executionId)
      assertConfiguredResourceProfile(execution)
      const expectedJobName = slurmJobName(
        sshTarget,
        evidenceDirectory,
        execution.request.request_id,
      )
      const activeResult = await ssh(
        `squeue -h -j ${shellQuote(identity.jobId)} -o '%j|%T|%N'`,
      )
      const [
        activeJobName = "",
        activeState = "",
        activeNodeList = "",
      ] = activeResult.stdout.trim().split("|")
      if ((activeJobName || activeState) && activeJobName !== expectedJobName) {
        throw new Error("Slurm execution handle no longer owns the scheduler job id")
      }
      if (activeState) {
        const state = classifyState(activeState)
        if (enforceResourceProfile) {
          recordRestartCount(
            execution,
            await verifySlurmAllocation(
              identity.jobId,
              admittedProfile,
              false,
            ),
          )
        }
        if (schedulerStateProvesExecution(activeState)) {
          execution.observedRunning = true
        }
        if (state === "running") {
          return {
            state,
            evidenceRefs: schedulerEvidenceRefs(
              sshTarget,
              identity.jobId,
              execution,
              activeState,
              activeNodeList,
            ),
          }
        }
      }
      const accountingFormat = enforceResourceProfile
        ? "JobName,State,ExitCode,NodeList,AllocCPUS,ReqMem,Timelimit,Restarts,ReqTRES,AllocTRES"
        : "JobName,State,ExitCode,NodeList"
      const result = await ssh(
        `sacct -X -j ${shellQuote(identity.jobId)} --noheader --parsable2 --format=${accountingFormat}`,
      )
      const records = result.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
      if (records.length === 0) {
        return { state: execution.observedRunning ? "running" : "accepted" }
      }
      const [
        accountingJobName = "",
        schedulerState = "UNKNOWN",
        exitCode = "",
        nodeList = "",
        allocatedCpus = "",
        requestedMemory = "",
        timeLimit = "",
        restartCount = "",
        requestedTres = "",
        allocatedTres = "",
      ] = records[0]!.split("|")
      if (accountingJobName !== expectedJobName) {
        throw new Error("Slurm execution handle no longer owns the scheduler job id")
      }
      if (enforceResourceProfile) {
        recordRestartCount(
          execution,
          verifySlurmAccounting(
            admittedProfile,
            schedulerState,
            execution.observedRunning === true,
            allocatedCpus,
            requestedMemory,
            timeLimit,
            restartCount,
            requestedTres,
            allocatedTres,
          ),
        )
      }
      const state = classifyState(schedulerState)
      if (schedulerStateProvesExecution(schedulerState)) {
        execution.observedRunning = true
      }
      if (state === "running") {
        return {
          state,
          evidenceRefs: schedulerEvidenceRefs(
            sshTarget,
            identity.jobId,
            execution,
            schedulerState,
            nodeList,
          ),
        }
      }
      const exitMatch = /^(\d+):(\d+)$/.exec(exitCode)
      const returnCode = exitMatch ? Number.parseInt(exitMatch[1] ?? "", 10) : null
      const signal = exitMatch ? Number.parseInt(exitMatch[2] ?? "", 10) : null
      const numericExitCode = canonicalProcessExitCode(returnCode, signal)
      const status = state === "completed" && returnCode === 0 && signal === 0
        ? "completed"
        : state === "completed"
          ? "failed"
          : state
      // Terminal scheduler ownership is durable in remote metadata. Do not retain
      // one in-memory request entry per failed evidence collection attempt.
      submitted.delete(identity.jobId)
      const readOutput = async (
        path: string,
      ): Promise<{ readonly content: string; readonly evidenceRefs: readonly string[] }> => {
        const header = (await ssh(
          [
            `if [ ! -f ${shellQuote(path)} ]; then printf 'M\\n'; else`,
            `size=$(wc -c < ${shellQuote(path)});`,
            `digest=$(sha256sum ${shellQuote(path)}); digest=\${digest%% *};`,
            `printf 'F:%s:%s\\n' "$size" "$digest"; fi`,
          ].join(" "),
          commandTimeoutMs,
          undefined,
          receiptOutputMaxBytes,
        )).stdout.trim()
        if (header === "M") {
          throw new Error(`${status} Slurm execution output is missing`)
        }
        const fileMatch = /^F:(\d+):([0-9a-f]{64})$/.exec(header)
        if (!fileMatch) throw new Error("Slurm output metadata frame is invalid")
        const size = Number.parseInt(fileMatch[1] ?? "", 10)
        const remoteDigest = fileMatch[2] ?? ""
        if (!Number.isSafeInteger(size)) {
          throw new Error("Slurm output size exceeds the safe integer range")
        }
        if (size > maxOutputBytes) {
          throw new Error("Slurm output exceeds the configured per-stream limit")
        }
        const chunks: Buffer[] = []
        for (let offset = 0; offset < size; offset += maxOutputBytes) {
          const count = Math.min(maxOutputBytes, size - offset)
          const encoded = (await ssh(
            [
              `dd if=${shellQuote(path)} bs=1 skip=${offset} count=${count} status=none`,
              "| base64 | tr -d '\\n'",
            ].join(" "),
            commandTimeoutMs,
            undefined,
            framedOutputMaxBytes,
          )).stdout
          const bytes = Buffer.from(encoded, "base64")
          if (bytes.length !== count) {
            throw new Error("Slurm output chunk length does not match its frame")
          }
          chunks.push(bytes)
        }
        const bytes = Buffer.concat(chunks)
        if (
          bytes.length !== size
          || createHash("sha256").update(bytes).digest("hex") !== remoteDigest
        ) {
          throw new Error("Slurm output content does not match remote evidence")
        }
        const content = bytes.toString("utf8")
        return {
          content,
          evidenceRefs: [],
        }
      }
      const [stdout, stderr] = await Promise.all([
        readOutput(execution.stdoutPath),
        readOutput(execution.stderrPath),
      ])
      const terminalResult = await resultFor(
        execution,
        status,
        numericExitCode,
        stdout.content,
        stderr.content,
      )
      return {
        state: status,
        result: terminalResult,
        evidenceRefs: [
          ...schedulerEvidenceRefs(
            sshTarget,
            identity.jobId,
            execution,
            schedulerState,
            nodeList,
          ),
          ...stdout.evidenceRefs,
          ...stderr.evidenceRefs,
        ],
      }
    },
    async cancel(executionId, context) {
      const identity = parseExecutionHandle(executionId)
      const deadline = context.deadlineAtMs ?? Date.now() + commandTimeoutMs
      let remaining = deadline - Date.now()
      if (remaining <= 0) throw new Error("Slurm cancellation deadline expired")
      const execution = await loadExecution(
        executionId,
        remaining,
        context.signal,
      )
      remaining = deadline - Date.now()
      if (remaining <= 0) throw new Error("Slurm cancellation deadline expired")
      const expectedJobName = slurmJobName(
        sshTarget,
        evidenceDirectory,
        execution.request.request_id,
      )
      await ssh(
        `timeout ${commandTimeoutSeconds}s scancel --name ${shellQuote(expectedJobName)} 2>/dev/null || true`,
        remaining,
        context.signal,
      )
    },
  }
}
