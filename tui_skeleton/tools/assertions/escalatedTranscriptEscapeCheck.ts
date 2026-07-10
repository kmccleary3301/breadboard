import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
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

const parseSnapshots = (raw: string): Map<string, string> => {
  const map = new Map<string, string>()
  let current: string | null = null
  let lines: string[] = []
  const flush = () => {
    if (!current) return
    map.set(current, lines.join("\n"))
  }
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("# ")) {
      flush()
      current = line.slice(2).trim()
      lines = []
      continue
    }
    if (current) lines.push(line)
  }
  flush()
  return map
}

export const evaluateEscalatedTranscriptEscape = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsPath = path.join(caseDir, "terminal_snapshots.txt")
  const snapshotsRaw = await fs.readFile(snapshotsPath, "utf8")
  const snapshots = parseSnapshots(snapshotsRaw)
  const viewerOpen = snapshots.get("viewer-open") ?? ""
  const viewerBack = snapshots.get("viewer-back") ?? ""

  if (!viewerOpen) {
    anomalies.push({ id: "missing-viewer-open", message: "Missing viewer-open snapshot." })
  }
  if (!viewerBack) {
    anomalies.push({ id: "missing-viewer-back", message: "Missing viewer-back snapshot." })
  }
  if (anomalies.length > 0) return anomalies

  if (!viewerOpen.includes("follow tail") || !viewerOpen.includes("g top")) {
    anomalies.push({
      id: "transcript-open-contract-missing",
      message: "Escalated transcript open snapshot does not show the expected transcript viewer contract.",
    })
  }

  const viewerBackLower = viewerBack.toLowerCase()
  const hasTranscriptAffordance =
    viewerBackLower.includes("ctrl+o transcript") ||
    viewerBackLower.includes("ctrl+t transcript")
  if (!viewerBackLower.includes("owned viewport host") || !hasTranscriptAffordance) {
    anomalies.push({
      id: "transcript-return-contract-missing",
      message: "Escalated transcript return snapshot does not show the owned viewport host with the transcript affordance restored.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateEscalatedTranscriptEscape(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
