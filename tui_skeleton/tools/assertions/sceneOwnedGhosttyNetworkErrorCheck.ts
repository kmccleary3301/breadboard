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

const hasFailureCue = (text: string): boolean => {
  const lowered = text.toLowerCase()
  return ["[error]", "warning", "failed", "connection lost", "network failure", "retrying", "disconnected", "stalled", "reconnecting"].some((token) => lowered.includes(token))
}

export const evaluateSceneOwnedGhosttyNetworkError = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const streaming = await readLabel(caseDir, "network-streaming")
  const stalled = await readLabel(caseDir, "network-stalled")

  if (!streaming.includes("Live Shell")) {
    anomalies.push({ id: "streaming-host-missing", message: "Ghostty network-error case lost the owned-scene host while active." })
  }
  if (!stalled.includes("Live Shell")) {
    anomalies.push({ id: "stalled-host-missing", message: "Ghostty network-error case lost the owned-scene host in the failure state." })
  }
  if (stalled.includes("[responding]")) {
    anomalies.push({ id: "stuck-responding", message: "Ghostty network-error case was still visibly responding at the stalled checkpoint." })
  }
  if (!hasFailureCue(stalled)) {
    anomalies.push({ id: "failure-cue-missing", message: "Ghostty network-error case did not surface a visible local failure cue." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedGhosttyNetworkError(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
