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

const hasStreamingContent = (text: string): boolean =>
  text.includes("# Streaming") ||
  text.includes("## Streaming") ||
  text.includes("- first item") ||
  text.includes("console.log('ghostty')")

export const evaluateSceneOwnedGhosttyStreamingCurrentWindow = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const mid = await readLabel(caseDir, 'streaming-mid')
  const final = await readLabel(caseDir, 'streaming-final')
  const finalScrollback = await readLabel(caseDir, 'streaming-final', true)

  if (!mid.includes('Live Shell')) {
    anomalies.push({ id: 'mid-host-missing', message: 'Ghostty current-window streaming-mid did not show the owned-scene host.' })
  }
  if (!mid.includes('[responding]') && !(mid.includes('Live Shell · Ready') && hasStreamingContent(mid))) {
    anomalies.push({ id: 'mid-responding-missing', message: 'Ghostty current-window streaming-mid did not show a visible responding state or already-settled streamed content.' })
  }
  if (mid.includes('Live Shell compact view')) {
    anomalies.push({ id: 'mid-compact-fallback', message: 'Ghostty current-window streaming-mid leaked compact transcript fallback.' })
  }
  if (!final.includes('Live Shell')) {
    anomalies.push({ id: 'final-host-missing', message: 'Ghostty current-window streaming-final did not retain the owned-scene host.' })
  }
  if (!hasStreamingContent(final) && !hasStreamingContent(finalScrollback)) {
    anomalies.push({ id: 'final-content-missing', message: 'Ghostty current-window streaming-final did not retain the streamed markdown payload.' })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedGhosttyStreamingCurrentWindow(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? '', 'file:').href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
