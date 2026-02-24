import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const WEBAPP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const ARTIFACT_ROOT = path.resolve(WEBAPP_ROOT, "..", "artifacts", "webapp_e2e")
const REPORT_PATH = path.join(ARTIFACT_ROOT, "report.json")
const SUMMARY_PATH = path.join(ARTIFACT_ROOT, "summary.json")
const OUTPUT_PATH = path.join(ARTIFACT_ROOT, "quality_gate.json")

const REQUIRED_PROJECTS = ["desktop-chromium", "mobile-chromium"]
const REQUIRED_SCENARIOS = [
  "shell renders with deterministic diagnostics status",
  "keyboard navigation triggers diagnostics, send, and permission decision",
  "replay import hydrates transcript, tools, permissions, checkpoints, and task tree",
  "connection mode and token policy persist across reload",
  "live workflow: create attach send permissions checkpoints files and artifacts",
  "gap workflow: sequence gap surfaces recover flow and returns to active stream",
  "remote mode invalid base url surfaces validation error state",
  "remote mode auth failure surfaces 401 and recovers after token update",
  "resume window 409 surfaces gap recovery guidance",
  "diagnostics surfaces 5xx engine failures",
  "permission revoke unsupported path surfaces explicit error",
]
const REQUIRED_ATTACHMENTS = [
  "shell-diagnostics-ok",
  "replay-import-tools-panel",
  "replay-import-task-tree-panel",
  "connection-mode-persistence",
  "live-workflow-tools-panel",
  "live-workflow-permissions-panel",
  "gap-workflow-gap-state",
  "gap-workflow-recovered",
]
const PROJECT_DURATION_BUDGET_MS = {
  "desktop-chromium": {
    maxTestDurationMs: 45_000,
    totalProjectDurationMs: 240_000,
  },
  "mobile-chromium": {
    maxTestDurationMs: 70_000,
    totalProjectDurationMs: 360_000,
  },
}

const readJson = (filePath) => JSON.parse(readFileSync(filePath, "utf8"))

const collectSpecs = (suite, parentPath = []) => {
  const title = typeof suite?.title === "string" ? suite.title : ""
  const here = title ? [...parentPath, title] : parentPath
  const rows = []

  const specs = Array.isArray(suite?.specs) ? suite.specs : []
  for (const spec of specs) {
    const specTitle = typeof spec?.title === "string" ? spec.title : "(unnamed)"
    const tests = Array.isArray(spec?.tests) ? spec.tests : []
    for (const test of tests) {
      const results = Array.isArray(test?.results) ? test.results : []
      const finalResult = results[results.length - 1] ?? {}
      const attachments = Array.isArray(finalResult?.attachments) ? finalResult.attachments : []
      rows.push({
        title: specTitle,
        project: String(test?.projectName ?? "default"),
        status: typeof finalResult?.status === "string" ? finalResult.status : "unknown",
        durationMs: Number(finalResult?.duration ?? 0),
        path: [...here, specTitle],
        attachments: attachments.map((entry) => String(entry?.name ?? "")).filter((name) => name.length > 0),
      })
    }
  }

  const children = Array.isArray(suite?.suites) ? suite.suites : []
  for (const child of children) rows.push(...collectSpecs(child, here))
  return rows
}

const check = (name, ok, detail) => ({ name, ok, detail })
const percentile = (values, ratio) => {
  if (!Array.isArray(values) || values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * ratio) - 1))
  return sorted[index]
}

const main = () => {
  const report = readJson(REPORT_PATH)
  const summary = readJson(SUMMARY_PATH)
  const specs = (Array.isArray(report?.suites) ? report.suites : []).flatMap((suite) => collectSpecs(suite))
  const checks = []

  checks.push(check("summary.ok", summary?.ok === true, { ok: summary?.ok ?? null }))
  checks.push(check("summary.no_failures", Number(summary?.counts?.failed ?? 0) === 0, { failed: summary?.counts?.failed ?? null }))

  const expectedMinimumTotal = REQUIRED_PROJECTS.length * REQUIRED_SCENARIOS.length
  checks.push(
    check("scenario.total_minimum", specs.length >= expectedMinimumTotal, {
      expectedMinimumTotal,
      observedTotal: specs.length,
    }),
  )

  for (const project of REQUIRED_PROJECTS) {
    const projectRows = specs.filter((row) => row.project === project)
    checks.push(
      check(`project.present.${project}`, projectRows.length > 0, {
        project,
        observed: projectRows.length,
      }),
    )
    checks.push(
      check(`project.row_count_minimum.${project}`, projectRows.length >= REQUIRED_SCENARIOS.length, {
        expectedMinimum: REQUIRED_SCENARIOS.length,
        observed: projectRows.length,
      }),
    )

    const passedTitles = new Set(projectRows.filter((row) => row.status === "passed").map((row) => row.title))
    const missingScenarios = REQUIRED_SCENARIOS.filter((title) => !passedTitles.has(title))
    checks.push(
      check(`project.scenario_coverage.${project}`, missingScenarios.length === 0, {
        missingScenarios,
      }),
    )

    const attachmentNames = new Set(projectRows.flatMap((row) => row.attachments))
    const missingAttachments = REQUIRED_ATTACHMENTS.filter((name) => !attachmentNames.has(name))
    checks.push(
      check(`project.attachments.${project}`, missingAttachments.length === 0, {
        missingAttachments,
      }),
    )

    const durations = projectRows.map((row) => Number(row.durationMs ?? 0)).filter((value) => Number.isFinite(value) && value >= 0)
    const maxDurationMs = durations.length ? Math.max(...durations) : 0
    const totalDurationMs = durations.reduce((acc, value) => acc + value, 0)
    const p95DurationMs = percentile(durations, 0.95)
    const budget = PROJECT_DURATION_BUDGET_MS[project]
    if (budget) {
      checks.push(
        check(`project.duration.max.${project}`, maxDurationMs <= budget.maxTestDurationMs, {
          maxDurationMs,
          budgetMs: budget.maxTestDurationMs,
          p95DurationMs,
        }),
      )
      checks.push(
        check(`project.duration.total.${project}`, totalDurationMs <= budget.totalProjectDurationMs, {
          totalDurationMs,
          budgetMs: budget.totalProjectDurationMs,
          p95DurationMs,
        }),
      )
    }
  }

  const ok = checks.every((row) => row.ok)
  const payload = {
    ok,
    generatedAt: new Date().toISOString(),
    expected: {
      projects: REQUIRED_PROJECTS,
      scenarios: REQUIRED_SCENARIOS,
      attachments: REQUIRED_ATTACHMENTS,
      durationBudgetsMs: PROJECT_DURATION_BUDGET_MS,
    },
    observed: {
      rows: specs.length,
      projects: [...new Set(specs.map((row) => row.project))],
    },
    checks,
  }

  mkdirSync(ARTIFACT_ROOT, { recursive: true })
  writeFileSync(OUTPUT_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`)
  if (!ok) process.exit(1)
}

main()
