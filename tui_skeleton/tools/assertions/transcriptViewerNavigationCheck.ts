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

const SNAPSHOT_LABELS = new Set([
  "viewer-follow",
  "viewer-search",
  "viewer-top",
  "viewer-user-anchor",
  "viewer-assistant-anchor",
  "viewer-tail",
  "back",
  "viewer-reopen",
])

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

export const evaluateTranscriptViewerNavigation = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsPath = await (async () => {
    const ptyPath = path.join(caseDir, "pty_snapshots.txt")
    try {
      await fs.access(ptyPath)
      return ptyPath
    } catch {}
    const terminalPath = path.join(caseDir, "terminal_snapshots.txt")
    await fs.access(terminalPath)
    return terminalPath
  })()
  const snapshotsRaw = await fs.readFile(snapshotsPath, "utf8")
  const snapshots = parseSnapshots(snapshotsRaw)
  const byLabel = new Map(snapshots.map((entry) => [entry.label, entry.body]))

  for (const label of SNAPSHOT_LABELS) {
    if (!byLabel.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }
  if (anomalies.length > 0) return anomalies

  const follow = byLabel.get("viewer-follow") ?? ""
  const search = byLabel.get("viewer-search") ?? ""
  const top = byLabel.get("viewer-top") ?? ""
  const userAnchor = byLabel.get("viewer-user-anchor") ?? ""
  const assistantAnchor = byLabel.get("viewer-assistant-anchor") ?? ""
  const tail = byLabel.get("viewer-tail") ?? ""
  const back = byLabel.get("back") ?? ""
  const reopen = byLabel.get("viewer-reopen") ?? ""

  if (!follow.includes("follow tail") || !follow.includes("g top")) {
    anomalies.push({ id: "initial-follow-contract-missing", message: "viewer-follow does not show the expected initial follow-tail contract." })
  }
  if (!search.includes("Search: ello")) {
    anomalies.push({ id: "search-contract-missing", message: "viewer-search does not show the expected search contract." })
  }
  if (!top.includes("inspect ") || !top.includes("G tail")) {
    anomalies.push({ id: "top-contract-missing", message: "viewer-top does not show the expected inspect contract." })
  }
  if (!userAnchor.includes("Hello transcript")) {
    anomalies.push({ id: "user-anchor-missing", message: "viewer-user-anchor does not show the expected user anchor context." })
  }
  if (!assistantAnchor.includes("Mock assistant: hello world")) {
    anomalies.push({
      id: "assistant-anchor-missing",
      message: "viewer-assistant-anchor does not show the expected last-assistant anchor context.",
    })
  }
  if (!tail.includes("follow tail")) {
    anomalies.push({ id: "tail-return-missing", message: "viewer-tail does not return to the follow-tail contract." })
  }
  const backLower = back.toLowerCase()
  const backIsComposer = (backLower.includes("[ready]") || backLower.includes("enter send")) && !backLower.includes("press / to search")
  const backShowsTranscriptAffordance =
    backLower.includes("ctrl+o transcript") ||
    backLower.includes("ctrl+t transcript") ||
    (backIsComposer && backLower.includes("ctrl+t"))
  if (!backShowsTranscriptAffordance) {
    anomalies.push({ id: "footer-transcript-hint-missing", message: "back snapshot does not expose the transcript footer affordance." })
  }
  if (!reopen.includes("inspect ") || reopen.includes("follow tail")) {
    anomalies.push({
      id: "reopen-position-memory-missing",
      message: "viewer-reopen did not preserve the last detached viewer position.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateTranscriptViewerNavigation(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
