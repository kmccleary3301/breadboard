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

interface SurfaceModelRecord {
  readonly pendingResponse?: boolean
  readonly mainFollowTail?: boolean
  readonly transcriptTailCount?: number
}

interface StateDumpRecord {
  readonly state?: {
    readonly pendingResponse?: boolean
    readonly mainFollowTail?: boolean
  }
}

const SNAPSHOT_LABELS = new Set([
  "before-pause",
  "paused",
  "paused-after-tail-advance",
  "after-resume",
  "settled",
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

const readNdjson = async <T>(filePath: string): Promise<T[]> => {
  const text = await fs.readFile(filePath, "utf8")
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as T)
}

export const evaluateFollowPauseResume = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [snapshotsRaw, stateRecords, surfaceRecords] = await Promise.all([
    fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8"),
    readNdjson<StateDumpRecord>(path.join(caseDir, "repl_state.ndjson")),
    readNdjson<SurfaceModelRecord>(path.join(caseDir, "surface_model.ndjson")),
  ])

  const snapshots = parseSnapshots(snapshotsRaw)
  const beforePause = snapshots.find((entry) => entry.label === "before-pause") ?? null
  const paused = snapshots.find((entry) => entry.label === "paused") ?? null
  const pausedAfterTailAdvance = snapshots.find((entry) => entry.label === "paused-after-tail-advance") ?? null
  const afterResume = snapshots.find((entry) => entry.label === "after-resume") ?? null
  const settled = snapshots.find((entry) => entry.label === "settled") ?? null

  if (!beforePause || !paused || !pausedAfterTailAdvance || !afterResume || !settled) {
    anomalies.push({ id: "missing-snapshots", message: "Missing one or more required follow pause/resume snapshots." })
    return anomalies
  }

  const beforePauseShowsInitialDelta =
    (beforePause.body.includes("## Pause Follow") || beforePause.body.includes("Assistant streaming")) &&
    beforePause.body.includes("- first")
  if (!beforePauseShowsInitialDelta) {
    anomalies.push({ id: "before-pause-missing-first-delta", message: "Snapshot before-pause is missing the initial streamed markdown delta." })
  }

  if (!paused.body.includes("follow paused")) {
    anomalies.push({ id: "paused-footer-missing", message: "Snapshot paused does not show the paused follow footer state." })
  }

  for (const forbidden of ["- second", "- third", "Final."]) {
    if (pausedAfterTailAdvance.body.includes(forbidden)) {
      anomalies.push({
        id: 
          "paused-snapshot-leaked-" + forbidden.replace(/[^a-z0-9]+/gi, "-").toLowerCase(),
        message: "Snapshot paused-after-tail-advance unexpectedly exposed " + forbidden + " while follow was paused.",
      })
    }
  }

  if (!afterResume.body.includes("Final.")) {
    anomalies.push({ id: "after-resume-missing-final", message: "Snapshot after-resume does not show the resumed final markdown tail." })
  }

  if (!settled.body.includes("Final.") || !settled.body.includes("[ready]")) {
    anomalies.push({ id: "settled-missing-final-or-ready", message: "Snapshot settled does not contain the final content and ready footer." })
  }

  const pausedStateSeen = stateRecords.some((record) => record.state?.pendingResponse === true && record.state?.mainFollowTail === false)
  if (!pausedStateSeen) {
    anomalies.push({ id: "paused-state-not-seen", message: "No state dump record shows pendingResponse=true with mainFollowTail=false." })
  }

  const resumedStateSeen = stateRecords.some((record) => record.state?.pendingResponse === true && record.state?.mainFollowTail === true)
  if (!resumedStateSeen) {
    anomalies.push({ id: "resumed-state-not-seen", message: "No state dump record shows pendingResponse=true with mainFollowTail=true after resume." })
  }

  const pausedTailAdvanceSeen = surfaceRecords.some(
    (record) => record.pendingResponse === true && record.mainFollowTail === false && (record.transcriptTailCount ?? 0) > 0,
  )
  if (!pausedTailAdvanceSeen) {
    anomalies.push({ id: "paused-tail-advance-not-seen", message: "No surface-model record shows transcript tail growth while follow was paused." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateFollowPauseResume(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
