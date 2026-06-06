import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"
import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const readLabel = async (caseDir: string, label: string, scrollback = false): Promise<string> => {
  const nativeSuffix = scrollback ? ".ghostty_scrollback.txt" : ".ghostty_screen.txt"
  const fallbackSuffix = scrollback ? ".tmux_scrollback.txt" : ".tmux.txt"
  for (const suffix of [nativeSuffix, fallbackSuffix]) {
    try {
      return await fs.readFile(path.join(caseDir, "observer_text", `${label}${suffix}`), "utf8")
    } catch {
      // Try the next evidence surface.
    }
  }
  return ""
}

const containsAssistantStreamingMarker = (text: string): boolean =>
  text.includes("# Streaming") ||
  text.includes("## Streaming") ||
  text.includes("- first item") ||
  text.includes("- second item") ||
  text.includes("| mode | slow |") ||
  text.includes("console.log('ghostty')")

export const evaluateSceneOwnedGhosttyResizeChurn = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const labels = [
    'streaming-churn-0',
    'streaming-churn-1',
    'streaming-churn-2',
    'streaming-churn-3',
    'streaming-churn-4',
    'streaming-churn-5',
  ]

  let streamedFrameCount = 0
  for (const label of labels) {
    const frame = await readLabel(caseDir, label)
    if (!frame.trim()) {
      anomalies.push({ id: `blank-${label}`, message: `Ghostty resize churn frame ${label} was blank.` })
      continue
    }
    if (!frame.includes('Live Shell')) {
      anomalies.push({ id: `host-missing-${label}`, message: `Ghostty resize churn frame ${label} lost the owned-scene host.` })
    }
    if (frame.includes('Live Shell compact view')) {
      anomalies.push({ id: `compact-fallback-${label}`, message: `Ghostty resize churn frame ${label} leaked compact transcript fallback.` })
    }
    if (containsAssistantStreamingMarker(frame)) streamedFrameCount += 1
  }

  if (streamedFrameCount < 3) {
    anomalies.push({ id: 'streamed-content-too-thin', message: `Ghostty resize churn only showed assistant streaming content in ${streamedFrameCount} frames.` })
  }

  const settled = await readLabel(caseDir, 'settled-history', true)
  if (!settled.includes('Live Shell')) {
    anomalies.push({ id: 'settled-host-missing', message: 'Ghostty resize churn settled history did not retain the owned-scene host.' })
  }
  if (!containsAssistantStreamingMarker(settled)) {
    anomalies.push({ id: 'settled-content-missing', message: 'Ghostty resize churn settled history did not retain the streamed markdown payload.' })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedGhosttyResizeChurn(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
