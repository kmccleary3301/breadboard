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

export const evaluateSceneOwnedRuntimeWidthShrink = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const turn1 = await readObserverText(caseDir, "turn1-settled.txt")
  const afterShrink = await readObserverText(caseDir, "after-width-shrink.txt")
  const turn2 = await readObserverText(caseDir, "turn2-settled.txt")

  if (!turn1.includes("Live Shell")) {
    anomalies.push({ id: "turn1-host-missing", message: "Turn-1 settled view does not expose the Live Shell label." })
  }

  if (!afterShrink.includes("Live Shell")) {
    anomalies.push({ id: "after-shrink-host-missing", message: "Width-shrink checkpoint lost the Live Shell label." })
  }

  const afterShrinkNonblank = countNonblank(afterShrink)
  if (afterShrinkNonblank < 8) {
    anomalies.push({ id: "after-shrink-near-blank", message: `Width-shrink checkpoint is still near-blank (${afterShrinkNonblank} nonblank lines).` })
  }

  if (!afterShrink.includes("hello from wezterm turn one")) {
    anomalies.push({ id: "after-shrink-turn-context-missing", message: "Width-shrink checkpoint lost the first-turn context entirely." })
  }

  if (afterShrink.includes("Live Shell compact view")) {
    anomalies.push({ id: "after-shrink-transcript-fallback", message: "Width-shrink checkpoint fell back to compact transcript behavior inside the Live Shell." })
  }

  if (!turn2.includes("Live Shell")) {
    anomalies.push({ id: "turn2-host-missing", message: "Turn-2 settled view lost the Live Shell label." })
  }
  if (!turn2.includes("Live Shell · Ready") || turn2.includes("Streaming...") || turn2.includes("[responding]")) {
    anomalies.push({ id: "turn2-settled-state-stale", message: "Settled second-turn view still shows an active/streaming state instead of the Ready state." })
  }

  if (!turn2.includes("hello from wezterm turn two")) {
    anomalies.push({ id: "turn2-context-missing", message: "Settled second-turn view does not show the second prompt context." })
  }

  if (turn2.includes("Live Shell compact view")) {
    anomalies.push({ id: "turn2-transcript-fallback", message: "Settled second-turn view still relies on compact transcript fallback semantics." })
  }
  const duplicateToolLines = ["● list_dir: running", "● apply_unified_patch: running"].filter((token) => turn2.split(token).length - 1 > 1)
  if (duplicateToolLines.length > 0) {
    anomalies.push({ id: "turn2-duplicate-tool-lines", message: `Settled second-turn view duplicated tool-status lines: ${duplicateToolLines.join(", ")}.` })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeWidthShrink(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
