import fs from "node:fs/promises"
import path from "node:path"
import { spawn } from "node:child_process"

interface Finding {
  id: string
  message: string
  severity: "error" | "warning"
}

const parseArgs = () => {
  const args = process.argv.slice(2)
  let caseDir = ""
  let strict = false
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--case-dir") caseDir = args[++i] ?? ""
    else if (args[i] === "--strict") strict = true
  }
  if (!caseDir) throw new Error("missing --case-dir")
  return { caseDir: path.resolve(caseDir), strict }
}

const readOptional = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const parseNdjson = (text: string): any[] => {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => {
      try {
        return JSON.parse(line)
      } catch {
        return null
      }
    })
    .filter(Boolean)
}

const runCommand = (command: string, args: string[], cwd: string, timeoutMs: number): Promise<{ code: number | null; stdout: string; stderr: string; timedOut: boolean }> => {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] })
    let stdout = ""
    let stderr = ""
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      try { child.kill("SIGTERM") } catch {}
      setTimeout(() => { try { child.kill("SIGKILL") } catch {} }, 1500).unref()
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { stdout += String(chunk) })
    child.stderr.on("data", (chunk) => { stderr += String(chunk) })
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve({ code, stdout, stderr, timedOut })
    })
  })
}

const evaluate = async (caseDir: string): Promise<Finding[]> => {
  const findings: Finding[] = []
  const add = (id: string, message: string, severity: "error" | "warning" = "error") => findings.push({ id, message, severity })

  const caseInfoText = await readOptional(path.join(caseDir, "case_info.json"))
  const caseInfo = caseInfoText ? JSON.parse(caseInfoText) : null
  const workspace = String(caseInfo?.workspace ?? "")
  if (!workspace) add("missing-workspace", "case_info.json does not identify the dummy workspace.")
  const logDir = (await readOptional(path.join(caseDir, "logging_dir.txt"))).trim()
  const runSummaryText = logDir ? await readOptional(path.join(logDir, "meta", "run_summary.json")) : ""
  const runSummary = runSummaryText ? JSON.parse(runSummaryText) : null
  const toolUsage = runSummary?.tool_usage ?? {}
  const testCommands = Number(toolUsage?.test_commands ?? 0)
  const successfulTests = Number(toolUsage?.successful_tests ?? 0)
  const turnToolUsage = runSummary?.turn_tool_usage && typeof runSummary.turn_tool_usage === "object" ? runSummary.turn_tool_usage : {}
  const successfulTestCommandText = Object.values(turnToolUsage)
    .flatMap((turn: any) => (Array.isArray(turn?.tools) ? turn.tools : []))
    .filter((tool: any) => Boolean(tool?.success) && Boolean(tool?.meta?.is_test_command) && Number(tool?.meta?.exit_code ?? 0) === 0)
    .map((tool: any) => String(tool?.meta?.command ?? ""))
    .join("\n")
  if (runSummary) {
    if (testCommands < 1 || successfulTests < 1) {
      add("missing-session-verification", `Expected the live session to run at least one successful build/smoke command; saw test_commands=${testCommands}, successful_tests=${successfulTests}.`)
    }
    if (!/make clean all/i.test(successfulTestCommandText) || !/smoke_test\.sh/i.test(successfulTestCommandText)) {
      add("missing-requested-session-verification", `Session did not record the requested make clean all + smoke_test.sh verification command: ${successfulTestCommandText.slice(0, 500)}`)
    }
  }

  const stateRecords = parseNdjson(await readOptional(path.join(caseDir, "repl_state.ndjson")))
  const finalRecord = stateRecords.at(-1) ?? null
  const finalState = finalRecord?.state ?? null
  if (!finalState) {
    add("missing-final-state", "No final state dump record was captured.")
  } else {
    const conversation = Array.isArray(finalState.conversation) ? finalState.conversation : []
    const transcriptCells = Array.isArray(finalState.transcriptCells) ? finalState.transcriptCells : []
    const userRows = conversation.filter((row: any) => String(row?.speaker ?? row?.role ?? "") === "user")
    const assistantRows = conversation.filter((row: any) => String(row?.speaker ?? row?.role ?? "") === "assistant")
    const userCells = transcriptCells.filter((cell: any) => String(cell?.role ?? "") === "user-request")
    const assistantCells = transcriptCells.filter((cell: any) => String(cell?.role ?? "") === "assistant-message")
    if (finalState.pendingResponse !== false) add("not-settled", "Final state still has pendingResponse=true.")
    const status = String(finalState.status ?? "")
    if (!finalState.completionReached && !/^Finished\b/.test(status)) add("completion-not-reached", "Final state did not report completionReached=true or Finished status.")
    if (userRows.length + userCells.length < 1) add("user-row-count", `Expected one visible user request row/cell, saw ${userRows.length + userCells.length}.`)
    if (assistantRows.length + assistantCells.length < 1) add("missing-final-assistant", "No assistant row appeared in the readable conversation or transcript cells.")
    const finalText = String(
      assistantRows.at(-1)?.text
        ?? assistantCells.at(-1)?.text
        ?? runSummary?.completion_summary?.final_message
        ?? assistantCells.at(-1)?.textPreview
        ?? "",
    )
    if (!/make clean all|smoke_test\.sh|smoke test/i.test(finalText)) {
      add("final-missing-verification", `Final assistant text does not mention exact verification commands: ${finalText.slice(0, 300)}`)
    }
    if ((finalState.stats?.toolCount ?? 0) > 25) {
      add("excessive-tool-churn", `Expected small repair task tool use, saw ${finalState.stats?.toolCount ?? 0} tool events.`, "warning")
    }
  }

  const snapshots = await readOptional(path.join(caseDir, "pty_snapshots.txt"))
  if (/You are GPT-5\.|Your capabilities:/i.test(snapshots)) {
    add("prompt-leakage-visible", "System prompt text appeared in visible PTY snapshots.")
  }
  const finalHistory = snapshots.split(/^# turn1-final-history$/m).at(-1) ?? snapshots
  const promptCopies = (finalHistory.match(/tiny C project/g) ?? []).length
  if (promptCopies !== 1) add("prompt-copy-count", `Expected one visible prompt copy in final history, saw ${promptCopies}.`, "warning")

  if (workspace) {
    const diffNames = await runCommand("git", ["diff", "--name-only", "HEAD", "--"], workspace, 30_000)
    if (diffNames.code === 0) {
      const changed = diffNames.stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
      const unexpected = changed.filter((file) => file !== "calc.c" && file !== "calc" && !file.startsWith(".breadboard/"))
      if (!changed.includes("calc.c")) add("missing-calc-diff", `Expected calc.c to be changed; changed files: ${changed.join(", ") || "(none)"}`)
      if (unexpected.length > 0) add("unexpected-file-diff", `Tiny repair lane should only change calc.c; unexpected changes: ${unexpected.join(", ")}`)
    } else {
      add("git-diff-failed", `Unable to inspect workspace diff: ${(diffNames.stderr || diffNames.stdout).slice(0, 500)}`)
    }
    const smoke = await runCommand("bash", ["smoke_test.sh"], workspace, 30_000)
    if (smoke.timedOut) add("smoke-timeout", `smoke_test.sh timed out: ${(smoke.stderr || smoke.stdout).slice(0, 500)}`)
    if (smoke.code !== 0) add("smoke-failed", `smoke_test.sh failed with code ${smoke.code}: ${(smoke.stderr || smoke.stdout).slice(0, 500)}`)
    if (!smoke.stdout.includes("tiny-repair-smoke-ok")) add("missing-smoke-ok", `smoke output did not include tiny-repair-smoke-ok: ${smoke.stdout.slice(0, 500)}`)
    const calc = await readOptional(path.join(workspace, "calc.c"))
    if (!/(?:a|left)\s*\+\s*(?:b|right)/.test(calc)) add("bug-not-fixed", "calc.c does not contain an observable sum implementation.")
  }

  return findings
}

const run = async () => {
  const { caseDir, strict } = parseArgs()
  const findings = await evaluate(caseDir)
  const report = { caseDir, ok: findings.filter((finding) => finding.severity === "error").length === 0, findings }
  await fs.writeFile(path.join(caseDir, "live_repair_tiny_c_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (strict && !report.ok) process.exitCode = 1
}

void run().catch((error) => {
  console.error((error as Error).message)
  process.exitCode = 1
})
