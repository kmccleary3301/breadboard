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
  return { caseDir }
}

const readOptional = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

export const evaluateLiveWrapperToolSmoke = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [snapshotsText, replStateText] = await Promise.all([
    readOptional(path.join(caseDir, "pty_snapshots.txt")),
    readOptional(path.join(caseDir, "repl_state.ndjson")),
  ])

  if (!snapshotsText.includes("● Tool")) {
    anomalies.push({ id: "missing-tool-header", message: "Tool snapshot did not render the tool header." })
  }
  if (!snapshotsText.includes("stdout 1 line")) {
    anomalies.push({ id: "missing-tool-summary", message: "Tool snapshot did not render the shell summary line." })
  }
  const hasDetailLine = /^\s*[│└].+\/.+/m.test(snapshotsText)
  if (!hasDetailLine) {
    anomalies.push({ id: "missing-tool-detail", message: "Tool snapshot did not render the shell output detail line." })
  }

  const lastStateLine = replStateText.trim().split(/\r?\n/).filter(Boolean).pop() ?? ""
  if (lastStateLine) {
    try {
      const record = JSON.parse(lastStateLine) as { state?: { lastToolEvent?: { text?: string } } }
      const toolText = record.state?.lastToolEvent?.text ?? ""
      if (toolText && !toolText.includes("stdout 1 line")) {
        anomalies.push({
          id: "controller-text-missing-summary",
          message: "Controller lastToolEvent text did not preserve the shell summary line.",
        })
      }
    } catch {
      anomalies.push({ id: "invalid-repl-state", message: "Could not parse repl_state.ndjson final record." })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperToolSmoke(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
