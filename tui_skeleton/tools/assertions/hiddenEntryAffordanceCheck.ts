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

const textFilesToScan = async (caseDir: string): Promise<Array<{ file: string; body: string }>> => {
  const candidates = [
    path.join(caseDir, "terminal_text", "visible_final.txt"),
    path.join(caseDir, "terminal_text", "scrollback_final.txt"),
  ]
  const observerDir = path.join(caseDir, "observer_text")
  const observerEntries = await fs.readdir(observerDir).catch(() => [])
  for (const entry of observerEntries) {
    if (entry.endsWith(".txt") && !entry.endsWith(".x11.txt") && !entry.endsWith(".summary.txt")) {
      candidates.push(path.join(observerDir, entry))
    }
  }

  const out: Array<{ file: string; body: string }> = []
  for (const file of candidates) {
    try {
      out.push({ file, body: await fs.readFile(file, "utf8") })
    } catch {
      // Harnesses emit different capture surfaces.
    }
  }
  return out
}

const ambiguousHiddenCue = /\.\.\.\s+\d+\s+earlier\s+entries\s+hidden\s+\.\.\./i

export const evaluateHiddenEntryAffordance = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await textFilesToScan(caseDir)
  for (const { file, body } of files) {
    const relative = path.relative(caseDir, file)
    if (ambiguousHiddenCue.test(body)) {
      anomalies.push({
        id: "hidden-entry-ambiguous-cue",
        message: `User-facing capture ${relative} includes the ambiguous old hidden-entry cue.`,
      })
    }

    for (const line of body.split(/\r?\n/)) {
      const normalized = line.replace(/\s+/g, " ").trim()
      const lower = normalized.toLowerCase()
      if (!lower.includes("earlier")) continue

      if (lower.includes("earlier message") && !lower.includes("transcript") && !lower.includes("ctrl+o")) {
        anomalies.push({
          id: "hidden-entry-live-recovery-missing",
          message: `Live Shell hidden-entry cue in ${relative} does not name the transcript recovery path: "${normalized}".`,
        })
      }
      if (lower.includes("earlier output") && !lower.includes("scroll") && !lower.includes("transcript")) {
        anomalies.push({
          id: "hidden-entry-scrollback-recovery-missing",
          message: `Scrollback hidden-entry cue in ${relative} does not name scrollback or transcript recovery: "${normalized}".`,
        })
      }
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateHiddenEntryAffordance(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
