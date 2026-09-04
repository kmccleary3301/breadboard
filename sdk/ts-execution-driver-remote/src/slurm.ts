import { createHash } from "node:crypto"
import { execFile } from "node:child_process"
import { promisify } from "node:util"

import { assertValid, type SandboxRequestV1, type SandboxResultV1 } from "@breadboard/kernel-contracts"
import {
  buildCanonicalSandboxEvidence,
  persistCanonicalSandboxArtifact,
} from "@breadboard/execution-drivers"
import type {
  ScheduledExecutionBackendV1,
  ScheduledExecutionObservationV1,
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
}

interface SubmittedSlurmExecution {
  readonly request: SandboxRequestV1
  readonly stdoutPath: string
  readonly requestDigest: string
  readonly stderrPath: string
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

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value)
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Slurm request contains a non-finite number")
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`
  }
  throw new Error("Slurm request contains a non-JSON value")
}

function requestDigest(request: SandboxRequestV1): string {
  return sha256(canonicalJson(request))
}

function retainedSubmission(request: SandboxRequestV1): string {
  return JSON.stringify({ request, requestDigest: requestDigest(request) })
}

function genericFailureReason(status: SandboxResultV1["status"]): string {
  if (status === "timed_out") return "execution_timed_out"
  if (status === "cancelled") return "execution_cancelled"
  return "scheduled_execution_failed"
}

async function resultFor(
  execution: SubmittedSlurmExecution,
  status: SandboxResultV1["status"],
  exitCode: number | null,
  stdout: string,
  stderr: string,
): Promise<SandboxResultV1> {
  const evidence = buildCanonicalSandboxEvidence({
    command: execution.request.command,
    status,
    exitCode,
    stdout,
    stderr,
    evidenceMode: execution.request.evidence_mode,
  })
  await Promise.all([
    persistCanonicalSandboxArtifact(evidence.stdout_ref, stdout),
    persistCanonicalSandboxArtifact(evidence.stderr_ref, stderr),
  ])
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

function schedulerEvidenceRefs(
  sshTarget: string,
  executionId: string,
  execution: SubmittedSlurmExecution,
  schedulerState: string,
  nodeList: string,
): string[] {
  return [
    `slurm://job/${executionId}`,
    `slurm://job/${executionId}/state/${encodeURIComponent(schedulerState)}`,
    ...(nodeList && nodeList !== "(null)"
      ? [`slurm://job/${executionId}/node/${encodeURIComponent(nodeList)}`]
      : []),
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
    ? parsed as { readonly request: unknown; readonly requestDigest: unknown }
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
  return {
    request,
    requestDigest: digest,
    stdoutPath: remotePath(evidenceDirectory, `slurm-${executionId}.out`),
    stderrPath: remotePath(evidenceDirectory, `slurm-${executionId}.err`),
  }
}

