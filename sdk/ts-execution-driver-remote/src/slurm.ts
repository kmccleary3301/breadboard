import { execFile } from "node:child_process"
import { promisify } from "node:util"

import type { SandboxRequestV1, SandboxResultV1 } from "@breadboard/kernel-contracts"
import type {
  ScheduledExecutionBackendV1,
  ScheduledExecutionObservationV1,
} from "./scheduled.js"

export interface CommandResultV1 {
  readonly stdout: string
  readonly stderr: string
}

export type CommandRunnerV1 = (
  program: string,
  args: readonly string[],
) => Promise<CommandResultV1>

export interface SshSlurmBackendOptionsV1 {
  readonly sshTarget: string
  readonly remoteEvidenceDirectory: string
  readonly sshProgram?: string
  readonly runCommand?: CommandRunnerV1
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
): Promise<CommandResultV1> {
  const result = await execFileAsync(program, args, {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
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

function resultFor(
  execution: SubmittedSlurmExecution,
  status: SandboxResultV1["status"],
  executionId: string,
  schedulerState: string,
  sshTarget: string,
): SandboxResultV1 {
  return {
    schema_version: "bb.sandbox_result.v1",
    request_id: execution.request.request_id,
    status,
    stdout_ref: `ssh://${sshTarget}${execution.stdoutPath}`,
    stderr_ref: `ssh://${sshTarget}${execution.stderrPath}`,
    evidence_refs: [
      `slurm://job/${executionId}`,
      `slurm://job/${executionId}/state/${schedulerState}`,
    ],
    error: status === "completed" ? null : { reason: `slurm_${schedulerState.toLowerCase()}` },
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
  if (base === "TIMEOUT") return "timed_out"
  return "failed"
}

export function makeSshSlurmBackend(
  options: SshSlurmBackendOptionsV1,
): ScheduledExecutionBackendV1 {
  const sshTarget = requireSafeValue(options.sshTarget, "sshTarget")
  const evidenceDirectory = requireSafeValue(
    options.remoteEvidenceDirectory,
    "remoteEvidenceDirectory",
  )
  const sshProgram = options.sshProgram ?? "ssh"
  const runCommand = options.runCommand ?? defaultRunCommand
  const submitted = new Map<string, SubmittedSlurmExecution>()

  async function ssh(remoteCommand: string): Promise<CommandResultV1> {
    return runCommand(sshProgram, [sshTarget, remoteCommand])
  }

  return {
    backendId: `slurm:${sshTarget}`,
    async submit(request) {
      const command = request.command.map(shellQuote).join(" ")
      const result = await ssh([
        "mkdir -p",
        shellQuote(evidenceDirectory),
        "&& sbatch --parsable",
        `--output=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.out"))}`,
        `--error=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.err"))}`,
        `--wrap=${shellQuote(command)}`,
      ].join(" "))
      const executionId = result.stdout.trim().split(";", 1)[0] ?? ""
      if (!/^\d+$/.test(executionId)) {
        throw new Error(`Slurm submission returned an invalid job id: ${result.stdout.trim()}`)
      }
      const execution = {
        request,
        stdoutPath: remotePath(evidenceDirectory, `slurm-${executionId}.out`),
        stderrPath: remotePath(evidenceDirectory, `slurm-${executionId}.err`),
      }
      submitted.set(executionId, execution)
      return {
        executionId,
        evidenceRefs: [`slurm://job/${executionId}/submitted`],
      }
    },
    async observe(executionId) {
      const execution = submitted.get(executionId)
      if (!execution) throw new Error(`unknown Slurm execution: ${executionId}`)
      const result = await ssh(
        `sacct -X -j ${shellQuote(executionId)} --noheader --parsable2 --format=State,ExitCode`,
      )
      const records = result.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
      if (records.length === 0) return { state: "accepted" }
      const [schedulerState = "UNKNOWN", exitCode = ""] = records[0].split("|")
      const state = classifyState(schedulerState)
      if (state === "running") return { state }
      const status = state === "completed" && exitCode.startsWith("0:")
        ? "completed"
        : state === "completed"
          ? "failed"
          : state
      return {
        state: status,
        result: resultFor(execution, status, executionId, schedulerState, sshTarget),
      }
    },
    async cancel(executionId) {
      if (!submitted.has(executionId)) {
        throw new Error(`unknown Slurm execution: ${executionId}`)
      }
      await ssh(`scancel ${shellQuote(executionId)}`)
    },
  }
}
