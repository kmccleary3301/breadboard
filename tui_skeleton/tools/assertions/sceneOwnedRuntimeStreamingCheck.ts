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

const readObserverText = async (caseDir: string, name: string): Promise<string> =>
  fs.readFile(path.join(caseDir, "observer_text", name), "utf8")

export const evaluateSceneOwnedRuntimeStreaming = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const partial = await readObserverText(caseDir, "streaming-partial.txt")
  const settled = await readObserverText(caseDir, "streaming-settled.txt")

  if (!partial.includes("Live Shell")) {
    anomalies.push({ id: "partial-host-missing", message: "Streaming partial checkpoint does not show the Live Shell label." })
  }
  if (!partial.includes("Streaming...")) {
    anomalies.push({ id: "partial-owned-live-missing", message: "Streaming partial checkpoint does not show the Streaming state." })
  }
  if (!partial.includes("stream markdown")) {
    anomalies.push({ id: "partial-prompt-context-missing", message: "Streaming partial checkpoint lost the prompt context." })
  }

  const partialHostCount = (partial.match(/Live Shell/g) ?? []).length
  if (partialHostCount !== 1) {
    anomalies.push({ id: "partial-host-duplication", message: `Streaming partial checkpoint shows ${partialHostCount} host headers instead of 1.` })
  }

  if (!settled.includes("Live Shell")) {
    anomalies.push({ id: "settled-host-missing", message: "Settled checkpoint does not show the Live Shell label." })
  }
  const settledHostCount = (settled.match(/Live Shell/g) ?? []).length
  if (settledHostCount !== 1) {
    anomalies.push({ id: "settled-host-duplication", message: `Settled checkpoint shows ${settledHostCount} host headers instead of 1.` })
  }
  if (!settled.includes("Ready")) {
    anomalies.push({ id: "settled-owned-state-missing", message: "Settled checkpoint does not show the Ready state." })
  }
  if (!settled.includes("# Streaming") && !settled.includes("Streaming")) {
    anomalies.push({ id: "settled-stream-body-missing", message: "Settled checkpoint does not show the streamed body." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeStreaming(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
