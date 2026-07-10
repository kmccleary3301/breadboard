import { promises as fs } from "node:fs"
import path from "node:path"
import { evaluateLandingPersistence, parseSnapshots } from "./liveWrapperLandingPersistenceCheck.ts"

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

export const evaluateMaintenanceWrapperPlainSmoke = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const [landing, snapshotsRaw] = await Promise.all([
    evaluateLandingPersistence(caseDir),
    fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8"),
  ])

  const snapshots = parseSnapshots(snapshotsRaw)
  const afterAnswer = snapshots.find((entry) => entry.label === "after-answer") ?? null
  const anomalies: LayoutAnomaly[] = [...landing]

  if (!afterAnswer) {
    anomalies.push({ id: "missing-after-answer-snapshot", message: 'Missing required snapshot "after-answer".' })
    return anomalies
  }

  if (!afterAnswer.body.includes("Answer with exactly: PING_OK")) {
    anomalies.push({ id: "after-answer-missing-prompt", message: 'Snapshot "after-answer" does not contain the submitted prompt.' })
  }
  if (!afterAnswer.body.includes("Implementation receipts and verification receipts are present")) {
    anomalies.push({ id: "after-answer-missing-receipt-summary", message: 'Snapshot "after-answer" does not contain the mock C-filesystem receipt summary.' })
  }
  if (!afterAnswer.body.includes("Verification: verification receipt present")) {
    anomalies.push({ id: "after-answer-missing-verification-receipt", message: 'Snapshot "after-answer" does not contain the mock C-filesystem verification receipt.' })
  }
  if (!afterAnswer.body.includes("[ready]")) {
    anomalies.push({ id: "after-answer-missing-ready", message: 'Snapshot "after-answer" does not contain the ready footer.' })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMaintenanceWrapperPlainSmoke(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
