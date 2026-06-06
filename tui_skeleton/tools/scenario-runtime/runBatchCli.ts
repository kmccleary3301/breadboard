import { spawn } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { ensureDir, writeJson, writeText } from "./reports/artifactBundle"

type BatchScenario = string | { readonly path: string; readonly lane?: string; readonly expectedRed?: boolean }
interface BatchFile {
  readonly id: string
  readonly campaign?: string
  readonly scenarios: BatchScenario[]
  readonly expectedRed?: string[]
}
const run = (cmd: string, args: string[], cwd: string): Promise<{ code: number; stdout: string; stderr: string }> =>
  new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, env: process.env })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => (stdout += String(chunk)))
    child.stderr.on("data", (chunk) => (stderr += String(chunk)))
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }))
  })

const args = process.argv.slice(2)
let batchPath: string | undefined
let maxReds = Number.POSITIVE_INFINITY
let stopOnNewFailureClass = false
let laneOverride: string | undefined
let artifactRootOverride: string | undefined
for (let i = 0; i < args.length; i += 1) {
  const arg = args[i]
  if (arg === "--max-reds") maxReds = Number(args[++i] ?? "0")
  else if (arg === "--stop-on-new-failure-class") stopOnNewFailureClass = true
  else if (arg === "--continue-on-red") {
    // Default behavior is already continue-on-red; keep the flag explicit for campaign scripts.
  } else if (arg === "--lane") laneOverride = args[++i]
  else if (arg === "--artifact-root") artifactRootOverride = args[++i]
  else if (!batchPath && !arg.startsWith("--")) batchPath = arg
}
if (!batchPath) {
  console.error("Usage: tsx tools/scenario-runtime/runBatchCli.ts <batch.json> [--lane pty|wezterm|ghostty] [--max-reds n] [--continue-on-red] [--stop-on-new-failure-class]")
  process.exit(2)
}
const batchAbs = path.isAbsolute(batchPath) ? batchPath : path.join(process.cwd(), batchPath)
const batch = JSON.parse(await fs.readFile(batchAbs, "utf8")) as BatchFile
const artifactRoot = artifactRootOverride
  ? (path.isAbsolute(artifactRootOverride) ? artifactRootOverride : path.join(process.cwd(), artifactRootOverride))
  : path.join(process.cwd(), "../docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v6_scenario_stress/artifacts/campaigns", `${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}_${batch.id}`)
