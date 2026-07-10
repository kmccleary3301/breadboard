import { promises as fs } from "node:fs"
import path from "node:path"
import { pathToFileURL } from "node:url"
import { parseSnapshots } from "./liveWrapperLandingPersistenceCheck.ts"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface SurfaceModelRecord {
  readonly landingRetired?: boolean
  readonly warmLandingVisible?: boolean
  readonly showSessionHeaderInline?: boolean
  readonly appendLandingToFeed?: boolean
  readonly pendingResponse?: boolean
  readonly activeWindowOvershoot?: boolean
}

const parseSurfaceRecords = (raw: string): SurfaceModelRecord[] =>
  raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as SurfaceModelRecord)

const validateCompactSnapshot = (
  snapshotBody: string,
  anomalies: LayoutAnomaly[],
  label: string,
  options?: { requireIdentity?: boolean },
) => {
  if (snapshotBody.includes("Tips for getting started")) {
    anomalies.push({
      id: `rich-landing-revived-inline-${label}`,
      message: `Compact session-header snapshot ${label} still contains rich landing text.`,
    })
  }
  const hasCompactPathIdentity = /^\S+\s+·\s+\/.+/m.test(snapshotBody)
  const hasFooterTurnIdentity = /\bmdl\s+\S+.*\bturn\s+\d+/m.test(snapshotBody)
  if ((options?.requireIdentity ?? true) && !hasCompactPathIdentity && !hasFooterTurnIdentity) {
    anomalies.push({
      id: `missing-compact-session-identity-${label}`,
      message: `Compact session-header snapshot ${label} does not contain the compact identity row.`,
    })
  }
  if (!snapshotBody.includes("turn ")) {
    anomalies.push({
      id: `missing-turn-indicator-${label}`,
      message: `Compact session-header snapshot ${label} does not contain a turn indicator.`,
    })
  }
  if (!snapshotBody.includes("Verification: verification receipt present")) {
    anomalies.push({
      id: `missing-settled-output-${label}`,
      message: `Compact session-header snapshot ${label} does not contain the mock C-filesystem verification receipt.`,
    })
  }
}

export const evaluateMaintenanceWrapperCompactSessionHeader = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [snapshotsRaw, surfaceRaw] = await Promise.all([
    fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8"),
    fs.readFile(path.join(caseDir, "surface_model.ndjson"), "utf8"),
  ])

  const snapshots = parseSnapshots(snapshotsRaw)
  const afterResizeSmall = snapshots.find((entry) => entry.label === "after-resize-small") ?? null
  const afterAnswerSmall = snapshots.find((entry) => entry.label === "after-answer-small") ?? null
  const afterResizeTight = snapshots.find((entry) => entry.label === "after-resize-tight") ?? null

  if (!afterResizeSmall) {
    anomalies.push({ id: "missing-snapshot", message: 'Missing required snapshot "after-resize-small".' })
  }
  if (!afterAnswerSmall) {
    anomalies.push({ id: "missing-snapshot", message: 'Missing required snapshot "after-answer-small".' })
  }
  if (!afterResizeTight) {
    anomalies.push({ id: "missing-snapshot", message: 'Missing required snapshot "after-resize-tight".' })
  }

  const surfaceRecords = parseSurfaceRecords(surfaceRaw)
  const headerInlineRecord = surfaceRecords.find(
    (record) =>
      record.landingRetired === true &&
      record.warmLandingVisible === false &&
      record.showSessionHeaderInline === true,
  )
  const staticLandingPreservedRecord = surfaceRecords.find(
    (record) =>
      record.landingRetired === true &&
      record.warmLandingVisible === false &&
      record.appendLandingToFeed === true &&
      record.pendingResponse === false,
  )

  if (!headerInlineRecord && !staticLandingPreservedRecord) {
    anomalies.push({
      id: "missing-retired-landing-orientation-state",
      message: "No surface-model record showed either inline header orientation or preserved retired landing orientation.",
    })
  }

  const overshootRecord = surfaceRecords.find((record) => record.activeWindowOvershoot === true)
  if (overshootRecord) {
    anomalies.push({
      id: "compact-active-window-overshoot",
      message: "Compact session-header regression case overshot its managed active window.",
    })
  }

  if (afterAnswerSmall) {
    validateCompactSnapshot(afterAnswerSmall.body, anomalies, "after-answer-small")
  }
  if (afterResizeTight) {
    validateCompactSnapshot(afterResizeTight.body, anomalies, "after-resize-tight", { requireIdentity: false })
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
  const anomalies = await evaluateMaintenanceWrapperCompactSessionHeader(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

const entryHref = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null
if (entryHref === import.meta.url) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
