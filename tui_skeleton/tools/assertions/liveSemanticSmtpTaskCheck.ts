import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { spawn } from "node:child_process"

interface Finding {
  readonly id: string
  readonly message: string
  readonly severity: "error" | "warning"
}

interface CaseInfo {
  readonly workspace?: string
  readonly repo_root?: string
  readonly config_path?: string
  readonly command?: string
}

const parseArgs = (): { caseDir: string; strict: boolean } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  let strict = false
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
    else if (args[i] === "--strict") strict = true
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir), strict }
}

const readOptional = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const readJsonOptional = async <T>(file: string): Promise<T | null> => {
  const raw = await readOptional(file)
  if (!raw.trim()) return null
  return JSON.parse(raw) as T
}

const exists = async (file: string): Promise<boolean> => {
  try {
    await fs.access(file)
    return true
  } catch {
    return false
  }
}

const parseNdjson = (raw: string): any[] =>
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line)]
      } catch {
        return []
      }
    })

const walkFiles = async (root: string): Promise<string[]> => {
  const out: string[] = []
  const visit = async (dir: string) => {
    const entries = await fs.readdir(dir, { withFileTypes: true }).catch(() => [])
    for (const entry of entries) {
      if (entry.name === ".git" || entry.name === ".breadboard" || entry.name === "docs_tmp") continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) await visit(full)
      else if (entry.isFile()) out.push(full)
    }
  }
  await visit(root)
  return out.sort()
}

const runCommand = async (
  command: string,
  args: string[],
  cwd: string,
  timeoutMs: number,
): Promise<{ code: number | null; stdout: string; stderr: string; timedOut: boolean }> =>
  new Promise((resolve) => {
    const child = spawn(command, args, { cwd, detached: true, stdio: ["ignore", "pipe", "pipe"] })
    let timedOut = false
    const killGroup = (signal: NodeJS.Signals) => {
      if (typeof child.pid !== "number") return
      try {
        process.kill(-child.pid, signal)
      } catch {
        try {
          child.kill(signal)
        } catch {
          // best-effort cleanup for disposable validation subprocesses
        }
      }
    }
    const timer = setTimeout(() => {
      timedOut = true
      killGroup("SIGTERM")
      setTimeout(() => killGroup("SIGKILL"), 2_000).unref()
    }, timeoutMs)
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk)
    })
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk)
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve({ code, stdout, stderr, timedOut })
    })
  })

const gitStatusChangedFiles = async (workspace: string): Promise<Set<string>> => {
  const result = await runCommand("git", ["status", "--porcelain"], workspace, 10_000)
  const changed = new Set<string>()
  if (result.code !== 0 || result.timedOut) return changed
  for (const line of result.stdout.split(/\r?\n/)) {
    if (!line.trim()) continue
    const rawPath = line.slice(3).trim()
    if (!rawPath) continue
    const normalized = rawPath.includes(" -> ") ? rawPath.split(" -> ").at(-1) ?? rawPath : rawPath
    changed.add(normalized.replace(/\\/g, "/"))
  }
  return changed
}

const parseProviderToolCalls = async (logDir: string): Promise<Array<{ file: string; name: string; args: any }>> => {
  const dir = path.join(logDir, "provider_native", "tool_calls")
  const entries = await fs.readdir(dir).catch(() => [])
  const calls: Array<{ file: string; name: string; args: any }> = []
  for (const entry of entries.sort()) {
    if (!entry.endsWith(".json")) continue
    const raw = await readJsonOptional<any[]>(path.join(dir, entry))
    if (!Array.isArray(raw)) continue
    for (const call of raw) {
      const fn = call?.function ?? {}
      let parsedArgs: any = {}
      if (typeof fn.arguments === "string") {
        try {
          parsedArgs = JSON.parse(fn.arguments)
        } catch {
          parsedArgs = { raw: fn.arguments }
        }
      }
      calls.push({ file: entry, name: String(fn.name ?? ""), args: parsedArgs })
    }
  }
  return calls
}

const parseProviderToolResults = async (logDir: string): Promise<Array<{ file: string; name: string; args: any; out: any }>> => {
  const dir = path.join(logDir, "provider_native", "tool_results")
  const entries = await fs.readdir(dir).catch(() => [])
  const results: Array<{ file: string; name: string; args: any; out: any }> = []
  for (const entry of entries.sort()) {
    if (!entry.endsWith(".json")) continue
    const raw = await readJsonOptional<any[]>(path.join(dir, entry))
    if (!Array.isArray(raw)) continue
    for (const result of raw) {
      results.push({
        file: entry,
        name: String(result?.provider_fn ?? result?.fn ?? ""),
        args: result?.args ?? {},
        out: result?.out ?? {},
      })
    }
  }
  return results
}

