import { promises as fs } from "node:fs"
import path from "node:path"

interface FrameRecord {
  readonly timestamp: number
  readonly label: string
  readonly text?: string
}

interface RenderTimelineRecord {
  readonly ts?: number
  readonly event?: string
  readonly rowCount?: number
  readonly contentWidth?: number
  readonly bodyBudgetRows?: number
  readonly managedBodyRows?: number
  readonly managedViewportRowsAboveCursor?: number
  readonly landingVariant?: string
  readonly landingRowCount?: number
  readonly pendingResponse?: boolean
  readonly landingRetired?: boolean
  readonly warmLandingVisible?: boolean
  readonly transcriptBudgetRows?: number
  readonly activeWindowCount?: number
  readonly activeWindowUsedLines?: number
  readonly activeWindowHiddenCount?: number
  readonly activeWindowTruncated?: boolean
  readonly activeWindowOvershoot?: boolean
  readonly appendLandingToFeed?: boolean
  readonly appendHeaderToFeed?: boolean
  readonly transcriptCommittedCount?: number
  readonly transcriptTailCount?: number
}

interface SurfaceRecord extends RenderTimelineRecord {}
interface ResetRecord { readonly ts?: number }

export interface EmulatorResizeRenderCorrelationReport {
  readonly degradedFrames: string[]
  readonly delayedDegradedFrames: string[]
  readonly nonInitialViewportResetCount: number
  readonly observerResizeEventCount: number
  readonly likelyRootCause:
    | "landing_budget_overshoot_during_active_resize"
    | "viewport_reset_churn"
    | "persistent_post_resize_corruption"
    | "observer_capture_transient_after_resize"
    | "no_render_side_issue_detected"
    | "transient_unknown"
  readonly frameCorrelations: ReadonlyArray<{
    readonly label: string
    readonly timestamp: number
    readonly degraded: boolean
    readonly nearestGeometryDeltaMs: number | null
    readonly nearestGeometry: {
      readonly rowCount: number | null
      readonly contentWidth: number | null
      readonly bodyBudgetRows: number | null
      readonly managedBodyRows: number | null
      readonly managedViewportRowsAboveCursor: number | null
      readonly landingVariant: string | null
    } | null
    readonly nearestCommitDeltaMs: number | null
    readonly nearestCommit: {
      readonly pendingResponse: boolean | null
      readonly warmLandingVisible: boolean | null
      readonly landingRetired: boolean | null
      readonly activeWindowUsedLines: number | null
      readonly transcriptBudgetRows: number | null
      readonly activeWindowOvershoot: boolean | null
      readonly appendLandingToFeed: boolean | null
      readonly transcriptTailCount: number | null
    } | null
  }>
}

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
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

const readObserverResizeCount = async (filePath: string): Promise<number> => {
  try {
    const text = await fs.readFile(filePath, "utf8")
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.includes("event=window-resized")).length
  } catch {
    return 0
  }
}

const buildFrameCheck = (label: string, text: string) => ({
  label,
  nonBlank: text.trim().length > 0,
  hasComposer: text.includes("❯"),
  hasItemContent: /item-\d+/i.test(text),
})

const frameIsDegraded = (frame: ReturnType<typeof buildFrameCheck>): boolean =>
  !frame.nonBlank || !frame.hasComposer || !frame.hasItemContent

const looksLikeObserverCaptureTransient = (
  frame: EmulatorResizeRenderCorrelationReport["frameCorrelations"][number],
): boolean =>
  frame.degraded &&
  frame.label.startsWith("immediate-") &&
  frame.nearestCommit?.pendingResponse === true &&
  frame.nearestCommit?.warmLandingVisible === true &&
  frame.nearestCommit?.landingRetired === false &&
  frame.nearestCommit?.activeWindowOvershoot === false &&
  (frame.nearestCommit?.transcriptTailCount ?? 0) >= 1

const nearestByTs = <T extends { ts?: number }>(records: readonly T[], timestamp: number): { deltaMs: number; record: T } | null => {
  let best: { deltaMs: number; record: T } | null = null
  for (const record of records) {
    if (typeof record.ts !== "number") continue
    const deltaMs = Math.abs(record.ts - timestamp)
    if (!best || deltaMs < best.deltaMs) {
      best = { deltaMs, record }
    }
  }
  return best
}

const asNum = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null)
const asBool = (value: unknown): boolean | null => (typeof value === "boolean" ? value : null)
const asStr = (value: unknown): string | null => (typeof value === "string" ? value : null)

