import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface SnapshotEntry {
  readonly label: string
  readonly body: string
}

const PROMPT_1 = "What is 1+1? Answer with only the lowercase English word."
const PROMPT_2 = "What is 2+2? Answer with only the lowercase English word."
const PROMPT_3 = "What is 3+3? Answer with only the lowercase English word."

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const parseSnapshots = (raw: string): SnapshotEntry[] => {
  const lines = raw.split(/\r?\n/)
  const entries: SnapshotEntry[] = []
  let currentLabel: string | null = null
  let buffer: string[] = []

  const flush = () => {
    if (!currentLabel) return
    entries.push({ label: currentLabel, body: buffer.join("\n") })
  }

  for (const line of lines) {
    if (line.startsWith("# ")) {
      flush()
      currentLabel = line.slice(2).trim()
      buffer = []
      continue
    }
    buffer.push(line)
  }
  flush()
  return entries
}

const normalizeLines = (body: string): string[] =>
  body
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)

const requireSnapshot = (snapshots: SnapshotEntry[], label: string, anomalies: LayoutAnomaly[]): SnapshotEntry | null => {
  const snapshot = snapshots.find((entry) => entry.label === label) ?? null
  if (!snapshot) anomalies.push({ id: "missing-snapshot", message: `Missing required snapshot "${label}".` })
  return snapshot
}

const countPromptOccurrences = (body: string, prompt: string): number => normalizeLines(body).filter((line) => line.includes(prompt)).length

const requirePromptPresentOnce = (body: string, prompt: string, label: string, anomalies: LayoutAnomaly[]) => {
  const occurrences = countPromptOccurrences(body, prompt)
  if (occurrences !== 1) {
    anomalies.push({ id: "unexpected-prompt-count", message: `Snapshot "${label}" expected exactly one visible occurrence of "${prompt}", saw ${occurrences}.` })
  }
}

export const evaluateMaintenanceWrapperMultiturnOrdering = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const raw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []

  const turn1 = requireSnapshot(snapshots, "turn1-history", anomalies)
  const turn2 = requireSnapshot(snapshots, "turn2-history", anomalies)
  const turn3 = requireSnapshot(snapshots, "turn3-history", anomalies)

  if (turn1) requirePromptPresentOnce(turn1.body, PROMPT_1, turn1.label, anomalies)
  if (turn2) {
    requirePromptPresentOnce(turn2.body, PROMPT_2, turn2.label, anomalies)
    const prompt1Occurrences = countPromptOccurrences(turn2.body, PROMPT_1)
    if (prompt1Occurrences > 1) {
      anomalies.push({ id: "unexpected-prompt-count", message: `Snapshot "${turn2.label}" expected prompt 1 to appear at most once after scrolling, saw ${prompt1Occurrences}.` })
    }
  }
  if (turn3) {
    requirePromptPresentOnce(turn3.body, PROMPT_3, turn3.label, anomalies)
    const prompt2Occurrences = countPromptOccurrences(turn3.body, PROMPT_2)
    if (prompt2Occurrences > 1) {
      anomalies.push({ id: "unexpected-prompt-count", message: `Snapshot "${turn3.label}" expected prompt 2 to appear at most once after scrolling, saw ${prompt2Occurrences}.` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMaintenanceWrapperMultiturnOrdering(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