const aggregateProviderToolUsage = (results: Array<{ name: string; args: any; out: any }>) => {
  const successfulWriteTargets = new Set<string>()
  let writeCalls = 0
  let successfulWrites = 0
  let testCommands = 0
  let successfulTests = 0
  for (const result of results) {
    const name = String(result.name ?? "")
    const out = result.out ?? {}
    if (/apply_patch|apply_unified_patch|patch/.test(name)) {
      writeCalls += 1
      const ok = out.ok === true || out.exit === 0
      if (ok) successfulWrites += 1
      const paths = Array.isArray(out?.data?.paths) ? out.data.paths : []
      if (ok) {
        for (const pathValue of paths) {
          const target = String(pathValue ?? "").replace(/^\.\/+/, "")
          if (target) successfulWriteTargets.add(target)
        }
      }
    }
    if (/shell_command|run_shell/.test(name)) {
      const command = String(result.args?.command ?? "")
      const isTest = /\b(make|gcc|cc)\b|smoke_test\.sh/.test(command)
      if (isTest) {
        testCommands += 1
        if (out.exit === 0) successfulTests += 1
      }
    }
  }
  return {
    writeCalls,
    successfulWrites,
    userFacingWriteCalls: writeCalls,
    successfulUserFacingWrites: successfulWrites,
    requestedFileWriteCalls: writeCalls,
    successfulRequestedFileWrites: successfulWrites,
    successfulRequestedTargets: successfulWriteTargets,
    testCommands,
    successfulTests,
  }
}

const runSummaryToolCommands = (runSummary: any): Array<{ file: string; name: string; args: any }> => {
  const turns = runSummary?.turn_tool_usage ?? runSummary?.tool_usage?.turns ?? {}
  const calls: Array<{ file: string; name: string; args: any }> = []
  if (!turns || typeof turns !== "object") return calls
  for (const [turn, payload] of Object.entries(turns)) {
    const tools = Array.isArray((payload as any)?.tools) ? (payload as any).tools : []
    for (const tool of tools) {
      const meta = tool?.meta ?? {}
      const command = typeof meta.command === "string" ? meta.command : ""
      if (!command) continue
      calls.push({
        file: `run_summary.turn_${turn}`,
        name: String(tool?.name ?? ""),
        args: { command, workdir: meta.workdir },
      })
    }
  }
  return calls
}

const splitSnapshots = (raw: string): Array<{ label: string; body: string }> => {
  const sections: Array<{ label: string; body: string }> = []
  let label: string | null = null
  let body: string[] = []
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("# ")) {
      if (label !== null) sections.push({ label, body: body.join("\n") })
      label = line.slice(2).trim()
      body = []
    } else {
      body.push(line)
    }
  }
  if (label !== null) sections.push({ label, body: body.join("\n") })
  return sections
}

const resolvedWorkdir = (workspace: string, workdir: unknown): string => {
  if (typeof workdir !== "string" || workdir.trim() === "") return workspace
  return path.resolve(workspace, workdir)
}

const isInside = (parent: string, child: string): boolean => {
  const relative = path.relative(parent, child)
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative))
}

