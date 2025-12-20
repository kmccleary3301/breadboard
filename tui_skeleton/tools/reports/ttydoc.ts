import { promises as fs } from "node:fs"
import path from "node:path"

interface ReportOptions {
  readonly caseDir: string
  readonly caseId: string
  readonly scriptPath?: string
  readonly configPath?: string
}

const readText = async (filePath: string | null | undefined): Promise<string> => {
  if (!filePath) return ""
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const readJson = async (filePath: string | null | undefined): Promise<any> => {
  if (!filePath) return null
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"))
  } catch {
    return null
  }
}

const truncate = (value: string, max = 400): string => {
  if (value.length <= max) return value
  return `${value.slice(0, max)}…`
}

export const buildTtyDoc = async ({ caseDir, caseId, scriptPath, configPath }: ReportOptions) => {
  const timelineSummary = await readJson(path.join(caseDir, "timeline_summary.json"))
  const flamegraph = await readText(path.join(caseDir, "timeline_flamegraph.txt"))
  const anomalies = await readJson(path.join(caseDir, "anomalies.json"))
  const gridDiff = await readText(path.join(caseDir, "grid_snapshots", "final_vs_active.diff"))
  const finalSnapshot = await readText(path.join(caseDir, "grid_snapshots", "final.txt"))
  const activeSnapshot = await readText(path.join(caseDir, "grid_snapshots", "active.txt"))
  const summaryLines: string[] = []
  summaryLines.push(`# BreadBoard TTY Report — ${caseId}`)
  summaryLines.push(`script: ${scriptPath ?? "unknown"}`)
  summaryLines.push(`config: ${configPath ?? "unknown"}`)
  summaryLines.push("")
  summaryLines.push("## Timeline Summary")
  summaryLines.push(timelineSummary ? JSON.stringify(timelineSummary, null, 2) : "(missing timeline summary)")
  summaryLines.push("")
  summaryLines.push("## Chaos Metadata")
  if (timelineSummary?.chaos) {
    summaryLines.push(JSON.stringify(timelineSummary.chaos, null, 2))
  } else {
    summaryLines.push("(none)")
  }
  summaryLines.push("")
  summaryLines.push("## Flamegraph")
  summaryLines.push(flamegraph.trim().length > 0 ? flamegraph : "(missing flamegraph)")
  summaryLines.push("")
  summaryLines.push("## Layout Anomalies")
  summaryLines.push(anomalies ? JSON.stringify(anomalies, null, 2) : "[]")
  summaryLines.push("")
  summaryLines.push("## Final Snapshot (excerpt)")
  summaryLines.push(truncate(finalSnapshot, 600))
  summaryLines.push("")
  summaryLines.push("## Active Snapshot (excerpt)")
  summaryLines.push(truncate(activeSnapshot, 600))
  summaryLines.push("")
  summaryLines.push("## Final vs Active Diff")
  summaryLines.push(gridDiff.trim().length > 0 ? truncate(gridDiff, 1000) : "(no diff)")
  summaryLines.push("")
  const reportPath = path.join(caseDir, "ttydoc.txt")
  await fs.writeFile(reportPath, summaryLines.join("\n"), "utf8")
}
