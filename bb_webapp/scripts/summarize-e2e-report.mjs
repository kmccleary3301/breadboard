import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const WEBAPP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const ARTIFACT_ROOT = path.resolve(WEBAPP_ROOT, "..", "artifacts", "webapp_e2e")
const REPORT_PATH = path.join(ARTIFACT_ROOT, "report.json")
const SUMMARY_JSON_PATH = path.join(ARTIFACT_ROOT, "summary.json")
const SUMMARY_MD_PATH = path.join(ARTIFACT_ROOT, "summary.md")

const readReport = () => {
  try {
    return JSON.parse(readFileSync(REPORT_PATH, "utf8"))
  } catch {
    return null
  }
}

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
      const status = typeof finalResult?.status === "string" ? finalResult.status : "unknown"
      const attachments = Array.isArray(finalResult?.attachments) ? finalResult.attachments : []
      rows.push({
        title: specTitle,
        path: [...here, specTitle],
        project: String(test?.projectName ?? "default"),
        status,
        durationMs: Number(finalResult?.duration ?? 0),
        attachments: attachments
          .map((entry) => ({
            name: String(entry?.name ?? ""),
            path: typeof entry?.path === "string" ? path.relative(WEBAPP_ROOT, entry.path) : null,
          }))
          .filter((entry) => entry.path),
      })
    }
  }

  const children = Array.isArray(suite?.suites) ? suite.suites : []
  for (const child of children) {
    rows.push(...collectSpecs(child, here))
  }
  return rows
}

const buildSummary = () => {
  const report = readReport()
  if (!report) {
    return {
      ok: false,
      error: "missing report.json",
      counts: {
        total: 0,
        passed: 0,
        failed: 0,
        skipped: 0,
        timedOut: 0,
        interrupted: 0,
      },
      failedTests: [],
    }
  }

  const suites = Array.isArray(report?.suites) ? report.suites : []
  const specs = suites.flatMap((suite) => collectSpecs(suite))
  const counts = {
    total: specs.length,
    passed: specs.filter((row) => row.status === "passed").length,
    failed: specs.filter((row) => row.status === "failed").length,
    skipped: specs.filter((row) => row.status === "skipped").length,
    timedOut: specs.filter((row) => row.status === "timedOut").length,
    interrupted: specs.filter((row) => row.status === "interrupted").length,
  }
  const projectBreakdown = {}
  for (const row of specs) {
    if (!projectBreakdown[row.project]) {
      projectBreakdown[row.project] = {
        total: 0,
        passed: 0,
        failed: 0,
        skipped: 0,
        timedOut: 0,
        interrupted: 0,
      }
    }
    const bucket = projectBreakdown[row.project]
    bucket.total += 1
    if (row.status === "passed") bucket.passed += 1
    if (row.status === "failed") bucket.failed += 1
    if (row.status === "skipped") bucket.skipped += 1
    if (row.status === "timedOut") bucket.timedOut += 1
    if (row.status === "interrupted") bucket.interrupted += 1
  }
  const failedTests = specs.filter((row) => row.status !== "passed" && row.status !== "skipped")
  return {
    ok: failedTests.length === 0,
    generatedAt: new Date().toISOString(),
    reportPath: path.relative(WEBAPP_ROOT, REPORT_PATH),
    counts,
    projectBreakdown,
    failedTests,
  }
}

const toMarkdown = (summary) => {
  const lines = []
  lines.push("# Webapp E2E Summary")
  lines.push("")
  lines.push(`- ok: \`${summary.ok}\``)
  if (summary.generatedAt) lines.push(`- generatedAt: \`${summary.generatedAt}\``)
  lines.push(`- total: \`${summary.counts.total}\``)
  lines.push(`- passed: \`${summary.counts.passed}\``)
  lines.push(`- failed: \`${summary.counts.failed}\``)
  lines.push(`- skipped: \`${summary.counts.skipped}\``)
  lines.push(`- timedOut: \`${summary.counts.timedOut}\``)
  lines.push(`- projects: \`${Object.keys(summary.projectBreakdown).length}\``)
  lines.push("")
  lines.push("## Project Breakdown")
  lines.push("")
  for (const [project, counts] of Object.entries(summary.projectBreakdown)) {
    lines.push(`- ${project}: total=\`${counts.total}\` passed=\`${counts.passed}\` failed=\`${counts.failed}\` skipped=\`${counts.skipped}\``)
  }
  lines.push("")
  if (summary.failedTests.length > 0) {
    lines.push("## Failures")
    lines.push("")
    for (const failure of summary.failedTests) {
      lines.push(`- ${failure.path.join(" > ")} [${failure.status}]`)
      for (const attachment of failure.attachments) {
        lines.push(`  - ${attachment.name}: \`${attachment.path}\``)
      }
    }
    lines.push("")
  }
  return `${lines.join("\n")}\n`
}

const main = () => {
  const summary = buildSummary()
  mkdirSync(ARTIFACT_ROOT, { recursive: true })
  writeFileSync(SUMMARY_JSON_PATH, `${JSON.stringify(summary, null, 2)}\n`, "utf8")
  writeFileSync(SUMMARY_MD_PATH, toMarkdown(summary), "utf8")
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`)
}

main()
