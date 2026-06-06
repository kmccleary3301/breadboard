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

export const evaluateMaintenanceWrapperToolSmoke = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [snapshotsText, plainText, replStateText] = await Promise.all([
    readOptional(path.join(caseDir, "pty_snapshots.txt")),
    readOptional(path.join(caseDir, "pty_plain.txt")),
    readOptional(path.join(caseDir, "repl_state.ndjson")),
  ])

  const transcriptText = snapshotsText.trim().length > 0 ? snapshotsText : plainText

  if (!transcriptText.includes("● list_dir")) {
    anomalies.push({ id: "missing-tool-header", message: "Tool transcript did not render the list_dir tool row." })
  }
  if (!transcriptText.includes("Implementation receipts and verification receipts are present")) {
    anomalies.push({ id: "missing-receipt-summary", message: "Settled tool transcript did not contain the mock C-filesystem receipt summary." })
  }
  if (!transcriptText.includes("Verification: verification receipt present")) {
    anomalies.push({ id: "missing-verification-receipt", message: "Settled tool transcript did not contain the mock C-filesystem verification receipt." })
  }

  const stateLines = replStateText.trim().split(/\r?\n/).filter(Boolean)
  if (stateLines.length > 0) {
    try {
      const sawMockToolIdentity = stateLines
        .map((line) => JSON.parse(line) as { state?: { lastToolEvent?: { kind?: string; status?: string; text?: string } } })
        .some((record) => {
          const event = record.state?.lastToolEvent
          if (!event || event.kind !== "call") return false
          const text = event.text ?? ""
          return text.includes("list_dir") || text.includes("apply_unified_patch")
        })
      if (!sawMockToolIdentity) {
        anomalies.push({ id: "controller-text-missing-tool", message: "State history did not preserve any successful mock tool identity." })
      }
    } catch {
      anomalies.push({ id: "invalid-repl-state", message: "Could not parse repl_state.ndjson records." })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMaintenanceWrapperToolSmoke(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
