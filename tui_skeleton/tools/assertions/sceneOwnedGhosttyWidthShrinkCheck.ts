import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"
import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly { readonly id: string; readonly message: string }

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

export const evaluateSceneOwnedGhosttyWidthShrink = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const turn1 = await readLabel(caseDir, 'turn1-settled')
  const shrink = await readLabel(caseDir, 'after-width-shrink')
  const turn2 = await readLabel(caseDir, 'turn2-settled')
  const turn2Scrollback = await readLabel(caseDir, 'turn2-settled', true)

  if (!turn1.includes('Live Shell')) {
    anomalies.push({ id: 'turn1-host-missing', message: 'Ghostty width-shrink turn1-settled did not show the owned-scene host.' })
  }
  if (!shrink.includes('Live Shell')) {
    anomalies.push({ id: 'shrink-host-missing', message: 'Ghostty width-shrink after-width-shrink did not retain the owned-scene host.' })
  }
  if (shrink.trim().length < 40) {
    anomalies.push({ id: 'shrink-too-thin', message: 'Ghostty width-shrink after-width-shrink was too thin to trust.' })
  }
  if (shrink.includes('Live Shell compact view') || turn2.includes('Live Shell compact view')) {
    anomalies.push({ id: 'compact-fallback-leak', message: 'Ghostty width-shrink leaked compact transcript fallback.' })
  }
  const turn2Combined = `${turn2}\n${turn2Scrollback}`
  if (!turn2Combined.includes('Live Shell')) {
    anomalies.push({ id: 'turn2-host-missing', message: 'Ghostty width-shrink turn2-settled did not retain the owned-scene host.' })
  }
  if (!turn2Combined.includes('Give a short follow-up paragraph') && !turn2Combined.includes('Scroll up for')) {
    anomalies.push({ id: 'turn2-continuation-missing', message: 'Ghostty width-shrink turn2-settled did not show continuation truth.' })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedGhosttyWidthShrink(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? '', 'file:').href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
