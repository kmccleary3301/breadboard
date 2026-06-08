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

const countNonblank = (value: string): number => value.split(/\r?\n/).filter((line) => line.trim().length > 0).length

export const evaluateSceneOwnedRuntimeHeightChange = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const startup = await readObserverText(caseDir, "startup.txt")
  const streamingMid = await readObserverText(caseDir, "streaming-mid.txt")
  const afterHeightShrink = await readObserverText(caseDir, "after-height-shrink.txt")
  const settled = await readObserverText(caseDir, "settled-after-height-shrink.txt")

  if (!startup.includes("Live Shell")) {
    anomalies.push({ id: "scene-owned-host-missing", message: "Startup view does not expose the Live Shell label." })
  }
  if (!streamingMid.includes("Live Shell") || !streamingMid.includes("[responding]")) {
    anomalies.push({ id: "streaming-mid-missing", message: "Height-change streaming checkpoint does not show the active scene-owned shell." })
  }
  if (countNonblank(afterHeightShrink) < 8) {
    anomalies.push({ id: "after-height-shrink-near-blank", message: `Height-shrink checkpoint is near-blank (${countNonblank(afterHeightShrink)} nonblank lines).` })
  }
  if (!afterHeightShrink.includes("Live Shell")) {
    anomalies.push({ id: "after-height-shrink-host-missing", message: "Height-shrink checkpoint lost the Live Shell label." })
  }
  if (afterHeightShrink.includes("Live Shell compact view") || settled.includes("Live Shell compact view")) {
    anomalies.push({ id: "compact-fallback-leak", message: "Height-change path leaked compact transcript fallback into the Live Shell." })
  }
  const settledShowsReady = settled.includes("Live Shell · Ready") || settled.includes("[ready]") || settled.includes("enter send")
  if (!settled.includes("Live Shell") || !settledShowsReady) {
    anomalies.push({ id: "settled-scene-missing", message: "Settled post-height-change view did not restore the Live Shell cleanly." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeHeightChange(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
