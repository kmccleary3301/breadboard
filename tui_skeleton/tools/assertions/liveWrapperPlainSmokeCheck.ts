import { promises as fs } from "node:fs"
import path from "node:path"
import { evaluateLandingPersistence, parseSnapshots } from "./liveWrapperLandingPersistenceCheck.ts"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const SYSTEM_PROMPT_LEAK_PATTERNS = [
  /You are Codex, based on GPT-5/i,
  /You are running as a coding agent in the Codex CLI/i,
  /## Editing constraints/i,
  /## Presenting your work and final message/i,
  /### Final answer structure and style guidelines/i,
  /Default: be very concise; friendly coding teammate tone/i,
  /File References: When referencing files in your response/i,
]

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir }
}

export const evaluateLiveWrapperPlainSmoke = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const [landing, snapshotsRaw] = await Promise.all([
    evaluateLandingPersistence(caseDir),
    fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8"),
  ])

  const snapshots = parseSnapshots(snapshotsRaw)
  const afterAnswer = snapshots.find((entry) => entry.label === "after-answer") ?? null
  const anomalies: LayoutAnomaly[] = [...landing]

  if (!afterAnswer) {
    anomalies.push({ id: "missing-after-answer-snapshot", message: 'Missing required snapshot "after-answer".' })
  } else {
    const leakedPattern = SYSTEM_PROMPT_LEAK_PATTERNS.find((pattern) => pattern.test(afterAnswer.body))
    if (leakedPattern) {
      anomalies.push({
        id: "system-prompt-visible",
        message: `Snapshot "after-answer" exposed system prompt text matching ${String(leakedPattern)}.`,
      })
    }
    if (!afterAnswer.body.split(/\r?\n/).some((line) => line.trim() === "two")) {
      anomalies.push({ id: "after-answer-missing-output", message: 'Snapshot "after-answer" does not contain the settled answer line "two".' })
    }
    if (!afterAnswer.body.includes("[ready]") && !afterAnswer.body.includes("Type your request")) {
      anomalies.push({ id: "after-answer-missing-ready", message: 'Snapshot "after-answer" does not contain ready footer or idle Codex input placeholder.' })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperPlainSmoke(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
