import { promises as fs } from "node:fs"
import path from "node:path"
import { pathToFileURL } from "node:url"

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
  readonly transcriptCommittedCount?: number
  readonly transcriptTailCount?: number
  readonly landingRetired?: boolean
  readonly appendLandingToFeed?: boolean
  readonly warmLandingVisible?: boolean
  readonly landingLifecycleCommittedSnapshot?: boolean
}

export const parseSnapshots = (raw: string): SnapshotEntry[] => {
  const lines = raw.split(/\r?\n/)
  const entries: SnapshotEntry[] = []
  let currentLabel: string | null = null
  let buffer: string[] = []

  const flush = () => {
    if (!currentLabel) return
    entries.push({ label: currentLabel, body: buffer.join("\n") })
  }

  for (const line of lines) {
    if (line.startsWith("# ")) {
      flush()
      currentLabel = line.slice(2).trim()
      buffer = []
      continue
    }
    buffer.push(line)
  }
  flush()
  return entries
}

const parseSurfaceRecords = (raw: string): SurfaceModelRecord[] =>
  raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as SurfaceModelRecord)

export const evaluateLandingPersistence = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsRaw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const surfaceRaw = await fs.readFile(path.join(caseDir, "surface_model.ndjson"), "utf8")
  const snapshots = parseSnapshots(snapshotsRaw)
  const afterSubmit = snapshots.find((entry) => entry.label === "after-submit") ?? null

  if (!afterSubmit) {
    anomalies.push({ id: "missing-snapshot", message: 'Missing required snapshot "after-submit".' })
  } else {
    if (!afterSubmit.body.includes("Tips for getting started") && !afterSubmit.body.includes("BreadBoard v")) {
      anomalies.push({ id: "landing-missing", message: 'Snapshot "after-submit" does not retain the rich landing content.' })
    }
  }

  const surfaceRecords = parseSurfaceRecords(surfaceRaw)
  const pendingBeforeTail = surfaceRecords.filter(
    (record) =>
      record.pendingResponse === true &&
      (record.transcriptCommittedCount ?? 0) >= 1 &&
      (record.transcriptTailCount ?? 0) === 0,
  )

  if (pendingBeforeTail.length === 0) {
    anomalies.push({ id: "missing-surface-phase", message: "No pending-before-tail surface-model record found." })
  }

  if (
    pendingBeforeTail.some(
      (record) =>
        !record.landingLifecycleCommittedSnapshot &&
        (record.landingRetired === true || record.appendLandingToFeed === true || record.warmLandingVisible === false),
    )
  ) {
    anomalies.push({
      id: "landing-retired-too-early",
      message: "Landing retired, was appended to history, or stopped being warm before any assistant tail appeared.",
    })
  }

  return anomalies
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") {
      caseDir = args[++i]
    }
  }
  if (!caseDir) {
    throw new Error("--case-dir is required")
  }
  return { caseDir: path.resolve(caseDir) }
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLandingPersistence(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

const entryHref = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null
if (entryHref === import.meta.url) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