await ensureDir(artifactRoot)
const results: Array<Record<string, unknown>> = []
const observedFailureClasses = new Set<string>()
let redCount = 0
for (const scenarioEntry of batch.scenarios) {
  const scenario = typeof scenarioEntry === "string" ? scenarioEntry : scenarioEntry.path
  const lane = laneOverride ?? (typeof scenarioEntry === "string" ? undefined : scenarioEntry.lane)
  const expectedRed = (typeof scenarioEntry !== "string" && scenarioEntry.expectedRed === true) || (batch.expectedRed ?? []).includes(scenario)
  const runArgs = ["scenario:run", "--", "--scenario", scenario, "--artifact-root", artifactRoot]
  if (lane) runArgs.push("--lane", lane)
  const out = await run("pnpm", runArgs, process.cwd())
  const artifactMatch = /\[scenario:run\] artifacts (.+)/.exec(out.stdout)
  const artifactDir = artifactMatch?.[1] ?? null
  let failureClass = ""
  let actualVerdict = out.code === 0 ? "pass" : "fail"
  if (artifactDir) {
    try {
      const failure = JSON.parse(await fs.readFile(path.join(artifactDir, "failure_classification.json"), "utf8")) as {
        verdict?: string
        failureClass?: string
      }
      actualVerdict = failure.verdict ?? actualVerdict
      failureClass = failure.failureClass ?? ""
    } catch {
      // Keep campaign execution robust if a scenario died before writing classification.
    }
  }
  const expectationSatisfied = expectedRed ? actualVerdict === "fail" : out.code === 0 && actualVerdict === "pass"
  if (actualVerdict === "fail") {
    redCount += 1
    if (failureClass) observedFailureClasses.add(failureClass)
  }
  results.push({ scenario, lane: lane ?? "pty", code: out.code, actualVerdict, expectedRed, expectationSatisfied, failureClass, stdout: out.stdout, stderr: out.stderr, artifactDir })
  process.stdout.write(out.stdout)
  process.stderr.write(out.stderr)
  if (redCount >= maxReds) break
  if (stopOnNewFailureClass && observedFailureClasses.size > 0) break
}
const ok = results.every((result) => result.expectationSatisfied === true)
await writeJson(path.join(artifactRoot, "batch_manifest.json"), { schemaVersion: 1, batchId: batch.id, campaign: batch.campaign ?? null, ok, redCount, observedFailureClasses: Array.from(observedFailureClasses), results })
await writeText(path.join(artifactRoot, "batch_summary.md"), `# Scenario Batch ${batch.id}\n\n- ok: ${String(ok)}\n- scenarios: ${results.length}\n- redCount: ${redCount}\n- observedFailureClasses: ${Array.from(observedFailureClasses).join(", ") || "none"}\n\n${results.map((r) => `- ${r.scenario}: actual=${r.actualVerdict} expectedRed=${String(r.expectedRed)} expectation=${r.expectationSatisfied === true ? "satisfied" : "failed"}`).join("\n")}\n`)
const invariantRows = ["scenario,lane,actualVerdict,expectedRed,expectationSatisfied,artifactDir,failureClass,failedInvariants,actionFailures"]
const failureLines = [`# Scenario Batch Failures`, ``, `- batch: ${batch.id}`, `- ok: ${String(ok)}`, ``]
for (const result of results) {
  const artifactDir = typeof result.artifactDir === "string" ? result.artifactDir : ""
  let failedInvariants = ""
  let actionFailures = ""
  if (artifactDir) {
    try {
      const failure = JSON.parse(await fs.readFile(path.join(artifactDir, "failure_classification.json"), "utf8")) as {
        failedInvariants?: string[]
        actionFailures?: string[]
      }
      failedInvariants = (failure.failedInvariants ?? []).join("|")
      actionFailures = (failure.actionFailures ?? []).join("|")
    } catch {
      // Keep batch reporting robust for partially-created red artifact directories.
    }
  }
  invariantRows.push([
    JSON.stringify(result.scenario ?? ""),
    JSON.stringify(result.lane ?? ""),
    JSON.stringify(result.actualVerdict ?? ""),
    String(result.expectedRed === true),
    String(result.expectationSatisfied === true),
    JSON.stringify(artifactDir),
    JSON.stringify(result.failureClass ?? ""),
    JSON.stringify(failedInvariants),
    JSON.stringify(actionFailures),
  ].join(","))
  if (result.expectationSatisfied !== true) {
    failureLines.push(`- ${result.scenario}: expectation failed`)
    failureLines.push(`  artifactDir: ${artifactDir || "not captured"}`)
    failureLines.push(`  actualVerdict: ${String(result.actualVerdict)}`)
    failureLines.push(`  expectedRed: ${String(result.expectedRed === true)}`)
    if (failedInvariants) failureLines.push(`  failedInvariants: ${failedInvariants}`)
    if (actionFailures) failureLines.push(`  actionFailures: ${actionFailures}`)
  }
}
if (ok) failureLines.push("No failed scenarios.")
await writeText(path.join(artifactRoot, "batch_invariant_matrix.csv"), `${invariantRows.join("\n")}\n`)
await writeText(path.join(artifactRoot, "batch_failures.md"), `${failureLines.join("\n")}\n`)
await writeText(path.join(artifactRoot, "latest_green_pointer.md"), ok ? `# Latest Green\n\n${artifactRoot}\n` : "# Latest Green\n\nBatch failed; no green pointer.\n")
console.log(`[scenario:batch] ${ok ? "PASS" : "FAIL"} ${batch.id}`)
console.log(`[scenario:batch] artifacts ${artifactRoot}`)
if (!ok) process.exit(1)
