import { spawnSync } from "node:child_process"
import { writeFileSync } from "node:fs"
import path from "node:path"

interface Args {
  strict: boolean
  out: string | null
  markdownOut: string | null
}

interface BundleStepResult {
  readonly name: string
  readonly command: string
  readonly ok: boolean
  readonly exitCode: number
  readonly durationMs: number
  readonly tail: string[]
}

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let strict = false
  let out: string | null = null
  let markdownOut: string | null = null
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--strict") {
      strict = true
      continue
    }
    if (arg === "--out") {
      out = argv[index + 1] ?? null
      index += 1
      continue
    }
    if (arg === "--markdown-out") {
      markdownOut = argv[index + 1] ?? null
      index += 1
      continue
    }
  }
  return { strict, out, markdownOut }
}

const runStep = (name: string, command: string): BundleStepResult => {
  const started = Date.now()
  const result = spawnSync("bash", ["-lc", command], {
    cwd: process.cwd(),
    stdio: "pipe",
    encoding: "utf8",
  })
  const stdout = String(result.stdout ?? "")
  const stderr = String(result.stderr ?? "")
  const combined = `${stdout}${stderr}`.trim()
  const lines = combined.length > 0 ? combined.split(/\r?\n/) : []
  const tail = lines.slice(Math.max(0, lines.length - 16))
  const exitCode = typeof result.status === "number" ? result.status : 1
  return {
    name,
    command,
    ok: exitCode === 0,
    exitCode,
    durationMs: Date.now() - started,
    tail,
  }
}

const renderMarkdown = (summary: any): string => {
  const lines: string[] = []
  lines.push("# Subagents Runtime Bundle")
  lines.push("")
  lines.push(`- generatedAt: \`${summary.generatedAt}\``)
  lines.push(`- strict: \`${String(summary.strict)}\``)
  lines.push(`- total: \`${summary.total}\``)
  lines.push(`- failed: \`${summary.failed}\``)
  lines.push("")
  lines.push("| Step | Result | Exit | Duration (ms) |")
  lines.push("| --- | --- | --- | --- |")
  for (const step of summary.steps as BundleStepResult[]) {
    lines.push(`| ${step.name} | ${step.ok ? "ok" : "fail"} | ${step.exitCode} | ${step.durationMs} |`)
  }
  lines.push("")
  return `${lines.join("\n")}\n`
}

const main = (): void => {
  const args = parseArgs()
  const steps = [
    runStep("typecheck", "npm run typecheck"),
    runStep(
      "targeted-subagents-tests",
      "npm run test -- src/commands/repl/__tests__/controllerSubagentRouting.test.ts src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts src/repl/components/replView/controller/__tests__/taskFocusLoader.test.ts src/repl/components/replView/controller/__tests__/taskFocusCadence.test.ts src/repl/components/replView/controller/__tests__/diagnosticsHeatmap.test.ts src/tui_config/__tests__/resolveTuiConfig.test.ts",
    ),
    runStep("capture-cp0", "npm run subagents:capture:cp0"),
    runStep("capture-cp1", "npm run subagents:capture:cp1"),
    runStep("capture-cp2", "npm run subagents:capture:cp2"),
    runStep("capture-cp3", "npm run subagents:capture:cp3"),
    runStep("capture-cp3-cache", "npm run subagents:capture:cp3-cache"),
    runStep("capture-expansion", "npm run subagents:capture:expansion"),
    runStep("subagents-scenario-gate", "npm run runtime:gate:subagents-scenarios"),
    runStep("strip-churn-strict", "npm run runtime:gate:strip-churn"),
    runStep("focus-latency-strict", "npm run runtime:gate:focus-latency"),
    runStep(
      "rollback-level-validation",
      "mkdir -p ../artifacts/subagents_rollback && npm run subagents:validate:rollback -- --out ../artifacts/subagents_rollback/summary.json --markdown-out ../artifacts/subagents_rollback/summary.md",
    ),
    runStep("ascii-no-color", "npm run runtime:validate:ascii-no-color"),
  ]

  const failed = steps.filter((step) => !step.ok)
  const summary = {
    ok: failed.length === 0,
    strict: args.strict,
    generatedAt: new Date().toISOString(),
    total: steps.length,
    failed: failed.length,
    steps,
  }

  const json = JSON.stringify(summary, null, 2)
  console.log(json)
  if (args.out) writeFileSync(path.resolve(args.out), `${json}\n`, "utf8")
  if (args.markdownOut) writeFileSync(path.resolve(args.markdownOut), renderMarkdown(summary), "utf8")
  if (args.strict && !summary.ok) process.exit(1)
  process.exit(0)
}

main()
