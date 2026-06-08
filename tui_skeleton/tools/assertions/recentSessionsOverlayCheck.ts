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

const SNAPSHOT_LABELS = new Set(["recent-sessions-open", "recent-sessions-close"])

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

const extractSessionId = (raw: string): string | null => {
  for (const line of raw.split(/\r?\n/).reverse()) {
    if (!line.trim()) continue
    try {
      const parsed = JSON.parse(line) as { state?: { sessionId?: string } }
      const sessionId = parsed.state?.sessionId
      if (typeof sessionId === "string" && sessionId.trim()) return sessionId.trim()
    } catch {
      // ignore malformed lines
    }
  }
  return null
}

const hasBaseShellView = (body: string): boolean =>
  body.includes('Try "refactor <filepath>"') ||
  body.includes("Type your request") ||
  body.includes("enter send") ||
  body.includes("resume /sessions")

export const evaluateRecentSessionsOverlay = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const plainRaw = await fs.readFile(path.join(caseDir, "pty_plain.txt"), "utf8").catch(() => snapshotsRaw)
  const statePath = await fs
    .stat(path.join(caseDir, "repl_state.ndjson"))
    .then(() => path.join(caseDir, "repl_state.ndjson"))
    .catch(() => path.join(caseDir, "state_dump.ndjson"))
  const stateDumpRaw = await fs.readFile(statePath, "utf8")
  const snapshots = parseSnapshots(snapshotsRaw)
  const byLabel = new Map(snapshots.map((entry) => [entry.label, entry.body]))
  const sessionId = extractSessionId(stateDumpRaw)

  for (const label of SNAPSHOT_LABELS) {
    if (!byLabel.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }
  if (anomalies.length > 0) return anomalies

  const open = byLabel.get("recent-sessions-open") ?? ""
  const overlayLast = plainRaw.lastIndexOf("Recent sessions")
  const baseLast = plainRaw.lastIndexOf('Try "refactor <filepath>"')
  if (!open.includes("Recent sessions")) {
    anomalies.push({ id: "overlay-title-missing", message: "Recent-sessions snapshot is missing the overlay title." })
  }
  if (!open.includes("Enter attach") || !open.includes("R refresh")) {
    anomalies.push({ id: "overlay-hint-missing", message: "Recent-sessions snapshot is missing the attach/refresh hint contract." })
  }
  if (!sessionId || !open.includes(sessionId)) {
    anomalies.push({ id: "session-id-missing", message: "Recent-sessions snapshot does not show the active session id." })
  }
  const close = byLabel.get("recent-sessions-close") ?? ""
  const closeHasOverlay = close.includes("Recent sessions")
  const closeHasBaseShell = hasBaseShellView(close)
  if (overlayLast >= 0 && baseLast >= 0 && baseLast <= overlayLast) {
    anomalies.push({ id: "overlay-close-stale", message: "Recent-sessions close evidence did not return to the base shell after the overlay content." })
  }
  if (!closeHasBaseShell || closeHasOverlay) {
    anomalies.push({ id: "overlay-close-base-missing", message: "Recent-sessions close evidence did not return to the base shell view." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateRecentSessionsOverlay(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
