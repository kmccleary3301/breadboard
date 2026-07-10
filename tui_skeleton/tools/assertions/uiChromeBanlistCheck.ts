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

const bannedPhrases = [
  "scene owned",
  "owned scene",
  "S1 prototype",
  "S2 prototype",
  "runtime host",
  "inline-scrollback",
  "OpenTUI mode",
  "Compact transcript mode active",
]

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
      // Not every harness emits every text surface.
    }
  }
  return out
}

export const evaluateUiChromeBanlist = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await textFilesToScan(caseDir)
  for (const { file, body } of files) {
    const lower = body.toLowerCase()
    for (const phrase of bannedPhrases) {
      if (lower.includes(phrase.toLowerCase())) {
        anomalies.push({
          id: `ui-chrome-banned-${phrase.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`,
          message: `User-facing capture ${path.relative(caseDir, file)} includes banned normal-mode UI phrase "${phrase}".`,
        })
      }
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateUiChromeBanlist(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
