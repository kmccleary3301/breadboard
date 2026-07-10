import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const WARNING_TEXT = "Encountered two children with the same key"

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

export const evaluateNoReactDuplicateKeyWarning = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = ["pty_plain.txt", "pty_raw.ansi", "ttydoc.txt"]
  for (const name of files) {
    const target = path.join(caseDir, name)
    try {
      const raw = await fs.readFile(target, "utf8")
      if (raw.includes(WARNING_TEXT)) {
        anomalies.push({
          id: "react-duplicate-key-warning",
          message: `Detected duplicate React child key warning in ${name}.`,
        })
        return anomalies
      }
    } catch {
      // ignore missing optional files
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateNoReactDuplicateKeyWarning(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
