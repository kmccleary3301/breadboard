import { promises as fs } from "node:fs"
import path from "node:path"

interface SurfaceRecord {
  readonly ts?: number
  readonly pendingResponse?: boolean
  readonly landingRetired?: boolean
  readonly landingShouldRetireNow?: boolean
  readonly appendLandingToFeed?: boolean
  readonly landingLifecycleCommittedSnapshot?: boolean
  readonly warmLandingVisible?: boolean
  readonly activeWindowHiddenCount?: number
  readonly activeWindowTruncated?: boolean
  readonly transcriptCommittedCount?: number
  readonly transcriptTailCount?: number
}

interface ResetRecord {
  readonly ts?: number
}

export interface ManagedRegionReclaimAudit {
  readonly viewportResetCount: number
  readonly nonInitialViewportResetCount: number
  readonly landingRetiredDuringPending: boolean
  readonly firstPendingRetirementTs: number | null
  readonly settledTruncationObserved: boolean
  readonly maxHiddenCountAfterSettlement: number
  readonly finalState: {
    readonly pendingResponse: boolean | null
    readonly landingRetired: boolean | null
    readonly warmLandingVisible: boolean | null
    readonly activeWindowHiddenCount: number | null
    readonly activeWindowTruncated: boolean | null
    readonly transcriptCommittedCount: number | null
    readonly transcriptTailCount: number | null
  }
}

const readNdjson = async <T>(filePath: string): Promise<T[]> => {
  try {
    const text = await fs.readFile(filePath, "utf8")
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as T)
  } catch {
    return []
  }
}

export const buildManagedRegionReclaimAudit = async (caseDir: string): Promise<ManagedRegionReclaimAudit> => {
  const [surface, resets] = await Promise.all([
    readNdjson<SurfaceRecord>(path.join(caseDir, "surface_model.ndjson")),
    readNdjson<ResetRecord>(path.join(caseDir, "viewport_resets.ndjson")),
  ])

  const pendingRetirement = surface.find(
    (record) =>
      record.pendingResponse === true &&
      (record.landingShouldRetireNow === true ||
        (record.landingRetired === true &&
          record.appendLandingToFeed !== true &&
          record.landingLifecycleCommittedSnapshot !== true)),
  )

  const settled = surface.filter((record) => record.pendingResponse === false)
  const final = surface[surface.length - 1] ?? null

  return {
    viewportResetCount: resets.length,
    nonInitialViewportResetCount: Math.max(0, resets.length - 1),
    landingRetiredDuringPending: Boolean(pendingRetirement),
    firstPendingRetirementTs: pendingRetirement?.ts ?? null,
    settledTruncationObserved: settled.some(
      (record) => record.activeWindowTruncated === true || (record.activeWindowHiddenCount ?? 0) > 0,
    ),
    maxHiddenCountAfterSettlement: settled.reduce(
      (max, record) => Math.max(max, Number(record.activeWindowHiddenCount ?? 0)),
      0,
    ),
    finalState: {
      pendingResponse: final?.pendingResponse ?? null,
      landingRetired: final?.landingRetired ?? null,
      warmLandingVisible: final?.warmLandingVisible ?? null,
      activeWindowHiddenCount: typeof final?.activeWindowHiddenCount === "number" ? final.activeWindowHiddenCount : null,
      activeWindowTruncated: final?.activeWindowTruncated ?? null,
      transcriptCommittedCount: typeof final?.transcriptCommittedCount === "number" ? final.transcriptCommittedCount : null,
      transcriptTailCount: typeof final?.transcriptTailCount === "number" ? final.transcriptTailCount : null,
    },
  }
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir }
}

const run = async () => {
  const { caseDir } = parseArgs()
  const report = await buildManagedRegionReclaimAudit(caseDir)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
