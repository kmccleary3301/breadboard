import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly { readonly id: string; readonly message: string }

const allowedStatuses = new Set(["Ready", "Streaming...", "Working...", "Tool running...", "Needs attention", "Offline", "Failed"])

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

const extractLiveShellStatus = (line: string): string | undefined => {
  const marker = "Live Shell"
  const index = line.indexOf(marker)
  if (index < 0) return undefined
  const after = line.slice(index + marker.length)
  const parts = after.split("·").map((part) => part.trim()).filter(Boolean)
  return parts[0]
}

export const evaluateStatusVocabulary = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await textFilesToScan(caseDir)
  for (const { file, body } of files) {
    const relative = path.relative(caseDir, file)
    for (const line of body.split(/\r?\n/)) {
      const normalized = line.replace(/\s+/g, " ").trim()
      const status = extractLiveShellStatus(normalized)
      if (!status) continue
      if (!allowedStatuses.has(status)) {
        anomalies.push({
          id: "status-vocabulary-unapproved",
          message: `Live Shell status in ${relative} is not in the approved vocabulary: "${status}" from "${normalized}".`,
        })
      }
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateStatusVocabulary(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
