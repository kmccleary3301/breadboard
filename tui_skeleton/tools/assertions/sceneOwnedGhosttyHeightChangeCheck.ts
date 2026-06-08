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

export const evaluateSceneOwnedGhosttyHeightChange = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const startup = await readLabel(caseDir, "startup")
  const streaming = await readLabel(caseDir, "streaming-mid")
  const shrink = await readLabel(caseDir, "after-height-shrink")
  const settled = await readLabel(caseDir, "settled-after-height-shrink")

  if (startup.trim().length > 0 && !startup.includes("Live Shell")) {
    anomalies.push({ id: "startup-host-missing", message: "Ghostty height-change startup showed terminal content but not the Live Shell." })
  }
  const streamingSettledWithContent =
    streaming.includes("Live Shell · Ready") &&
    (streaming.includes("# Streaming") || streaming.includes("console.log('ghostty')") || streaming.includes("| lane | ghostty |"))
  if (!streaming.includes("Live Shell") || (!streaming.includes("[responding]") && !streamingSettledWithContent)) {
    anomalies.push({ id: "streaming-scene-missing", message: "Ghostty height-change streaming-mid did not retain the Live Shell while active or already settled with streamed content." })
  }
  if (!shrink.includes("Live Shell")) {
    anomalies.push({ id: "shrink-host-missing", message: "Ghostty height-change lost the owned-scene host after height shrink." })
  }
  if (shrink.includes("Live Shell compact view")) {
    anomalies.push({ id: "shrink-compact-fallback", message: "Ghostty height-change leaked compact transcript fallback after height shrink." })
  }
  if (!settled.includes("Live Shell") || (!settled.includes("[ready]") && !settled.includes("Cooked for") && !settled.includes("Live Shell · Ready"))) {
    anomalies.push({ id: "settled-scene-missing", message: "Ghostty height-change did not settle back into a visible Live Shell with a ready cue." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedGhosttyHeightChange(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
