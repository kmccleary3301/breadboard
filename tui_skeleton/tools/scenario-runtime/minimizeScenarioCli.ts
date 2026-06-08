import { spawn } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { ensureDir, writeJson, writeText } from "./reports/artifactBundle"
import { parseScenarioJson, type Scenario, type ScenarioStep } from "./schema"

interface Options {
  readonly scenarioPath: string
  readonly outPath: string
  readonly artifactRoot: string
  readonly lane: string
  readonly maxAttempts: number
  readonly targetFailureClass?: string
}

const usage = `Usage: pnpm scenario:minimize --scenario <scenario.json> --out <minimized.json> [--artifact-root <dir>] [--lane pty] [--max-attempts n] [--target-failure-class class]
`

const parseArgs = (argv: string[]): Options => {
  let scenarioPath = ""
  let outPath = ""
  let artifactRoot = "../docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v6_scenario_stress/artifacts/minimizer"
  let lane = "pty"
  let maxAttempts = 25
  let targetFailureClass: string | undefined
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage)
      process.exit(0)
    } else if (arg === "--scenario") scenarioPath = argv[++i] ?? ""
    else if (arg.startsWith("--scenario=")) scenarioPath = arg.split("=")[1] ?? ""
    else if (arg === "--out") outPath = argv[++i] ?? ""
    else if (arg.startsWith("--out=")) outPath = arg.split("=")[1] ?? ""
    else if (arg === "--artifact-root") artifactRoot = argv[++i] ?? artifactRoot
    else if (arg.startsWith("--artifact-root=")) artifactRoot = arg.split("=")[1] ?? artifactRoot
    else if (arg === "--lane") lane = argv[++i] ?? lane
    else if (arg.startsWith("--lane=")) lane = arg.split("=")[1] ?? lane
    else if (arg === "--max-attempts") maxAttempts = Number(argv[++i] ?? maxAttempts)
    else if (arg.startsWith("--max-attempts=")) maxAttempts = Number(arg.split("=")[1] ?? maxAttempts)
    else if (arg === "--target-failure-class") targetFailureClass = argv[++i]
    else if (arg.startsWith("--target-failure-class=")) targetFailureClass = arg.split("=")[1]
    else throw new Error(`unknown argument: ${arg}`)
  }
  if (!scenarioPath || !outPath) throw new Error(usage.trim())
  if (!Number.isFinite(maxAttempts) || maxAttempts < 1) throw new Error("--max-attempts must be >= 1")
  return { scenarioPath, outPath, artifactRoot, lane, maxAttempts, targetFailureClass }
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

const artifactDirFromStdout = (stdout: string): string | null => /\[scenario:run\] artifacts (.+)/.exec(stdout)?.[1] ?? null

const readFailureClassification = async (artifactDir: string | null, fallback: string): Promise<{ verdict: string; failureClass: string }> => {
  if (!artifactDir) return { verdict: fallback === "pass" ? "pass" : "fail", failureClass: fallback }
  try {
    const parsed = JSON.parse(await fs.readFile(path.join(artifactDir, "failure_classification.json"), "utf8")) as { verdict?: string; failureClass?: string }
    return { verdict: parsed.verdict ?? (fallback === "pass" ? "pass" : "fail"), failureClass: parsed.failureClass ?? fallback }
  } catch {
    return { verdict: fallback === "pass" ? "pass" : "fail", failureClass: fallback }
  }
}

const runCandidate = async (scenario: Scenario, tmpDir: string, artifactRoot: string, lane: string, index: number) => {
  const candidatePath = path.join(tmpDir, `candidate_${String(index).padStart(3, "0")}.json`)
  await writeJson(candidatePath, scenario)
  const result = await run("tsx", ["tools/scenario-runtime/runScenarioCli.ts", "--scenario", candidatePath, "--lane", lane, "--artifact-root", artifactRoot], process.cwd())
  const artifactDir = artifactDirFromStdout(result.stdout)
  const classification = await readFailureClassification(artifactDir, result.code === 0 ? "pass" : "unknown")
  return { ...result, artifactDir, ...classification }
}

const removable = (step: ScenarioStep): boolean => {
  if (step.kind === "submit" || step.kind === "checkpoint") return false
  if (step.kind === "waitFor") return false
  return true
}

const main = async () => {
  const options = parseArgs(process.argv.slice(2))
  const scenarioAbs = path.isAbsolute(options.scenarioPath) ? options.scenarioPath : path.join(process.cwd(), options.scenarioPath)
  const outAbs = path.isAbsolute(options.outPath) ? options.outPath : path.join(process.cwd(), options.outPath)
  const artifactRoot = path.isAbsolute(options.artifactRoot) ? options.artifactRoot : path.join(process.cwd(), options.artifactRoot)
  const tmpDir = path.join(artifactRoot, `tmp_${Date.now()}`)
  await ensureDir(tmpDir)

  let current = parseScenarioJson(await fs.readFile(scenarioAbs, "utf8"))
  const baseline = await runCandidate(current, tmpDir, artifactRoot, options.lane, 0)
  const targetFailureClass = options.targetFailureClass ?? baseline.failureClass
  if (baseline.verdict !== "fail" || baseline.failureClass !== targetFailureClass) {
    throw new Error(`baseline did not reproduce target failure class ${targetFailureClass}; got ${baseline.failureClass}`)
  }

  const attempts: Array<Record<string, unknown>> = [
    { attempt: 0, accepted: false, baseline: true, failureClass: baseline.failureClass, artifactDir: baseline.artifactDir, timelineLength: current.timeline.length },
  ]
  let attempt = 1
  for (let index = 0; index < current.timeline.length && attempt <= options.maxAttempts; ) {
    if (!removable(current.timeline[index])) {
      index += 1
      continue
    }
    const candidate: Scenario = { ...current, timeline: current.timeline.filter((_, itemIndex) => itemIndex !== index) }
    const result = await runCandidate(candidate, tmpDir, artifactRoot, options.lane, attempt)
    const accepted = result.verdict === "fail" && result.failureClass === targetFailureClass
    attempts.push({
      attempt,
      removedIndex: index,
      removedKind: current.timeline[index].kind,
      accepted,
      failureClass: result.failureClass,
      artifactDir: result.artifactDir,
      timelineLength: candidate.timeline.length,
    })
    attempt += 1
    if (accepted) current = candidate
    else index += 1
  }

  await writeJson(outAbs, { ...current, id: `${current.id}_minimized`, minimization: { enabled: true, preserveSteps: ["submit", "waitFor", "checkpoint"], } })
  await writeJson(path.join(artifactRoot, "minimization_report.json"), { schemaVersion: 1, source: scenarioAbs, out: outAbs, targetFailureClass, attempts })
  await writeText(
    path.join(artifactRoot, "minimization_report.md"),
    [
      "# Scenario Minimization Report",
      "",
      `- source: ${scenarioAbs}`,
      `- output: ${outAbs}`,
      `- targetFailureClass: ${targetFailureClass}`,
      `- attempts: ${attempts.length}`,
      `- initialTimelineLength: ${(attempts[0]?.timelineLength as number | undefined) ?? "unknown"}`,
      `- finalTimelineLength: ${current.timeline.length}`,
      `- acceptedDeletions: ${attempts.filter((entry) => entry.accepted === true).length}`,
      "",
    ].join("\n"),
  )
  console.log(`[scenario:minimize] wrote ${outAbs}`)
  console.log(`[scenario:minimize] report ${artifactRoot}`)
}

await main()
