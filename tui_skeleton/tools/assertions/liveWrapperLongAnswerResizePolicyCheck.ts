import { promises as fs } from "node:fs"
import path from "node:path"
import { buildManagedRegionReclaimAudit } from "../reports/managedRegionReclaimAudit.ts"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface SnapshotFrame {
  readonly label: string
  readonly body: string
}

const LONG_ANSWER_PROMPT_FRAGMENTS = [
  "Answer with exactly 20 short bullet points",
  "Answer with exactly",
  "20 short bullet points",
]
const LONG_ANSWER_ITEM_PATTERN = /(^|\n)\s*[-\u2022]\s+item-(?:\d{0,2})/

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const readOptional = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const readOptionalJson = async <T>(filePath: string): Promise<T | null> => {
  const text = await readOptional(filePath)
  if (!text.trim()) return null
  return JSON.parse(text) as T
}

const parseSnapshotFrames = (raw: string): SnapshotFrame[] => {
  const frames: SnapshotFrame[] = []
  const lines = raw.split(/\r?\n/)
  let label: string | null = null
  let body: string[] = []

  const flush = () => {
    if (!label) return
    frames.push({ label, body: body.join("\n") })
  }

  for (const line of lines) {
    const match = line.match(/^#\s+(.+?)\s*$/)
    if (match) {
      flush()
      label = match[1]
      body = []
      continue
    }
    if (label) body.push(line)
  }
  flush()

  return frames
}

const findPromptIndex = (body: string): number => {
  const indexes = LONG_ANSWER_PROMPT_FRAGMENTS
    .map((fragment) => body.indexOf(fragment))
    .filter((index) => index >= 0)
  return indexes.length === 0 ? -1 : Math.min(...indexes)
}

const findFirstItemIndex = (body: string): number => {
  const match = LONG_ANSWER_ITEM_PATTERN.exec(body)
  return match?.index ?? -1
}

const hasBottomBoundary = (body: string): boolean => {
  const tail = body
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .slice(-8)
    .join("\n")
  return /(^|\n)❯\s*($|\n)/.test(tail) || /\[(responding|ready|working|thinking)\]|enter send|esc interrupt|follow live/.test(tail)
}

const hasUnexpectedViewportResetChurn = async (caseDir: string): Promise<number> => {
  const text = await readOptional(path.join(caseDir, "viewport_resets.ndjson"))
  const records = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line) as { pendingResponse?: boolean; resetKey?: string }
      } catch {
        return null
      }
    })
    .filter((record): record is { pendingResponse?: boolean; resetKey?: string } => record != null)
  if (records.length <= 1) return 0
  const nonInitial = records.slice(1)
  const pendingTransitions = nonInitial.filter((record) => record.pendingResponse === true)
  const idleTransitions = nonInitial.filter((record) => record.pendingResponse !== true)
  const allowedStableRunEpoch =
    pendingTransitions.length <= 1 &&
    idleTransitions.length <= 1 &&
    new Set(records.map((record) => String(record.resetKey ?? ""))).size === records.length
  return allowedStableRunEpoch ? 0 : nonInitial.length
}

