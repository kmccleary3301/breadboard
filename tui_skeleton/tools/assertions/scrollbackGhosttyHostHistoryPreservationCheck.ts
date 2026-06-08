import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { promises as fs } from "node:fs"
import path from "node:path"
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"

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

const readJson = async (file: string): Promise<any> => JSON.parse(await fs.readFile(file, "utf8"))
const readText = async (file: string): Promise<string> => fs.readFile(file, "utf8")

const readOptional = async (file: string): Promise<string> => {
  try {
    return await readText(file)
  } catch {
    return ""
  }
}

const readObserverFiles = async (caseDir: string, suffix: string): Promise<string> => {
  const observerDir = path.join(caseDir, "observer_text")
  const entries = await fs.readdir(observerDir).catch(() => [])
  const texts = await Promise.all(
    entries
      .filter((entry) => entry.endsWith(suffix))
      .sort()
      .map((entry) => readOptional(path.join(observerDir, entry))),
  )
  return texts.join("\n")
}

export const evaluateScrollbackGhosttyHostHistoryPreservation = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  const anchor = await readJson(path.join(caseDir, "app_start_anchor.txt"))
  const bounds = await readText(path.join(caseDir, "managed_region_bounds.ndjson"))
  const clauseVerdicts = await readJson(path.join(caseDir, "scrollback_clause_verdicts.json"))
  const scrollbackFinal = await readText(path.join(caseDir, "terminal_text", "scrollback_final.txt"))
  const tmuxScrollback = await readOptional(path.join(caseDir, "observer_text", "streaming-final.tmux_scrollback.txt"))
  const nativeScrollback = await readObserverFiles(caseDir, ".ghostty_scrollback.txt")
  const nativeScreen = await readObserverFiles(caseDir, ".ghostty_screen.txt")
  const combined = `${scrollbackFinal}\n${tmuxScrollback}\n${nativeScrollback}\n${nativeScreen}`
  const hasNativeExport = nativeScrollback.trim().length > 0 || nativeScreen.trim().length > 0

  if (anchor.mode !== "preserved-scrollback") {
    anomalies.push({ id: "anchor-mode-mismatch", message: `Ghostty app-start anchor mode is ${String(anchor.mode)}, expected preserved-scrollback.` })
  }
  if (anchor.preAppHistoryPolicy !== "untouched") {
    anomalies.push({ id: "anchor-history-policy-mismatch", message: "Ghostty app-start anchor does not declare untouched pre-app history." })
  }
  if (!bounds.trim()) {
    anomalies.push({ id: "managed-region-bounds-missing", message: "Ghostty managed_region_bounds.ndjson did not receive any records." })
  }
  if (!Array.isArray(clauseVerdicts.verdicts)) {
    anomalies.push({ id: "clause-verdict-schema-missing", message: "Ghostty scrollback_clause_verdicts.json does not expose a verdicts array." })
  }
  if (!hasNativeExport) {
    anomalies.push({ id: "ghostty-native-export-missing", message: "Ghostty-native claim case did not produce any .ghostty_screen.txt or .ghostty_scrollback.txt export artifacts." })
  }
  if (!combined.includes("PRE_APP_ALPHA") || !combined.includes("PRE_APP_BETA")) {
    anomalies.push({ id: "pre-app-history-missing", message: "Ghostty final scrollback text does not retain the seeded pre-app history lines." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateScrollbackGhosttyHostHistoryPreservation(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
