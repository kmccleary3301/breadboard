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

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") {
      caseDir = args[++i]
    }
  }
  if (!caseDir) {
    throw new Error("--case-dir is required")
  }
  return { caseDir: path.resolve(caseDir) }
}

const parseSnapshots = (raw: string): SnapshotEntry[] => {
  const lines = raw.split(/\r?\n/)
  const entries: SnapshotEntry[] = []
  let currentLabel: string | null = null
  let buffer: string[] = []

  const flush = () => {
    if (!currentLabel) return
    entries.push({
      label: currentLabel,
      body: buffer.join("\n"),
    })
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

const requireSnapshot = (snapshots: SnapshotEntry[], label: string, anomalies: LayoutAnomaly[]): SnapshotEntry | null => {
  const snapshot = snapshots.find((entry) => entry.label === label) ?? null
  if (!snapshot) {
    anomalies.push({
      id: "missing-snapshot",
      message: `Missing required snapshot "${label}".`,
    })
  }
  return snapshot
}

const checkOrderedLines = (body: string, specs: { token: string; mode: "contains" | "exact" }[], label: string, anomalies: LayoutAnomaly[]) => {
  const lines = body.split("\n")
  let cursor = -1
  for (const spec of specs) {
    const index = lines.findIndex((line, lineIndex) => {
      if (lineIndex <= cursor) return false
      const normalized = line.trim()
      return spec.mode === "exact" ? normalized === spec.token : normalized.includes(spec.token)
    })
    if (index === -1) {
      anomalies.push({
        id: "missing-token",
        message: `Snapshot "${label}" is missing ${spec.mode === "exact" ? "exact" : "matching"} token "${spec.token}".`,
      })
      return
    }
    cursor = index
  }
}

const countOccurrences = (body: string, token: string): number =>
  body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line === token).length

const checkNoConcatenatedPrompts = (body: string, label: string, anomalies: LayoutAnomaly[]) => {
  const malformedPrompt = /English word\.What is \d\+\d\?/
  if (malformedPrompt.test(body)) {
    anomalies.push({
      id: "concatenated-user-prompt",
      message: `Snapshot "${label}" contains a stale prompt concatenated with a later prompt.`,
    })
  }
}

const checkNoLifecycleHints = (body: string, label: string, anomalies: LayoutAnomaly[]) => {
  const lifecycleLines = body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => /^(?:Log link available\.?|(?:[●•-]\s+)?Log\s+·\s+file:\/\/logging\/\S+|\[log\]\s+file:\/\/logging\/\S+|Run finished(?:[ (.]|$)|[•-]\s+(?:Log link available\.?|Run finished(?:[ (.]|$)|✻ Cooked for)|✻ Cooked for)/.test(line))
  if (lifecycleLines.length > 0) {
    anomalies.push({
      id: "floating-lifecycle-hint",
      message: `Snapshot "${label}" contains lifecycle chrome outside the readable transcript: ${lifecycleLines.join(" | ")}`,
    })
  }
}

const run = async () => {
  const { caseDir } = parseArgs()
  const raw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []

  const turn1 = requireSnapshot(snapshots, "turn1-history", anomalies)
  const turn2 = requireSnapshot(snapshots, "turn2-history", anomalies)
  const turn3 = requireSnapshot(snapshots, "turn3-history", anomalies)

  if (turn1) {
    checkNoConcatenatedPrompts(turn1.body, turn1.label, anomalies)
    checkNoLifecycleHints(turn1.body, turn1.label, anomalies)
    checkOrderedLines(
      turn1.body,
      [
        { token: "two", mode: "exact" },
      ],
      turn1.label,
      anomalies,
    )
  }
  if (turn2) {
    checkNoConcatenatedPrompts(turn2.body, turn2.label, anomalies)
    checkNoLifecycleHints(turn2.body, turn2.label, anomalies)
    checkOrderedLines(
      turn2.body,
      [
        { token: "two", mode: "exact" },
        { token: "four", mode: "exact" },
      ],
      turn2.label,
      anomalies,
    )
  }
  if (turn3) {
    checkNoConcatenatedPrompts(turn3.body, turn3.label, anomalies)
    checkNoLifecycleHints(turn3.body, turn3.label, anomalies)
    checkOrderedLines(
      turn3.body,
      [
        { token: "two", mode: "exact" },
        { token: "four", mode: "exact" },
        { token: "six", mode: "exact" },
      ],
      turn3.label,
      anomalies,
    )
    for (const token of ["two", "four", "six"]) {
      const occurrences = countOccurrences(turn3.body, token)
      if (occurrences !== 1) {
        anomalies.push({
          id: "unexpected-token-count",
          message: `Snapshot "${turn3.label}" expected exactly 1 exact answer line for "${token}", saw ${occurrences}.`,
        })
      }
    }
  }

  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

void run().catch((error) => {
  console.error((error as Error).message)
  process.exit(1)
})
