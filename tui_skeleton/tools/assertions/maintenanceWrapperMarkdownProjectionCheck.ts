import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface SnapshotEntry {
  readonly label: string
  readonly body: string
}

const normalizeHeadingLine = (line: string): string => line.trim().replace(/^#{1,6}\s+/, "").trim()

const parseSnapshots = (raw: string): SnapshotEntry[] => {
  const entries: SnapshotEntry[] = []
  let currentLabel: string | null = null
  let buffer: string[] = []
  const flush = () => {
    if (!currentLabel) return
    entries.push({ label: currentLabel, body: buffer.join("\n") })
  }
  for (const line of raw.split(/\r?\n/)) {
    if (line === "# after-answer") {
      flush()
      currentLabel = line.slice(2).trim()
      buffer = []
      continue
    }
    if (currentLabel) buffer.push(line)
  }
  flush()
  return entries
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

export const evaluateMaintenanceWrapperMarkdownProjection = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(snapshotsRaw)
  const afterAnswer = snapshots.find((entry) => entry.label === "after-answer")
  if (!afterAnswer) {
    return [{ id: "missing-after-answer", message: "Missing after-answer snapshot." }]
  }
  if (!afterAnswer.body.includes("table · stacked 2 cols")) {
    anomalies.push({ id: "missing-stacked-table-cue", message: "Settled snapshot is missing the stacked table cue." })
  }
  if (!afterAnswer.body.includes("Column: Alpha") || !afterAnswer.body.includes("Value: 1")) {
    anomalies.push({ id: "missing-stacked-table-content", message: "Settled snapshot is missing stacked table cell content." })
  }
  if (!/code · (?:ts|typescript)/.test(afterAnswer.body) || !afterAnswer.body.includes("const answer = 42")) {
    anomalies.push({ id: "missing-code-language-cue", message: "Settled snapshot is missing the fenced code language cue or body." })
  }
  if (!afterAnswer.body.includes("Done.")) {
    anomalies.push({ id: "missing-projection-final", message: "Settled snapshot is missing the final projection tail." })
  }
  const projectionHeadingCount = afterAnswer.body
    .split(/\r?\n/)
    .filter((line) => normalizeHeadingLine(line) === "Projection").length
  if (projectionHeadingCount > 1) {
    anomalies.push({ id: "duplicate-projection-heading", message: "Settled snapshot duplicated a raw markdown heading after rich projection." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMaintenanceWrapperMarkdownProjection(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
