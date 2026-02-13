import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

interface Args {
  fixtures: string[]
  strict: boolean
  threshold: number
  out: string | null
  markdownOut: string | null
}

interface FixtureGateResult {
  fixture: string
  ok: boolean
  acceptanceChecks: Record<string, boolean>
  failedChecks: string[]
  taskToolRailLinesMax: number
  noiseRatios: number[]
  threshold: number
  errors: string[]
}

const DEFAULT_FIXTURES = [
  "docs/subagents_scenarios/cp1_async_completion_capture_20260213.json",
  "docs/subagents_scenarios/cp1_failure_sticky_toast_capture_20260213.json",
  "docs/subagents_scenarios/cp1_ascii_no_color_fallback_capture_20260213.json",
  "docs/subagents_scenarios/cp2_sync_progression_capture_20260213.json",
  "docs/subagents_scenarios/cp2_async_wakeup_output_ready_capture_20260213.json",
  "docs/subagents_scenarios/cp2_failure_retry_completion_capture_20260213.json",
  "docs/subagents_scenarios/cp2_concurrency_20_tasks_capture_20260213.json",
  "docs/subagents_scenarios/cp3_focus_active_updates_capture_20260213.json",
  "docs/subagents_scenarios/cp3_focus_rapid_lane_switch_capture_20260213.json",
]

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let fixtures: string[] = [...DEFAULT_FIXTURES]
  let strict = false
  let threshold = 0.8
  let out: string | null = null
  let markdownOut: string | null = null
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--fixtures") {
      const raw = argv[index + 1] ?? ""
      fixtures = raw
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean)
      index += 1
      continue
    }
    if (arg === "--strict") {
      strict = true
      continue
    }
    if (arg === "--threshold") {
      const parsed = Number(argv[index + 1] ?? "0.8")
      threshold = Number.isFinite(parsed) ? parsed : 0.8
      index += 1
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
  return { fixtures, strict, threshold, out, markdownOut }
}

const walk = (value: unknown, visit: (entry: unknown, keyPath: string[]) => void, keyPath: string[] = []): void => {
  visit(value, keyPath)
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) walk(value[index], visit, [...keyPath, String(index)])
    return
  }
  if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) walk(inner, visit, [...keyPath, key])
  }
}

const evaluateFixture = (fixturePath: string, threshold: number): FixtureGateResult => {
  const errors: string[] = []
  const acceptanceChecks: Record<string, boolean> = {}
  const failedChecks: string[] = []
  const taskToolRailValues: number[] = []
  const noiseRatios: number[] = []
  try {
    const raw = readFileSync(fixturePath, "utf8")
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const checksRaw = parsed.acceptanceChecks
    if (!checksRaw || typeof checksRaw !== "object" || Array.isArray(checksRaw)) {
      errors.push("missing acceptanceChecks map")
    } else {
      for (const [check, value] of Object.entries(checksRaw as Record<string, unknown>)) {
        const isTrue = value === true
        acceptanceChecks[check] = isTrue
        if (!isTrue) failedChecks.push(check)
      }
    }
    walk(parsed, (entry, keyPath) => {
      const key = keyPath[keyPath.length - 1] ?? ""
      if (key === "taskToolRailLines" && typeof entry === "number" && Number.isFinite(entry)) {
        taskToolRailValues.push(entry)
      }
      if (key === "ratio" && typeof entry === "number" && Number.isFinite(entry)) {
        const parent = keyPath[keyPath.length - 2] ?? ""
        if (parent === "metrics") noiseRatios.push(entry)
      }
    })
  } catch (error) {
    errors.push((error as Error).message)
  }

  const taskToolRailLinesMax = taskToolRailValues.length > 0 ? Math.max(...taskToolRailValues) : 0
  if (taskToolRailLinesMax > 0) {
    errors.push(`taskToolRailLines exceeded budget: max=${taskToolRailLinesMax}`)
  }

  const noiseExceeded = noiseRatios.some((ratio) => ratio > threshold)
  if (noiseExceeded) {
    errors.push(`noise ratio exceeded threshold ${threshold}`)
  }

  const ok = failedChecks.length === 0 && errors.length === 0
  return {
    fixture: fixturePath,
    ok,
    acceptanceChecks,
    failedChecks,
    taskToolRailLinesMax,
    noiseRatios,
    threshold,
    errors,
  }
}

const toMarkdown = (summary: any): string => {
  const lines: string[] = []
  lines.push("# Subagents Scenario Gate")
  lines.push("")
  lines.push(`- generatedAt: \`${summary.generatedAt}\``)
  lines.push(`- strict: \`${String(summary.strict)}\``)
  lines.push(`- threshold: \`${summary.threshold}\``)
  lines.push(`- total: \`${summary.total}\``)
  lines.push(`- failed: \`${summary.failed}\``)
  lines.push("")
  lines.push("| Fixture | Result | Failed Checks | Errors |")
  lines.push("| --- | --- | --- | --- |")
  for (const fixture of summary.fixtures as FixtureGateResult[]) {
    const failedChecks = fixture.failedChecks.length > 0 ? fixture.failedChecks.join(", ") : "-"
    const errors = fixture.errors.length > 0 ? fixture.errors.join("; ") : "-"
    lines.push(`| \`${fixture.fixture}\` | ${fixture.ok ? "ok" : "fail"} | ${failedChecks} | ${errors} |`)
  }
  lines.push("")
  return `${lines.join("\n")}\n`
}

const main = (): void => {
  const args = parseArgs()
  const resolvedFixtures = args.fixtures.map((fixture) => path.resolve(fixture))
  const fixtureResults = resolvedFixtures.map((fixture) => evaluateFixture(fixture, args.threshold))
  const failed = fixtureResults.filter((entry) => !entry.ok)
  const summary = {
    ok: failed.length === 0,
    strict: args.strict,
    threshold: args.threshold,
    generatedAt: new Date().toISOString(),
    total: fixtureResults.length,
    failed: failed.length,
    fixtures: fixtureResults.map((entry) => ({
      ...entry,
      fixture: path.relative(process.cwd(), entry.fixture),
    })),
  }

  const output = JSON.stringify(summary, null, 2)
  console.log(output)
  if (args.out) writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  if (args.markdownOut) writeFileSync(path.resolve(args.markdownOut), toMarkdown(summary), "utf8")
  if (args.strict && !summary.ok) process.exit(1)
  process.exit(0)
}

main()
