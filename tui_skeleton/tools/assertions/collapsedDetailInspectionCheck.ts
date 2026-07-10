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

const SNAPSHOT_LABELS = new Set(["collapsed-summary", "detail-open", "detail-close"])

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

export const evaluateCollapsedDetailInspection = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const plainRaw = await fs.readFile(path.join(caseDir, "pty_plain.txt"), "utf8").catch(() => snapshotsRaw)
  const snapshots = parseSnapshots(snapshotsRaw)
  const byLabel = new Map(snapshots.map((entry) => [entry.label, entry.body]))

  for (const label of SNAPSHOT_LABELS) {
    if (!byLabel.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }
  if (anomalies.length > 0) return anomalies

  const summary = byLabel.get("collapsed-summary") ?? ""
  const detailOpen = byLabel.get("detail-open") ?? ""
  const detailLast = plainRaw.lastIndexOf("Message detail")
  const collapsedLast = plainRaw.lastIndexOf("o inspect")
  if (!summary.includes("o inspect") || !summary.includes("hidden")) {
    anomalies.push({ id: "collapsed-contract-missing", message: "Collapsed summary does not expose the inspect contract." })
  }
  if (!detailOpen.includes("Message detail") || !detailOpen.includes("Detail line 01")) {
    anomalies.push({ id: "detail-open-missing", message: "Detail overlay did not open with the expected opening content." })
  }
  if (detailLast >= 0 && collapsedLast >= 0 && collapsedLast <= detailLast) {
    anomalies.push({ id: "detail-close-stale", message: "Detail close evidence did not return to the collapsed transcript summary after the detail sheet." })
  }
  if (collapsedLast < 0 || (detailLast >= 0 && collapsedLast <= detailLast)) {
    anomalies.push({ id: "detail-close-return-missing", message: "Detail close evidence did not return to the collapsed transcript summary." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateCollapsedDetailInspection(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
