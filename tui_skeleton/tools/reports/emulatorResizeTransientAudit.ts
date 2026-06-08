import { promises as fs } from "node:fs"
import path from "node:path"

interface SurfaceRecord {
  readonly pendingResponse?: boolean
  readonly landingVariant?: string
  readonly activeWindowHiddenCount?: number
  readonly activeWindowTruncated?: boolean
}

interface ResetRecord {
  readonly ts?: number
}

export interface EmulatorResizeTransientAudit {
  readonly profileKind: "settle_probe" | "streaming_churn" | "unknown"
  readonly frameChecks: ReadonlyArray<{
    readonly label: string
    readonly nonBlank: boolean
    readonly hasComposer: boolean
    readonly hasPrompt: boolean
    readonly hasItemContent: boolean
  }>
  readonly immediateFramesDegraded: string[]
  readonly delayedFramesDegraded: string[]
  readonly churnFramesDegraded: string[]
  readonly settledHistoryHasFinalTail: boolean
  readonly settledHistoryDuplicateTailCount: number
  readonly nonInitialViewportResetCount: number
  readonly finalSurfaceState: {
    readonly pendingResponse: boolean | null
    readonly landingVariant: string | null
    readonly activeWindowHiddenCount: number | null
    readonly activeWindowTruncated: boolean | null
  }
  readonly classification:
    | "green"
    | "immediate_transient_only"
    | "persistent_or_structural"
    | "streaming_churn_green"
    | "streaming_churn_degraded"
    | "not_applicable"
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

const buildFrameCheck = (label: string, text: string) => ({
  label,
  nonBlank: text.trim().length > 0,
  hasComposer: text.includes("❯"),
  hasPrompt: text.includes("❯ Answer with exactly 20 short bullet points"),
  hasItemContent: /item-\d+/i.test(text),
})

const frameIsDegraded = (frame: ReturnType<typeof buildFrameCheck>): boolean =>
  !frame.nonBlank || !frame.hasComposer || !frame.hasItemContent

export const buildEmulatorResizeTransientAudit = async (caseDir: string): Promise<EmulatorResizeTransientAudit> => {
  const observerDir = path.join(caseDir, "observer_text")
  const observerFiles = await fs.readdir(observerDir).catch(() => [])
  const labels = observerFiles
    .filter((name) => name.endsWith(".txt") && name !== "settled-history.txt")
    .map((name) => name.replace(/\.txt$/u, ""))
    .sort()
  const [settledHistory, resets, surface] = await Promise.all([
    readText(path.join(observerDir, "settled-history.txt")),
    readNdjson<ResetRecord>(path.join(caseDir, "viewport_resets.ndjson")),
    readNdjson<SurfaceRecord>(path.join(caseDir, "surface_model.ndjson")),
  ])

  const frames = await Promise.all(
    labels.map(async (label) => buildFrameCheck(label, await readText(path.join(observerDir, `${label}.txt`)))),
  )

  const immediateFrames = frames.filter((frame) => frame.label.startsWith("immediate-"))
  const delayedFrames = frames.filter((frame) => frame.label.startsWith("settled-"))
  const churnFrames = frames.filter((frame) => frame.label.startsWith("streaming-churn-"))
  const immediateFramesDegraded = immediateFrames.filter(frameIsDegraded).map((frame) => frame.label)
  const delayedFramesDegraded = delayedFrames.filter(frameIsDegraded).map((frame) => frame.label)
  const churnFramesDegraded = churnFrames.filter(frameIsDegraded).map((frame) => frame.label)
  const finalSurface = surface[surface.length - 1] ?? null
  const settledTailMatches = settledHistory.match(/^- item-20\b/gim) ?? []
  const settledHistoryDuplicateTailCount = settledTailMatches.length
  const profileKind: EmulatorResizeTransientAudit["profileKind"] =
    immediateFrames.length > 0 || delayedFrames.length > 0
      ? "settle_probe"
      : churnFrames.length > 0
        ? "streaming_churn"
        : "unknown"

  let classification: EmulatorResizeTransientAudit["classification"] = "not_applicable"
  if (profileKind === "settle_probe") {
    classification = "green"
    if (immediateFramesDegraded.length > 0 && delayedFramesDegraded.length === 0) {
      classification = "immediate_transient_only"
    } else if (immediateFramesDegraded.length > 0 || delayedFramesDegraded.length > 0) {
      classification = "persistent_or_structural"
    }
  } else if (profileKind === "streaming_churn") {
    classification = churnFramesDegraded.length === 0 ? "streaming_churn_green" : "streaming_churn_degraded"
  }

  return {
    profileKind,
    frameChecks: frames,
    immediateFramesDegraded,
    delayedFramesDegraded,
    churnFramesDegraded,
    settledHistoryHasFinalTail: settledTailMatches.length > 0,
    settledHistoryDuplicateTailCount,
    nonInitialViewportResetCount: Math.max(0, resets.length - 1),
    finalSurfaceState: {
      pendingResponse: finalSurface?.pendingResponse ?? null,
      landingVariant: typeof finalSurface?.landingVariant === "string" ? finalSurface.landingVariant : null,
      activeWindowHiddenCount:
        typeof finalSurface?.activeWindowHiddenCount === "number" ? finalSurface.activeWindowHiddenCount : null,
      activeWindowTruncated: finalSurface?.activeWindowTruncated ?? null,
    },
    classification,
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
  const report = await buildEmulatorResizeTransientAudit(caseDir)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
