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

const readJsonLines = async (filePath: string): Promise<any[]> => {
  const raw = await fs.readFile(filePath, "utf8").catch(() => "")
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      try {
        return JSON.parse(line)
      } catch {
        return null
      }
    })
    .filter(Boolean)
}

export const evaluateLiveWrapperStreaming = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const events = await readJsonLines(path.join(caseDir, "events.ndjson"))

  const hasAssistantStreamEvent = events.some((event) => {
    const type = event?.type
    if (type === "assistant.message.delta" || type === "assistant_delta") return true
    const delta = event?.payload?.delta ?? event?.data?.delta ?? null
    return typeof delta === "string" && delta.length > 0 && (type === "assistant_message" || type === "assistant.message")
  })
  if (!hasAssistantStreamEvent) {
    anomalies.push({
      id: "assistant-stream-event-missing",
      message: "Expected replay-backed events.ndjson to contain an assistant streaming delta event.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperStreaming(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