const promiseOnlyPattern = /\b(?:I(?:'|’)ll|I(?:'|’)m going to|I am going to|I will|will first|then I(?:'|’)ll|next I(?:'|’)ll|I'll inspect|I’ll inspect)\b/i

export const evaluateLiveSemanticSmtpTask = async (caseDir: string): Promise<Finding[]> => {
  const findings: Finding[] = []
  const add = (id: string, message: string, severity: "error" | "warning" = "error") => findings.push({ id, message, severity })

  const caseInfo = await readJsonOptional<CaseInfo>(path.join(caseDir, "case_info.json"))
  const workspace = path.resolve(String(caseInfo?.workspace ?? ""))
  if (!workspace || workspace === path.parse(workspace).root || !(await exists(workspace))) {
    add("missing-workspace", `Case workspace is missing or invalid: ${String(caseInfo?.workspace ?? "<missing>")}`)
    return findings
  }

  const repoRoot = caseInfo?.repo_root ? path.resolve(caseInfo.repo_root) : ""
  if (repoRoot && isInside(repoRoot, workspace)) {
    add("workspace-inside-breadboard-repo", `Semantic live gate workspace must be outside the BreadBoard repo; saw ${workspace}`)
  }

  const expectedFiles = ["smtp_server.c", "Makefile", "smoke_test.sh"]
  const expectedRequestedWriteTargets = ["smtp_server.c", "Makefile", "README.md", "smoke_test.sh"]
  const changedFiles = await gitStatusChangedFiles(workspace)
  for (const relative of expectedFiles) {
    if (!(await exists(path.join(workspace, relative)))) add("missing-deliverable", `Expected generated file ${relative} is missing.`)
  }

  const source = await readOptional(path.join(workspace, "smtp_server.c"))
  const smtpCapabilityChecks: Array<[string, RegExp]> = [
    ["HELO", /\bHELO\b/i],
    ["EHLO", /\bEHLO\b/i],
    ["MAIL FROM", /\bMAIL\b[\s\S]{0,400}\bFROM\b|MAIL\s+FROM/i],
    ["RCPT TO", /\bRCPT\b[\s\S]{0,800}(?:\bTO\b|TO:|rcpt_to|recipient)|RCPT\s+TO/i],
    ["DATA", /\bDATA\b/i],
    ["RSET", /\bRSET\b/i],
    ["NOOP", /\bNOOP\b/i],
    ["QUIT", /\bQUIT\b/i],
    ["maildrop", /maildrop/i],
  ]
  for (const [label, pattern] of smtpCapabilityChecks) {
    if (source && !pattern.test(source)) add("missing-smtp-capability", `smtp_server.c is missing marker ${label}.`)
  }

  const readme = await readOptional(path.join(workspace, "README.md"))
  if (!/make/i.test(readme) || !/smoke/i.test(readme) || !/smtp/i.test(readme)) {
    add("readme-usage-missing", "README.md does not document SMTP build/smoke usage.")
  }

  const userFiles = (await walkFiles(workspace)).map((file) => path.relative(workspace, file).replace(/\\/g, "/"))
  const generatedUserFiles = userFiles.filter((file) => !["README.md", "requirements.txt", "AGENTS.md"].includes(file) && !file.startsWith("."))
  if (generatedUserFiles.length === 0) {
    add("zero-user-files-created", "The session finished without creating any user-facing project files.")
  }

  const loggingDirRaw = (await readOptional(path.join(caseDir, "logging_dir.txt"))).trim()
  const loggingDir = loggingDirRaw
    ? path.isAbsolute(loggingDirRaw)
      ? loggingDirRaw
      : path.resolve(repoRoot || process.cwd(), loggingDirRaw)
    : ""
  const runSummary = loggingDir ? await readJsonOptional<any>(path.join(loggingDir, "meta", "run_summary.json")) : null
  const providerResults = loggingDir ? await parseProviderToolResults(loggingDir) : []
  const providerAggregate = aggregateProviderToolUsage(providerResults)
  let observedToolCalls = providerResults.length
  if (!loggingDir || !runSummary) {
    add("missing-run-summary", "No logging_dir/meta/run_summary.json was captured for semantic verification.")
  } else {
    const toolUsage = runSummary.tool_usage ?? {}
    observedToolCalls = Math.max(observedToolCalls, Number(toolUsage.total_calls ?? 0))
    const writeCalls = Math.max(Number(toolUsage.write_calls ?? 0), providerAggregate.writeCalls)
    const successfulWrites = Math.max(Number(toolUsage.successful_writes ?? 0), providerAggregate.successfulWrites)
    const userFacingWriteCalls = Math.max(Number(toolUsage.user_facing_write_calls ?? 0), providerAggregate.userFacingWriteCalls)
    const successfulUserFacingWrites = Math.max(Number(toolUsage.successful_user_facing_writes ?? 0), providerAggregate.successfulUserFacingWrites)
    const requestedFileWriteCalls = Math.max(Number(toolUsage.requested_file_write_calls ?? 0), providerAggregate.requestedFileWriteCalls)
    const successfulRequestedFileWrites = Math.max(Number(toolUsage.successful_requested_file_writes ?? 0), providerAggregate.successfulRequestedFileWrites)
    const testCommands = Math.max(Number(toolUsage.test_commands ?? 0), providerAggregate.testCommands)
    const successfulTests = Math.max(Number(toolUsage.successful_tests ?? 0), providerAggregate.successfulTests)
    if (writeCalls < 1 || successfulWrites < 1) {
      add("zero-write-calls", `Expected at least one successful write call; saw write_calls=${writeCalls}, successful_writes=${successfulWrites}.`)
    }
    if (userFacingWriteCalls < 1 || successfulUserFacingWrites < 1) {
      add("zero-user-facing-write-calls", `Expected at least one successful user-facing write call; saw user_facing_write_calls=${userFacingWriteCalls}, successful_user_facing_writes=${successfulUserFacingWrites}.`)
    }
    const successfulRequestedTargets = new Set<string>((toolUsage.successful_requested_write_targets ?? []).map((target: unknown) => String(target)))
    for (const target of providerAggregate.successfulRequestedTargets) successfulRequestedTargets.add(target)
    const missingRequestedTargets = expectedRequestedWriteTargets.filter(
      (target) => !successfulRequestedTargets.has(target) && !changedFiles.has(target),
    )
    if (requestedFileWriteCalls < 1 || successfulRequestedFileWrites < 1) {
      add("zero-requested-file-write-calls", `Expected at least one successful requested-file write call; saw requested_file_write_calls=${requestedFileWriteCalls}, successful_requested_file_writes=${successfulRequestedFileWrites}.`)
    }
    if (missingRequestedTargets.length > 0) {
      add("missing-requested-write-receipts", `Missing successful write receipts for requested targets: ${missingRequestedTargets.join(", ")}.`)
    }
    if (testCommands < 1 || successfulTests < 1) {
      add("zero-test-commands", `Expected at least one successful build/smoke command; saw test_commands=${testCommands}, successful_tests=${successfulTests}.`)
    }
    const stepsTaken = Number(runSummary.steps_taken ?? runSummary.completion_summary?.steps_taken ?? 0)
    const maxSteps = Number(runSummary.max_steps ?? runSummary.completion_summary?.max_steps ?? 0)
    if (maxSteps > 0 && stepsTaken >= maxSteps) {
      add("max-steps-exhausted-or-borderline", `Run consumed the full step budget (${stepsTaken}/${maxSteps}); long-session implementation gates must not finish at the ceiling.`)
    }
    const completionReason = String(runSummary.completion_summary?.reason ?? "")
    if (completionReason === "empty_response") {
      add("provider-empty-response", "Provider stopped with an empty response before producing implementation artifacts.")
    }
    const workspacePath = path.resolve(String(runSummary.workspace_path ?? ""))
    if (workspacePath && workspacePath !== workspace) {
      add("workspace-path-mismatch", `Run summary workspace_path does not match dummy workspace: ${workspacePath} !== ${workspace}`)
    }

    const calls = [...runSummaryToolCommands(runSummary), ...(await parseProviderToolCalls(loggingDir))]
    observedToolCalls = Math.max(observedToolCalls, calls.length)
    const broadCommands = calls.filter((call) => {
      const command = String(call.args?.command ?? "")
      const workdir = resolvedWorkdir(workspace, call.args?.workdir)
      if (!isInside(workspace, workdir)) return true
      return /(^|[;&|]\s*|\s)(find|rg|grep|ls|cat|sed)\s+\.\.(\s|\/|$)/.test(command) || /\brg\s+--files\s+\.\./.test(command)
    })
    if (broadCommands.length > 0) {
      const first = broadCommands[0]
      add("workspace-escape-command", `Observed ${broadCommands.length} broad/out-of-workspace command(s); first ${first.file}: ${first.name} ${JSON.stringify(first.args).slice(0, 240)}`)
    }
    const shellToolMisuse = calls.filter((call) => {
      if (!/shell_command|run_shell/.test(call.name)) return false
      const command = String(call.args?.command ?? "").trim()
      return /^(?:update_plan|apply_patch|mark_task_complete)\b/.test(command)
    })
    if (shellToolMisuse.length > 0) {
      const first = shellToolMisuse[0]
      add("tool-invoked-through-shell", `Observed ${shellToolMisuse.length} tool invocation(s) routed through shell instead of native tool calls; first ${first.file}: ${JSON.stringify(first.args).slice(0, 240)}`)
    }
  }

  const stateRecords = parseNdjson(await readOptional(path.join(caseDir, "repl_state.ndjson")))
  const finalRecord = stateRecords.at(-1) ?? null
  const finalState = finalRecord?.state ?? null
  if (!finalState) {
    add("missing-final-state", "No final state dump record was captured.")
  } else {
    const transcriptCells = Array.isArray(finalRecord?.transcriptCells)
      ? finalRecord.transcriptCells
      : Array.isArray(finalState.transcriptCells)
        ? finalState.transcriptCells
        : []
    const conversation = Array.isArray(finalState.conversation) ? finalState.conversation : []
    const lastConversation = finalState.lastConversation ?? conversation.at(-1) ?? null
    const completionMethod = String(runSummary?.completion_summary?.method ?? "")
    const completionReason = String(runSummary?.completion_summary?.reason ?? "")
    const explicitFailedVerification =
      completionMethod === "failed_verification_forced_closure" ||
      completionReason === "failed_verification_after_retries" ||
      /^Halted\\s*\\(failed_verification_after_retries\\)/i.test(String(finalState.status ?? ""))
    const hasFinalAssistantCell = transcriptCells.some((cell: any) => {
      const role = String(cell?.role ?? cell?.speaker ?? "")
      const text = String(cell?.textPreview ?? cell?.text ?? "")
      return role === "assistant" && /final|committed/.test(String(cell?.lifecycle ?? "committed")) && text.trim().length > 0
    })
    const hasFinalAssistantConversation =
      (lastConversation?.speaker === "assistant" || lastConversation?.role === "assistant") &&
      String(lastConversation?.phase ?? "final") === "final"
    if (explicitFailedVerification) {
      add("failed-verification-forced-closure", "Run stopped with explicit failed-verification closure before producing a green SMTP implementation.")
    } else if (finalState.pendingResponse !== false || (!hasFinalAssistantCell && !hasFinalAssistantConversation)) {
      add("not-settled-final", "Final state did not settle to pendingResponse=false with a final assistant message.")
    }
    const preview = String(lastConversation?.preview ?? lastConversation?.text ?? "")
    if (promiseOnlyPattern.test(preview) && !/created|implemented|wrote|passes|compiled|usable/i.test(preview)) {
      add("promise-only-final", `Final assistant preview looks like a promise/progress update, not a completed result: ${preview}`)
    }
    if (observedToolCalls > 40) {
      add("excessive-tool-churn", `Expected bounded dummy task execution; provider/run-summary recorded ${observedToolCalls} tool calls.`, "warning")
    }
  }

  const snapshots = await readOptional(path.join(caseDir, "pty_snapshots.txt"))
  const snapshotSections = splitSnapshots(snapshots)
  const finalHistory = [...snapshotSections].reverse().find((section) => /final-history|settled|final/i.test(section.label)) ?? snapshotSections.at(-1)
  const readyCount = (finalHistory?.body.match(/Type your request/g) ?? []).length
  if (readyCount > 8) {
    add("scrollback-repaint-churn", `Final terminal-parsed history contains ${readyCount} composer placeholder copies.`, "warning")
  }
  if (/You are GPT-5\.\d+ running in the Codex CLI|Your capabilities:\s*- Receive user prompts/i.test(snapshots)) {
    add("prompt-leakage-visible", "Codex system prompt text appeared in visible PTY snapshots.")
  }

  if (await exists(path.join(workspace, "Makefile"))) {
    const make = await runCommand("make", ["clean", "all"], workspace, 30_000)
    if (make.timedOut) add("make-timeout", `make clean all timed out after 30000ms: ${(make.stderr || make.stdout).slice(0, 500)}`)
    if (make.code !== 0) add("make-failed", `make clean all failed with code ${make.code}: ${(make.stderr || make.stdout).slice(0, 500)}`)
  }
  if (await exists(path.join(workspace, "smoke_test.sh"))) {
    const smoke = await runCommand("bash", ["smoke_test.sh"], workspace, 45_000)
    if (smoke.timedOut) add("smoke-test-timeout", `smoke_test.sh timed out after 45000ms: ${(smoke.stderr || smoke.stdout).slice(0, 500)}`)
    if (smoke.code !== 0) add("smoke-test-failed", `smoke_test.sh failed with code ${smoke.code}: ${(smoke.stderr || smoke.stdout).slice(0, 500)}`)
  }

  return findings
}

const run = async () => {
  const { caseDir, strict } = parseArgs()
  const findings = await evaluateLiveSemanticSmtpTask(caseDir)
  const report = { caseDir, ok: findings.filter((finding) => finding.severity === "error").length === 0, findings }
  await fs.writeFile(path.join(caseDir, "live_semantic_smtp_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (strict && !report.ok) process.exitCode = 1
}

void run().catch((error) => {
  console.error((error as Error).message)
  process.exitCode = 1
})