export function makeSshSlurmBackend(
  options: SshSlurmBackendOptionsV1,
): ScheduledExecutionBackendV1 {
  const sshTarget = requireSafeValue(options.sshTarget, "sshTarget")
  if (sshTarget.startsWith("-")) {
    throw new Error("sshTarget must not begin with an option prefix")
  }
  const evidenceDirectory = requireSafeValue(
    options.remoteEvidenceDirectory,
    "remoteEvidenceDirectory",
  )
  if (!evidenceDirectory.startsWith("/")) {
    throw new Error("remoteEvidenceDirectory must be an absolute path")
  }
  const sshProgram = options.sshProgram ?? "ssh"
  const runCommand = options.runCommand ?? defaultRunCommand
  const maxOutputBytes = options.maxOutputBytes ?? 4 * 1024 * 1024
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1) {
    throw new Error("maxOutputBytes must be a positive safe integer")
  }
  const commandTimeoutMs = Math.max(1, options.commandTimeoutMs ?? 30_000)
  const submitted = new Map<string, SubmittedSlurmExecution>()
  const commandTimeoutSeconds = Math.max(1, Math.ceil(commandTimeoutMs / 1_000))
  const lockLeaseSeconds = commandTimeoutSeconds * 2 + 5

  async function ssh(
    remoteCommand: string,
    timeoutMs = commandTimeoutMs,
    signal?: AbortSignal,
  ): Promise<CommandResultV1> {
    return runCommand(
      sshProgram,
      [sshTarget, remoteCommand],
      {
        timeoutMs: Math.max(1, Math.min(commandTimeoutMs, timeoutMs)),
        maxOutputBytes,
        signal,
      },
    )
  }

  async function loadExecution(executionId: string): Promise<SubmittedSlurmExecution> {
    if (!/^\d+$/.test(executionId)) throw new Error("invalid Slurm execution id")
    const cached = submitted.get(executionId)
    if (cached) return cached
    const metadataPath = remotePath(evidenceDirectory, `slurm-${executionId}.request.b64`)
    const metadata = await ssh(`cat ${shellQuote(metadataPath)}`)
    const execution = parseExecution(metadata.stdout, evidenceDirectory, executionId)
    submitted.set(executionId, execution)
    return execution
  }

  return {
    backendId: `slurm:${sshTarget}`,
    async submit(request, context) {
      const expectedRequestDigest = requestDigest(request)
      const submissionKey = sha256(request.request_id).slice("sha256:".length)
      const jobName = `bb-${submissionKey.slice(0, 32)}`
      const receiptPath = remotePath(evidenceDirectory, `submission-${submissionKey}.receipt`)
      const cancelPath = remotePath(evidenceDirectory, `submission-${submissionKey}.cancel`)
      const lockPath = remotePath(evidenceDirectory, `submission-${submissionKey}.lock`)
      const launchLogPath = remotePath(evidenceDirectory, `submission-${submissionKey}.log`)
      const submissionCommandPath = remotePath(
        evidenceDirectory,
        `submission-${submissionKey}.command.b64`,
      )
      const encodedRequest = Buffer.from(retainedSubmission(request), "utf8").toString("base64")
      const command = request.command.map(shellQuote).join(" ")
      const submissionCommand = [
        `timeout ${commandTimeoutSeconds}s sbatch --parsable`,
        `--job-name=${shellQuote(jobName)}`,
        `--output=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.out"))}`,
        `--error=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.err"))}`,
        `--wrap=${shellQuote(command)}`,
      ].join(" ")
      const encodedSubmissionCommand = Buffer.from(
        submissionCommand,
        "utf8",
      ).toString("base64")
      const detachedScript = [
        "set -eu;",
        `lock=${shellQuote(lockPath)};`,
        `receipt=${shellQuote(receiptPath)};`,
        `owner="$lock/owner";`,
        "id='';",
        `cleanup() { if [ -n "$id" ] && [ ! -s "$receipt" ]; then case "$id" in *[!0-9]*) :;; *) scancel "$id" || true;; esac; fi; rm -rf "$lock"; };`,
        "trap cleanup EXIT;",
        `printf '%s %s\\n' "$$" "$(date +%s)" > "$owner.tmp";`,
        `mv "$owner.tmp" "$owner";`,
        `[ -s "$receipt" ] && exit 0;`,
        `printf '%s\\n' ${shellQuote(encodedSubmissionCommand)} > ${shellQuote(`${submissionCommandPath}.tmp`)};`,
        `mv ${shellQuote(`${submissionCommandPath}.tmp`)} ${shellQuote(submissionCommandPath)};`,
        `job=$(${submissionCommand});`,
        `id=\${job%%;*};`,
        `case "$id" in ""|*[!0-9]*) exit 65;; esac;`,
        `printf '%s\\n' "$id" > "$lock/job.tmp";`,
        `mv "$lock/job.tmp" "$lock/job";`,
        `metadata=${shellQuote(remotePath(evidenceDirectory, "slurm-"))}"\${id}.request.b64";`,
        `printf '%s\\n' ${shellQuote(encodedRequest)} > "$metadata.tmp";`,
        `mv "$metadata.tmp" "$metadata";`,
        `printf '%s\\n' "$id" > "$receipt.tmp";`,
        `mv "$receipt.tmp" "$receipt";`,
        `[ ! -f ${shellQuote(cancelPath)} ] || scancel "$id";`,
      ].join(" ")
      const launchCommand = [
        `mkdir -p ${shellQuote(evidenceDirectory)} &&`,
        `lock=${shellQuote(lockPath)}; receipt=${shellQuote(receiptPath)}; now=$(date +%s);`,
        `if [ -d "$lock" ] && [ ! -s "$receipt" ]; then`,
        `owner=$(cat "$lock/owner" 2>/dev/null || true);`,
        `pid=\${owner%% *}; created=\${owner#* };`,
        `case "$created" in ""|*[!0-9]*) created=$(stat -c %Y "$lock" 2>/dev/null || printf '0');; esac;`,
        `if ! kill -0 "$pid" 2>/dev/null && [ $((now-created)) -ge ${lockLeaseSeconds} ]; then`,
        `stale_job=$(cat "$lock/job" 2>/dev/null || true);`,
        `case "$stale_job" in ""|*[!0-9]*) scancel --name ${shellQuote(jobName)} 2>/dev/null || true;; *) scancel "$stale_job" 2>/dev/null || true;; esac;`,
        `rm -rf "$lock";`,
        `fi; fi;`,
        `if [ ! -s "$receipt" ] && mkdir "$lock" 2>/dev/null; then`,
        `printf '%s %s\\n' "$$" "$now" > "$lock/owner";`,
        `setsid sh -c ${shellQuote(detachedScript)}`,
        `</dev/null >${shellQuote(launchLogPath)} 2>&1 &`,
        "fi",
      ].join(" ")
      const deadline = Math.min(
        Date.now() + commandTimeoutMs,
        context.deadlineAtMs ?? Number.POSITIVE_INFINITY,
      )
      let launchError: unknown
      try {
        await ssh(launchCommand, deadline - Date.now())
      } catch (error: unknown) {
        launchError = error
      }
      let receipt = ""
      while (!receipt && !context.signal.aborted && Date.now() < deadline) {
        try {
          receipt = (await ssh(
            `if [ -s ${shellQuote(receiptPath)} ]; then cat ${shellQuote(receiptPath)}; fi`,
            deadline - Date.now(),
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
            `if [ -s ${shellQuote(receiptPath)} ]; then`,
            `job=$(cat ${shellQuote(receiptPath)}); scancel "\${job%%;*}";`,
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
      const executionId = receipt.split(";", 1)[0] ?? ""
      if (!/^\d+$/.test(executionId)) {
        throw new Error("Slurm submission returned an invalid job id")
      }
      const execution = await loadExecution(executionId)
      if (execution.requestDigest !== expectedRequestDigest) {
        throw new Error("Slurm submission receipt request mismatch")
      }
      return {
        executionId,
        evidenceRefs: [
          `slurm://job/${executionId}/submitted`,
          `ssh://${sshTarget}${uriPath(receiptPath)}`,
          `ssh://${sshTarget}${uriPath(submissionCommandPath)}`,
          `ssh://${sshTarget}${uriPath(launchLogPath)}`,
        ],
      }
    },
    async observe(executionId) {
      const execution = await loadExecution(executionId)
      const activeResult = await ssh(
        `squeue -h -j ${shellQuote(executionId)} -o '%T|%N'`,
      )
      const [activeState = "", activeNodeList = ""] = activeResult.stdout.trim().split("|")
      if (activeState) {
        const state = classifyState(activeState)
        if (state === "running") {
          return {
            state,
            evidenceRefs: schedulerEvidenceRefs(
              sshTarget,
              executionId,
              execution,
              activeState,
              activeNodeList,
            ),
          }
        }
      }
      const result = await ssh(
        `sacct -X -j ${shellQuote(executionId)} --noheader --parsable2 --format=State,ExitCode,NodeList`,
      )
      const records = result.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
      if (records.length === 0) return { state: "accepted" }
      const [schedulerState = "UNKNOWN", exitCode = "", nodeList = ""] = records[0].split("|")
      const state = classifyState(schedulerState)
      if (state === "running") {
        return {
          state,
          evidenceRefs: schedulerEvidenceRefs(
            sshTarget,
            executionId,
            execution,
            schedulerState,
            nodeList,
          ),
        }
      }
      const exitMatch = /^(\d+):(\d+)$/.exec(exitCode)
      const returnCode = exitMatch ? Number.parseInt(exitMatch[1] ?? "", 10) : null
      const signal = exitMatch ? Number.parseInt(exitMatch[2] ?? "", 10) : null
      const numericExitCode = signal !== null && signal > 0
        ? 128 + signal
        : returnCode
      const status = state === "completed" && returnCode === 0 && signal === 0
        ? "completed"
        : state === "completed"
          ? "failed"
          : state
      const readOutput = (path: string) => ssh([
        `if [ ! -f ${shellQuote(path)} ]; then exit ${status === "completed" ? 66 : 0}; fi;`,
        `size=$(wc -c < ${shellQuote(path)});`,
        `[ "$size" -le ${maxOutputBytes} ] || exit 67;`,
        `cat ${shellQuote(path)}`,
      ].join(" "))
      const [stdout, stderr] = await Promise.all([
        readOutput(execution.stdoutPath).then((output) => output.stdout),
        readOutput(execution.stderrPath).then((output) => output.stdout),
      ])
      return {
        state: status,
        result: await resultFor(execution, status, numericExitCode, stdout, stderr),
        evidenceRefs: schedulerEvidenceRefs(
          sshTarget,
          executionId,
          execution,
          schedulerState,
          nodeList,
        ),
      }
    },
    async cancel(executionId, context) {
      if (!/^\d+$/.test(executionId)) throw new Error("invalid Slurm execution id")
      const deadline = context.deadlineAtMs ?? Date.now() + commandTimeoutMs
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new Error("Slurm cancellation deadline expired")
      await ssh(`scancel ${shellQuote(executionId)}`, remaining, context.signal)
    },
  }
}