export const evaluateLiveWrapperLongAnswerResizePolicy = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [
    audit,
    ptySnapshotsText,
    emulatorSnapshotsText,
    observerSettledHistoryText,
    markdownPaintSummary,
  ] = await Promise.all([
    buildManagedRegionReclaimAudit(caseDir),
    readOptional(path.join(caseDir, "pty_snapshots.txt")),
    readOptional(path.join(caseDir, "emulator_snapshots.txt")),
    readOptional(path.join(caseDir, "observer_text", "settled-history.txt")),
    readOptionalJson<{
      readonly staticFeedAppendCount?: number
      readonly staticChunkDuplicateCount?: number
      readonly frozenBlockMutationCount?: number
      readonly activeFrozenOverlapCount?: number
      readonly maxHotTailRows?: number
      readonly broadClearCount?: number
      readonly unknownAttributionRows?: number
    }>(path.join(caseDir, "markdown_paint_summary.json")),
  ])
  const snapshotsText = [ptySnapshotsText, emulatorSnapshotsText].filter((value) => value.trim().length > 0).join("\n")
  const streamingFrames = parseSnapshotFrames(snapshotsText).filter((frame) => frame.label.startsWith("streaming-"))

  const unexpectedViewportResetCount = await hasUnexpectedViewportResetChurn(caseDir)
  if (unexpectedViewportResetCount > 0) {
    anomalies.push({
      id: "viewport-reset-churn",
      message: `Expected only stable idle/active/settled viewport reset-key epochs, saw ${unexpectedViewportResetCount} unexpected transition(s).`,
    })
  }

  if (audit.landingRetiredDuringPending) {
    anomalies.push({
      id: "landing-retired-during-pending",
      message: "Landing retired during the active streamed turn under the canonical long-answer/resize case.",
    })
  }

  if (audit.settledTruncationObserved || audit.maxHiddenCountAfterSettlement > 0) {
    anomalies.push({
      id: "settled-truncation",
      message:
        `Expected no post-settlement truncation/hidden-count discontinuity, saw ` +
        `settledTruncationObserved=${String(audit.settledTruncationObserved)} maxHiddenCountAfterSettlement=${audit.maxHiddenCountAfterSettlement}.`,
    })
  }

  const settledHistory = snapshotsText.includes("# settled-history")
    ? snapshotsText.split(/^# settled-history$/m)[1] ?? ""
    : observerSettledHistoryText
  if (!settledHistory.includes("- item-20")) {
    anomalies.push({
      id: "settled-history-missing-final-item",
      message: 'Settled history snapshot did not retain the final long-answer line "- item-20".',
    })
  }
  const submittedPromptCount = (settledHistory.match(/Answer with exactly 20 short bullet points/g) ?? []).length
  if (submittedPromptCount > 1) {
    anomalies.push({
      id: "settled-history-duplicate-prompt",
      message: `Settled history snapshot duplicated the submitted prompt during resize; saw ${submittedPromptCount} copies.`,
    })
  }
  const item20Count = (settledHistory.match(/- item-20/g) ?? []).length
  if (item20Count > 1) {
    anomalies.push({
      id: "settled-history-duplicate-tail",
      message: `Settled history snapshot appears to duplicate the tail line; saw ${item20Count} copies of "- item-20".`,
    })
  }

  for (const frame of streamingFrames) {
    const firstItemIndex = findFirstItemIndex(frame.body)
    if (firstItemIndex < 0) {
      anomalies.push({
        id: `streaming-frame-missing-active-output-${frame.label}`,
        message: `Streaming frame ${frame.label} did not show any active long-answer bullet output.`,
      })
      continue
    }

    if (!hasBottomBoundary(frame.body)) {
      anomalies.push({
        id: `streaming-frame-missing-bottom-boundary-${frame.label}`,
        message: `Streaming frame ${frame.label} did not show the composer/status bottom boundary near the active turn.`,
      })
    }

    const promptIndex = findPromptIndex(frame.body)
    if (promptIndex > firstItemIndex) {
      anomalies.push({
        id: `streaming-prompt-interleaved-with-output-${frame.label}`,
        message:
          `Streaming frame ${frame.label} rendered the submitted prompt below active answer output; ` +
          "the prompt must be committed before the assistant stream so the latest active output is not visually hidden or reordered.",
      })
    }
  }

  if (markdownPaintSummary) {
    if ((markdownPaintSummary.staticChunkDuplicateCount ?? 0) > 0) {
      anomalies.push({
        id: "markdown-static-chunk-duplicate",
        message: `Expected no duplicate static markdown chunks, saw ${markdownPaintSummary.staticChunkDuplicateCount}.`,
      })
    }
    if ((markdownPaintSummary.frozenBlockMutationCount ?? 0) > 0) {
      anomalies.push({
        id: "markdown-frozen-block-mutated",
        message: `Expected no frozen markdown mutations, saw ${markdownPaintSummary.frozenBlockMutationCount}.`,
      })
    }
    if ((markdownPaintSummary.activeFrozenOverlapCount ?? 0) > 0) {
      anomalies.push({
        id: "markdown-active-frozen-overlap",
        message: `Expected no active/frozen markdown overlap, saw ${markdownPaintSummary.activeFrozenOverlapCount}.`,
      })
    }
    if ((markdownPaintSummary.maxHotTailRows ?? 0) > 1) {
      anomalies.push({
        id: "markdown-hot-tail-unbounded",
        message: `Expected markdown hot tail to stay within 1 row, saw ${markdownPaintSummary.maxHotTailRows}.`,
      })
    }
    if ((markdownPaintSummary.broadClearCount ?? 0) > 0) {
      anomalies.push({
        id: "markdown-broad-clear",
        message: `Expected no broad clears during long-answer resize, saw ${markdownPaintSummary.broadClearCount}.`,
      })
    }
    if ((markdownPaintSummary.unknownAttributionRows ?? 0) > 0) {
      anomalies.push({
        id: "markdown-unknown-attribution",
        message: `Expected all changed rows to be attributed, saw ${markdownPaintSummary.unknownAttributionRows} unknown rows.`,
      })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperLongAnswerResizePolicy(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
