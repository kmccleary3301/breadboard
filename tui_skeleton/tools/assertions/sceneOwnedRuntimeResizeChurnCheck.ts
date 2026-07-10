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

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const containsAssistantStreamingMarker = (text: string): boolean =>
  text.includes("# Streaming") ||
  text.includes("## Streaming") ||
  text.includes("- first item") ||
  text.includes("- second item") ||
  text.includes("| mode | slow |") ||
  text.includes("console.log('ghostty')")

export const evaluateSceneOwnedRuntimeResizeChurn = async (caseDir: string): Promise<LayoutAnomaly[]> => {
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
  const frames = await Promise.all(labels.map(async (label) => ({ label, body: await readText(path.join(caseDir, 'observer_text', `${label}.txt`)) })))
  const settled = await readText(path.join(caseDir, 'observer_text', 'settled-history.txt'))

  let streamedFrameCount = 0
  for (const frame of frames) {
    if (!frame.body.trim()) {
      anomalies.push({ id: `blank-${frame.label}`, message: `Scene-owned resize churn frame ${frame.label} was blank.` })
      continue
    }
    if (!frame.body.includes('Live Shell')) {
      anomalies.push({ id: `host-missing-${frame.label}`, message: `Scene-owned resize churn frame ${frame.label} lost the host label.` })
    }
    if (!frame.body.includes('❯')) {
      anomalies.push({ id: `composer-missing-${frame.label}`, message: `Scene-owned resize churn frame ${frame.label} lost the visible composer boundary.` })
    }
    if (frame.body.includes('Live Shell compact view')) {
      anomalies.push({ id: `compact-fallback-${frame.label}`, message: `Scene-owned resize churn frame ${frame.label} leaked compact transcript fallback.` })
    }
    if (containsAssistantStreamingMarker(frame.body)) streamedFrameCount += 1
  }

  if (streamedFrameCount < 3) {
    anomalies.push({ id: 'streamed-content-too-thin', message: `Scene-owned resize churn only showed assistant streaming content in ${streamedFrameCount} frames.` })
  }

  if (!containsAssistantStreamingMarker(settled)) {
    anomalies.push({ id: 'settled-history-missing-final-content', message: 'Settled Live Shell resize churn history did not retain the final streamed markdown payload.' })
  }
  if (!settled.includes('Live Shell')) {
    anomalies.push({ id: 'settled-host-missing', message: 'Settled Live Shell resize churn history did not retain the Live Shell.' })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeResizeChurn(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? '', 'file:').href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