export const buildEmulatorResizeRenderCorrelationReport = async (
  caseDir: string,
): Promise<EmulatorResizeRenderCorrelationReport> => {
  const [frames, renderTimeline, surface, resets, observerResizeEventCount] = await Promise.all([
    readNdjson<FrameRecord>(path.join(caseDir, "emulator_frames.ndjson")),
    readNdjson<RenderTimelineRecord>(path.join(caseDir, "render_timeline.ndjson")),
    readNdjson<SurfaceRecord>(path.join(caseDir, "surface_model.ndjson")),
    readNdjson<ResetRecord>(path.join(caseDir, "viewport_resets.ndjson")),
    readObserverResizeCount(path.join(caseDir, "observer_runtime", "wezterm-events.ndjson")),
  ])

  const frameCorrelations = [] as Array<EmulatorResizeRenderCorrelationReport["frameCorrelations"][number]>
  const degradedFrames: string[] = []
  const delayedDegradedFrames: string[] = []

  for (const frame of frames) {
    const text = frame.text ?? (await readText(path.join(caseDir, "observer_text", `${frame.label}.txt`)))
    const frameCheck = buildFrameCheck(frame.label, text)
    const degraded = frameIsDegraded(frameCheck)
    if (degraded) {
      degradedFrames.push(frame.label)
      if (frame.label.startsWith("settled-")) delayedDegradedFrames.push(frame.label)
    }
    const nearestGeometry = nearestByTs(
      renderTimeline.filter((record) => record.event === "render_geometry"),
      frame.timestamp,
    )
    const nearestCommit = nearestByTs(
      renderTimeline.filter((record) => record.event === "render_commit"),
      frame.timestamp,
    ) ?? nearestByTs(surface, frame.timestamp)

    frameCorrelations.push({
      label: frame.label,
      timestamp: frame.timestamp,
      degraded,
      nearestGeometryDeltaMs: nearestGeometry?.deltaMs ?? null,
      nearestGeometry: nearestGeometry
        ? {
            rowCount: asNum(nearestGeometry.record.rowCount),
            contentWidth: asNum(nearestGeometry.record.contentWidth),
            bodyBudgetRows: asNum(nearestGeometry.record.bodyBudgetRows),
            managedBodyRows: asNum(nearestGeometry.record.managedBodyRows),
            managedViewportRowsAboveCursor: asNum(nearestGeometry.record.managedViewportRowsAboveCursor),
            landingVariant: asStr(nearestGeometry.record.landingVariant),
          }
        : null,
      nearestCommitDeltaMs: nearestCommit?.deltaMs ?? null,
      nearestCommit: nearestCommit
        ? {
            pendingResponse: asBool(nearestCommit.record.pendingResponse),
            warmLandingVisible: asBool(nearestCommit.record.warmLandingVisible),
            landingRetired: asBool(nearestCommit.record.landingRetired),
            activeWindowUsedLines: asNum(nearestCommit.record.activeWindowUsedLines),
            transcriptBudgetRows: asNum(nearestCommit.record.transcriptBudgetRows),
            activeWindowOvershoot: asBool(nearestCommit.record.activeWindowOvershoot),
            appendLandingToFeed: asBool(nearestCommit.record.appendLandingToFeed),
            transcriptTailCount: asNum(nearestCommit.record.transcriptTailCount),
          }
        : null,
    })
  }

  const degradedWithLandingOvershoot = frameCorrelations.some(
    (frame) =>
      frame.degraded &&
      frame.nearestCommit?.warmLandingVisible === true &&
      frame.nearestCommit?.activeWindowOvershoot === true,
  )

  const degradedFramesLookObserverOnly = degradedFrames.length > 0 &&
    frameCorrelations
      .filter((frame) => frame.degraded)
      .every((frame) => looksLikeObserverCaptureTransient(frame))

  const likelyRootCause: EmulatorResizeRenderCorrelationReport["likelyRootCause"] = delayedDegradedFrames.length > 0
    ? "persistent_post_resize_corruption"
    : degradedWithLandingOvershoot
      ? "landing_budget_overshoot_during_active_resize"
      : Math.max(0, resets.length - 1) > 0
        ? "viewport_reset_churn"
        : degradedFramesLookObserverOnly
          ? "observer_capture_transient_after_resize"
        : degradedFrames.length === 0
          ? "no_render_side_issue_detected"
          : "transient_unknown"

  return {
    degradedFrames,
    delayedDegradedFrames,
    nonInitialViewportResetCount: Math.max(0, resets.length - 1),
    observerResizeEventCount,
    likelyRootCause,
    frameCorrelations,
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
  const report = await buildEmulatorResizeRenderCorrelationReport(caseDir)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
