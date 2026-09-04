import { createHash, randomBytes } from "node:crypto"
import { execFile } from "node:child_process"
import { posix as posixPath } from "node:path"
import { promisify } from "node:util"

import { assertValid, type SandboxRequestV1, type SandboxResultV1 } from "@breadboard/kernel-contracts"
import {
  buildAndPersistCanonicalSandboxEvidence,
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


function retainedSubmission(request: SandboxRequestV1): string {
  return JSON.stringify({ request, requestDigest: requestDigest(request) })
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
  const rawEvidenceDirectory = requireSafeValue(
    options.remoteEvidenceDirectory,
    "remoteEvidenceDirectory",
  )
  if (!rawEvidenceDirectory.startsWith("/")) {
    throw new Error("remoteEvidenceDirectory must be an absolute path")
  }
  const evidenceDirectory = posixPath.normalize(rawEvidenceDirectory).replace(/\/+$/, "") || "/"
  if (evidenceDirectory === "/") {
    throw new Error("remoteEvidenceDirectory must not be the filesystem root")
  }
  const sshProgram = options.sshProgram ?? "ssh"
  const runCommand = options.runCommand ?? defaultRunCommand
  const maxOutputBytes = options.maxOutputBytes ?? 4 * 1024 * 1024
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1) {
    throw new Error("maxOutputBytes must be a positive safe integer")
  }
  const framedOutputMaxBytes = Math.ceil(maxOutputBytes / 3) * 4 + 64
  const receiptOutputMaxBytes = 1024
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
    outputLimitBytes = maxOutputBytes,
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

  async function loadExecution(
    executionId: string,
    timeoutMs = commandTimeoutMs,
    signal?: AbortSignal,
  ): Promise<SubmittedSlurmExecution> {
    const identity = parseExecutionHandle(executionId)
    const cached = submitted.get(identity.jobId)
    const metadataPath = remotePath(
      evidenceDirectory,
      `slurm-${identity.jobId}.request.b64`,
    )
    const metadata = await ssh(
      `cat ${shellQuote(metadataPath)}`,
      timeoutMs,
      signal,
      metadataOutputMaxBytes,
    )
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
      const encodedRequest = Buffer.from(retainedSubmission(request), "utf8").toString("base64")
      if (Buffer.byteLength(encodedRequest, "utf8") + 1 > metadataOutputMaxBytes) {
        throw new Error("Slurm retained request metadata exceeds the transport limit")
      }
      const command = request.command.map(shellQuote).join(" ")
      const submissionCommand = [
        `timeout ${commandTimeoutSeconds}s sbatch --parsable`,
        `--job-name=${shellQuote(jobName)}`,
        `--output=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.out"))}`,
        ...(request.workspace_ref
          ? [`--chdir=${shellQuote(request.workspace_ref)}`]
          : []),
        `--error=${shellQuote(remotePath(evidenceDirectory, "slurm-%j.err"))}`,
        `--wrap=${shellQuote(command)}`,
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
        `metadata=${shellQuote(remotePath(evidenceDirectory, "slurm-"))}"\${id}.request.b64";`,
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
        `owner=$(cat "$lock/owner" 2>/dev/null || true);`,
        `pid=\${owner%% *}; owner_rest=\${owner#* }; created=\${owner_rest%% *}; owner_start=\${owner_rest#* };`,
        `case "$created" in ""|*[!0-9]*) created=$(stat -c %Y "$lock" 2>/dev/null || printf '0');; esac;`,
        `current_start=''; case "$pid" in ""|*[!0-9]*) :;; *) current_start=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true);; esac;`,
        `owner_live=1; case "$pid" in ""|*[!0-9]*) :;; *) if [ ! -d "/proc/$pid" ]; then owner_live=0; elif [ -n "$owner_start" ] && [ -n "$current_start" ] && [ "$current_start" != "$owner_start" ]; then owner_live=0; fi;; esac;`,
        `if [ $((now-created)) -ge ${lockLeaseSeconds} ] && [ "$owner_live" -eq 0 ]; then`,
        `if timeout 1s scancel --name ${shellQuote(jobName)} 2>/dev/null; then`,
        `active=$(timeout 1s squeue -h --name ${shellQuote(jobName)} -o '%j' 2>/dev/null) || active=unknown;`,
        `[ -z "$active" ] && rm -rf "$lock";`,
        `fi;`,
        `fi; fi;`,
        `if [ ! -s "$receipt" ] && mkdir "$lock" 2>/dev/null; then`,
        `owner_start=$(awk '{print $22}' /proc/$$/stat 2>/dev/null || true); printf '%s %s %s\\n' "$$" "$now" "$owner_start" > "$lock/owner";`,
        `printf '%s\n' ${shellQuote(submissionAttemptToken)} > "$lock/attempt";`,
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
      submitted.delete(executionId)
      let execution: SubmittedSlurmExecution
      try {
        execution = await loadExecution(executionId)
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
          stdoutPath: remotePath(evidenceDirectory, `slurm-${executionId}.out`),
          stderrPath: remotePath(evidenceDirectory, `slurm-${executionId}.err`),
        }
        submitted.set(executionId, execution)
      }
      if (execution.requestDigest !== expectedRequestDigest) {
        throw new Error(
          "Slurm request_id collision; existing execution remains owned by its original request",
        )
      }
      return {
        executionId: durableExecutionHandle(executionId, execution.requestDigest),
        evidenceRefs: [
          `slurm://job/${executionId}/submitted`,
          `ssh://${sshTarget}${uriPath(receiptPath)}`,
          `ssh://${sshTarget}${uriPath(submissionCommandPath)}`,
          `ssh://${sshTarget}${uriPath(launchLogPath)}`,
        ],
      }
    },
    async observe(executionId) {
      const identity = parseExecutionHandle(executionId)
      const execution = await loadExecution(executionId)
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
      const result = await ssh(
        `sacct -X -j ${shellQuote(identity.jobId)} --noheader --parsable2 --format=JobName,State,ExitCode,NodeList`,
      )
      const records = result.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
      if (records.length === 0) return { state: "accepted" }
      const [
        accountingJobName = "",
        schedulerState = "UNKNOWN",
        exitCode = "",
        nodeList = "",
      ] = records[0]!.split("|")
      if (accountingJobName !== expectedJobName) {
        throw new Error("Slurm execution handle no longer owns the scheduler job id")
      }
      const state = classifyState(schedulerState)
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
      const numericExitCode = signal !== null && signal > 0
        ? 128 + signal
        : returnCode
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
          if (status === "completed" || status === "failed") {
            throw new Error(`${status} Slurm execution output is missing`)
          }
          return { content: "", evidenceRefs: [] }
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
