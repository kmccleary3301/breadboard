import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface PtyMetadata {
  readonly startedAt?: number
  readonly finishedAt?: number
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

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const readJson = async <T>(filePath: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8")) as T
  } catch {
    return null
  }
}

const resolveTuiRoot = (caseDir: string): string => {
  const marker = `${path.sep}docs_tmp${path.sep}`
  const index = caseDir.indexOf(marker)
  if (index > 0) return path.join(caseDir.slice(0, index), "tui_skeleton")
  return path.resolve(process.cwd())
}

export const evaluateTranscriptSaveExport = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const raw = await readText(path.join(caseDir, "pty_raw.ansi"))
  const plain = await readText(path.join(caseDir, "pty_plain.txt"))
  const metadata = await readJson<PtyMetadata>(path.join(caseDir, "pty_metadata.json"))

  if (!raw.includes("Saved to") && !plain.includes("Saved to")) {
    anomalies.push({
      id: "save-feedback-missing",
      message: "Transcript save flow did not render the saved-path feedback.",
    })
  }

  const tuiRoot = resolveTuiRoot(caseDir)
  const transcriptDir = path.join(tuiRoot, "artifacts", "transcripts")
  const startedAt = typeof metadata?.startedAt === "number" ? metadata.startedAt - 5_000 : 0
  const finishedAt = typeof metadata?.finishedAt === "number" ? metadata.finishedAt + 5_000 : Date.now() + 5_000

  let candidates: Array<{ readonly filePath: string; readonly mtimeMs: number; readonly contents: string }> = []
  try {
    const entries = await fs.readdir(transcriptDir, { withFileTypes: true })
    candidates = (
      await Promise.all(
        entries
          .filter((entry) => entry.isFile() && entry.name.endsWith(".txt"))
          .map(async (entry) => {
            const filePath = path.join(transcriptDir, entry.name)
            const stat = await fs.stat(filePath)
            const contents = await readText(filePath)
            return { filePath, mtimeMs: stat.mtimeMs, contents }
          }),
      )
    ).filter((entry) => entry.mtimeMs >= startedAt && entry.mtimeMs <= finishedAt)
  } catch {
    anomalies.push({
      id: "transcript-dir-missing",
      message: `Transcript directory was not readable: ${transcriptDir}`,
    })
    return anomalies
  }

  const matching = candidates.find(
    (entry) => entry.contents.includes("Hello viewer") && entry.contents.includes("Mock assistant: hello world"),
  )
  if (!matching) {
    anomalies.push({
      id: "saved-transcript-content-missing",
      message:
        "No transcript file created during the case contained both the user prompt and assistant response.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateTranscriptSaveExport(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
