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

const SNAPSHOT_LABELS = new Set(["tool-selected", "result-detail-open", "artifact-preview-open"])

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const parseSnapshots = (raw: string): SnapshotEntry[] => {
  const entries: SnapshotEntry[] = []
  let currentLabel: string | null = null
  let buffer: string[] = []
  const flush = () => {
    if (!currentLabel) return
    entries.push({ label: currentLabel, body: buffer.join("\n") })
  }
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("# ")) {
      const candidate = line.slice(2).trim()
      if (SNAPSHOT_LABELS.has(candidate)) {
        flush()
        currentLabel = candidate
        buffer = []
        continue
      }
    }
    if (currentLabel) buffer.push(line)
  }
  flush()
  return entries
}

export const evaluateTranscriptResultActionability = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const plainRaw = await fs.readFile(path.join(caseDir, "pty_plain.txt"), "utf8").catch(() => "")
  const snapshots = parseSnapshots(snapshotsRaw)
  const byLabel = new Map(snapshots.map((entry) => [entry.label, entry.body]))

  for (const label of SNAPSHOT_LABELS) {
    if (!byLabel.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }
  if (anomalies.length > 0) return anomalies

  const selected = byLabel.get("tool-selected") ?? ""
  const detailOpen = byLabel.get("result-detail-open") ?? ""
  const artifactOpen = byLabel.get("artifact-preview-open") ?? ""

  if (!selected.includes("Write(report.txt)") || !selected.includes("o inspect")) {
    anomalies.push({
      id: "tool-action-contract-missing",
      message: "Transcript tool selection does not expose the inspect contract.",
    })
  }
  if (!selected.includes("Enter open artifact")) {
    anomalies.push({
      id: "artifact-action-hint-missing",
      message: "Transcript tool selection does not expose the artifact-open contract.",
    })
  }
  const detailEvidence = `${detailOpen}\n${plainRaw}`
  if (!detailEvidence.includes("Result detail") || !detailEvidence.includes("Artifact persisted for transcript inspection.")) {
    anomalies.push({
      id: "result-detail-missing",
      message: "Result detail overlay did not open with the expected detail content.",
    })
  }
  if (!artifactOpen.includes("Artifact preview") || !artifactOpen.includes("scripts/fixtures/p3_result_actionability_report.txt")) {
    anomalies.push({
      id: "artifact-preview-header-missing",
      message: "Artifact preview overlay is missing the expected title or artifact path.",
    })
  }
  if (!artifactOpen.includes("P3 artifact preview line 01") || !artifactOpen.includes("Preview truncated to 4 lines.")) {
    anomalies.push({
      id: "artifact-preview-content-missing",
      message: "Artifact preview overlay is missing the expected preview lines or truncation note.",
    })
  }



  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateTranscriptResultActionability(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
