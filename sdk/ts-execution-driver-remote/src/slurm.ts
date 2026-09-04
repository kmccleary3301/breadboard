import { createHash } from "node:crypto"
import { execFile } from "node:child_process"
import { promisify } from "node:util"

import type { SandboxRequestV1, SandboxResultV1 } from "@breadboard/kernel-contracts"
import { buildCanonicalSandboxEvidence } from "@breadboard/execution-drivers"
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
}

interface SubmittedSlurmExecution {
  readonly request: SandboxRequestV1
  readonly stdoutPath: string
  readonly stderrPath: string
}

const execFileAsync = promisify(execFile)

async function defaultRunCommand(
  program: string,
  args: readonly string[],
  options: CommandRunOptionsV1,
): Promise<CommandResultV1> {
  const result = await execFileAsync(program, args, {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
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

function genericFailureReason(status: SandboxResultV1["status"]): string {
  if (status === "timed_out") return "execution_timed_out"
  if (status === "cancelled") return "execution_cancelled"
  return "scheduled_execution_failed"
}

function resultFor(
  execution: SubmittedSlurmExecution,
  status: SandboxResultV1["status"],
  exitCode: number | null,
  stdout: string,
  stderr: string,
): SandboxResultV1 {
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: execution.request.request_id,
    status,
    ...buildCanonicalSandboxEvidence({
      command: execution.request.command,
      status,
      exitCode,
      stdout,
      stderr,
      evidenceMode: execution.request.evidence_mode,
    }),
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
  let request: SandboxRequestV1
  try {
    request = JSON.parse(Buffer.from(encoded.trim(), "base64").toString("utf8")) as SandboxRequestV1
  } catch {
    throw new Error("Slurm submission metadata is invalid")
  }
  if (
    request.schema_version !== "bb.sandbox_request.v1"
    || typeof request.request_id !== "string"
    || !Array.isArray(request.command)
  ) {
    throw new Error("Slurm submission metadata is invalid")
  }
  return {
    request,
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
  const commandTimeoutMs = Math.max(1, options.commandTimeoutMs ?? 30_000)
  const submitted = new Map<string, SubmittedSlurmExecution>()

  async function ssh(
    remoteCommand: string,
    timeoutMs = commandTimeoutMs,
  ): Promise<CommandResultV1> {
    return runCommand(
      sshProgram,
      [sshTarget, remoteCommand],
      { timeoutMs: Math.max(1, Math.min(commandTimeoutMs, timeoutMs)) },
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
      const submissionKey = sha256(request.request_id).slice("sha256:".length)
      const receiptPath = remotePath(evidenceDirectory, `submission-${submissionKey}.receipt`)
      const cancelPath = remotePath(evidenceDirectory, `submission-${submissionKey}.cancel`)
      const lockPath = remotePath(evidenceDirectory, `submission-${submissionKey}.lock`)
      const launchLogPath = remotePath(evidenceDirectory, `submission-${submissionKey}.log`)
      const encodedRequest = Buffer.from(JSON.stringify(request), "utf8").toString("base64")
      const command = request.command.map(shellQuote).join(" ")
      const detachedScript = [
        "set -eu;",
        `lock=${shellQuote(lockPath)};`,
        `trap 'rmdir "$lock"' EXIT;`,
        `receipt=${shellQuote(receiptPath)};`,
        `[ -s "$receipt" ] && exit 0;`,
        `job=$(sbatch --parsable --output=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.out"))}`,
        `--error=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.err"))}`,
        `--wrap=${shellQuote(command)});`,
        `id=\${job%%;*};`,
        `metadata=${shellQuote(remotePath(evidenceDirectory, "slurm-"))}"\${id}.request.b64";`,
        `printf '%s\\n' ${shellQuote(encodedRequest)} > "$metadata.tmp";`,
        `mv "$metadata.tmp" "$metadata";`,
        `printf '%s\\n' "$job" > "$receipt.tmp";`,
        `mv "$receipt.tmp" "$receipt";`,
        `[ ! -f ${shellQuote(cancelPath)} ] || scancel "$id";`,
      ].join(" ")
      const launchCommand = [
        `mkdir -p ${shellQuote(evidenceDirectory)} &&`,
        `if [ ! -s ${shellQuote(receiptPath)} ] && mkdir ${shellQuote(lockPath)} 2>/dev/null; then`,
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
        await ssh([
          `touch ${shellQuote(cancelPath)};`,
          `if [ -s ${shellQuote(receiptPath)} ]; then`,
          `job=$(cat ${shellQuote(receiptPath)}); scancel "\${job%%;*}";`,
          "fi",
        ].join(" "))
        throw new Error(
          context.signal.aborted
            ? "Slurm submission was cancelled"
            : launchError
              ? "Slurm submission acknowledgement failed"
              : "Slurm submission acknowledgement timed out",
          { cause: launchError },
        )
      }
      const executionId = receipt.split(";", 1)[0] ?? ""
      if (!/^\d+$/.test(executionId)) {
        throw new Error("Slurm submission returned an invalid job id")
      }
      const execution = await loadExecution(executionId)
      if (execution.request.request_id !== request.request_id) {
        throw new Error("Slurm submission receipt request mismatch")
      }
      return {
        executionId,
        evidenceRefs: [`slurm://job/${executionId}/submitted`],
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
      const status = state === "completed" && exitCode.startsWith("0:")
        ? "completed"
        : state === "completed"
          ? "failed"
          : state
      const numericExitCode = /^\d+:\d+$/.test(exitCode)
        ? Number.parseInt(exitCode.split(":", 1)[0] ?? "", 10)
        : null
      const [stdout, stderr] = await Promise.all([
        ssh(`if [ -f ${shellQuote(execution.stdoutPath)} ]; then cat ${shellQuote(execution.stdoutPath)}; else exit 66; fi`)
          .then((output) => output.stdout),
        ssh(`if [ -f ${shellQuote(execution.stderrPath)} ]; then cat ${shellQuote(execution.stderrPath)}; else exit 66; fi`)
          .then((output) => output.stdout),
      ])
      return {
        state: status,
        result: resultFor(execution, status, numericExitCode, stdout, stderr),
        evidenceRefs: schedulerEvidenceRefs(
          sshTarget,
          executionId,
          execution,
          schedulerState,
          nodeList,
        ),
      }
    },
    async cancel(executionId) {
      if (!/^\d+$/.test(executionId)) throw new Error("invalid Slurm execution id")
      await ssh(`scancel ${shellQuote(executionId)}`)
    },
  }
}
