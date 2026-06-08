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

const readOptional = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

export const evaluateSceneOwnedRuntimeTranscriptEscape = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  const observerTextDir = path.join(caseDir, "observer_text")
  const viewerOpen = await readOptional(path.join(observerTextDir, "viewer-open.txt"))
  const viewerBack = await readOptional(path.join(observerTextDir, "viewer-back.txt"))
  const viewerOpenNormalized = viewerOpen.toLowerCase()
  const viewerBackNormalized = viewerBack.toLowerCase()

  if (!viewerOpen) anomalies.push({ id: "missing-viewer-open", message: "Missing viewer-open snapshot." })
  if (!viewerBack) anomalies.push({ id: "missing-viewer-back", message: "Missing viewer-back snapshot." })
  if (anomalies.length > 0) return anomalies

  if (!viewerOpenNormalized.includes("follow tail") || !viewerOpenNormalized.includes("g top")) {
    anomalies.push({ id: "transcript-open-contract-missing", message: "Viewer-open snapshot does not show the expected transcript viewer contract." })
  }

  const hasSceneHost = viewerBackNormalized.includes("live shell")
  const hasTranscriptAffordance =
    viewerBackNormalized.includes("ctrl+o transcript") ||
    viewerBackNormalized.includes("ctrl+t transcript") ||
    (viewerBackNormalized.includes("resume /sessions") && viewerBackNormalized.includes("@ attach") && viewerBackNormalized.includes("ctrl+k model"))

  if (!hasSceneHost || !hasTranscriptAffordance) {
    anomalies.push({ id: "transcript-return-contract-missing", message: "Viewer-back snapshot does not show the Live Shell with transcript affordance restored." })
  }
  if (viewerBackNormalized.includes("no conversation yet")) {
    anomalies.push({ id: "transcript-return-reset-empty", message: "Viewer-back snapshot reset the Live Shell to an empty-session state." })
  }
  if (!viewerBackNormalized.includes("stream markdown")) {
    anomalies.push({ id: "transcript-return-context-missing", message: "Viewer-back snapshot lost the current turn context after returning from transcript view." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateSceneOwnedRuntimeTranscriptEscape(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
