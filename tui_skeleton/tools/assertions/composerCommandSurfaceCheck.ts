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

const SNAPSHOT_LABELS = new Set(["slash-default-suggestions", "transcript-open", "sessions-open"])

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

export const evaluateComposerCommandSurface = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs
    .readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
    .catch(() => fs.readFile(path.join(caseDir, "terminal_snapshots.txt"), "utf8"))
  const snapshots = parseSnapshots(snapshotsRaw)
  const byLabel = new Map(snapshots.map((entry) => [entry.label, entry.body]))

  for (const label of SNAPSHOT_LABELS) {
    if (!byLabel.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }
  if (anomalies.length > 0) return anomalies

  const suggestions = byLabel.get("slash-default-suggestions") ?? ""
  const requiredSuggestions = ["/resume", "/transcript", "/attach", "/models", "/shortcuts"]
  for (const token of requiredSuggestions) {
    if (!suggestions.includes(token)) {
      anomalies.push({ id: `missing-${token.slice(1)}`, message: `Default slash suggestion deck is missing ${token}.` })
    }
  }
  if (suggestions.includes("/quit") || suggestions.includes("/status")) {
    anomalies.push({ id: "legacy-default-suggestions-visible", message: "Bare slash still prioritizes legacy generic commands instead of continuation-focused commands." })
  }

  const transcript = byLabel.get("transcript-open") ?? ""
  if (!transcript.includes("follow tail") || !transcript.includes("g top")) {
    anomalies.push({ id: "transcript-open-missing", message: "The /transcript command did not open the transcript viewer with the expected navigation footer." })
  }

  const sessions = byLabel.get("sessions-open") ?? ""
  if (!sessions.includes("Enter attach") || !sessions.includes("sessions • lines")) {
    anomalies.push({ id: "sessions-overlay-missing", message: "The /sessions command did not open the recent-session overlay with the expected attach/session controls." })
  }


  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateComposerCommandSurface(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
