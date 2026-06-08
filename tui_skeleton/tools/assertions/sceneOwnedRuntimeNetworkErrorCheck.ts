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

const readUtf8 = async (filePath: string): Promise<string | null> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return null
  }
}

export const evaluateSceneOwnedRuntimeNetworkError = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const checkpoint =
    (await readUtf8(path.join(caseDir, "observer_text", "network-stalled.txt"))) ??
    (await readUtf8(path.join(caseDir, "observer_text", "network-disconnected.txt"))) ??
    (await readUtf8(path.join(caseDir, "observer_text", "network-streaming.txt"))) ??
    ""
  const finalPlain = (await readUtf8(path.join(caseDir, "terminal_plain.txt"))) ?? ""

  if (!checkpoint.includes("Live Shell")) {
    anomalies.push({ id: "network-checkpoint-host-missing", message: "Network-error checkpoint does not show the Live Shell surface." })
  }

  const stillResponding = /\[responding\]/i.test(finalPlain)
  const failureCuePatterns = [
    /\[error\]/i,
    /warning/i,
    /failed/i,
    /connection lost/i,
    /network failure/i,
    /retrying/i,
    /disconnected/i,
    /stalled/i,
    /reconnecting/i,
  ]
  const hasVisibleFailureCue = failureCuePatterns.some((pattern) => pattern.test(finalPlain))

  if (stillResponding || !hasVisibleFailureCue) {
    anomalies.push({
      id: "network-error-not-surfaced",
      message: "Scene-owned runtime network-drop case did not exit responding cleanly into a visible stalled, reconnecting, or disconnected failure state.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeNetworkError(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
