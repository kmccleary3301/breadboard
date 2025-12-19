import { promises as fs } from "node:fs"
import path from "node:path"

interface KeyFuzzSummary {
  readonly runDir?: string
  readonly iterationsRequested?: number | null
  readonly iterationsCompleted?: number | null
  readonly stepsPerIteration?: number | null
  readonly seed?: number | null
  readonly failures?: string[] | null
}

export const buildKeyFuzzReport = async (batchDir: string, summary: KeyFuzzSummary | null | undefined) => {
  const reportsDir = path.join(batchDir, "reports")
  await fs.mkdir(reportsDir, { recursive: true })
  const reportPath = path.join(reportsDir, "key_fuzz_report.md")
  const lines: string[] = []
  lines.push("# Key-Fuzz Harness Report")
  if (!summary) {
    lines.push("")
    lines.push("Key-fuzz harness did not run for this batch.")
  } else {
    lines.push("")
    lines.push(`- Run directory: \`${summary.runDir ?? "(unknown)"}\``)
    lines.push(`- Iterations requested: ${summary.iterationsRequested ?? "n/a"}`)
    lines.push(`- Iterations completed: ${summary.iterationsCompleted ?? "n/a"}`)
    lines.push(`- Steps per iteration: ${summary.stepsPerIteration ?? "n/a"}`)
    lines.push(`- Seed: ${summary.seed ?? "n/a"}`)
    const failures = summary.failures ?? []
    lines.push(`- Failures: ${failures.length}`)
    if (failures.length > 0) {
      lines.push("")
      lines.push("## Failure Directories")
      for (const entry of failures) {
        lines.push(`- \`${entry}\``)
      }
      lines.push("")
      lines.push("To replay a failing iteration, run:")
      lines.push("```bash")
      lines.push("cd kylecode_cli_skeleton")
      lines.push("npm run devtools:key-fuzz -- --replay <failure-dir>")
      lines.push("```")
    }
  }
  lines.push("")
  await fs.writeFile(reportPath, lines.join("\n"), "utf8")
  return reportPath
}

