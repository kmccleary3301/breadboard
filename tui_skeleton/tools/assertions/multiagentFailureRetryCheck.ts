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

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const parseSnapshots = (text: string): Map<string, string> => {
  const snapshots = new Map<string, string>()
  const parts = text.split(/^# /m)
  for (const part of parts) {
    const trimmed = part.trimEnd()
    if (!trimmed) continue
    const newline = trimmed.indexOf("\n")
    if (newline === -1) continue
    snapshots.set(trimmed.slice(0, newline).trim(), trimmed.slice(newline + 1))
  }
  return snapshots
}

const idPart = (value: string): string => value.replace(/[^a-z0-9]+/gi, "-").toLowerCase()

const requireSnapshot = (
  snapshots: Map<string, string>,
  label: string,
  required: readonly string[],
  anomalies: LayoutAnomaly[],
): string => {
  const body = snapshots.get(label) ?? ""
  if (!body.trim()) {
    anomalies.push({ id: `missing-${label}`, message: `Snapshot ${label} is missing or empty.` })
    return body
  }
  for (const needle of required) {
    if (!body.includes(needle)) {
      anomalies.push({ id: `missing-${label}-${idPart(needle)}`, message: `Snapshot ${label} is missing expected text: ${needle}` })
    }
  }
  return body
}

const compactTailLoaded = (body: string, tailPath: string): boolean =>
  body.includes(`Loaded ${tailPath}`) &&
  body.includes("Tail loaded; output hidden to fit terminal height.")

export const evaluateMultiagentFailureRetry = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText =
    (await readText(path.join(caseDir, "pty_snapshots.txt"))) ||
    (await readText(path.join(caseDir, "terminal_snapshots.txt")))
  const snapshots = parseSnapshots(snapshotsText)

  const main = requireSnapshot(snapshots, "failure-main-transcript-summary", [
    "Failure summary: tester failed retryable; raw log kept in task artifact.",
  ], anomalies)
  if (main.includes("FAILURE_TAIL_LINE_05") || main.includes("Tester started SMTP negative path")) {
    anomalies.push({ id: "raw-log-flooded-main-transcript", message: "Failed task raw log lines leaked into the main readable transcript snapshot." })
  }

  requireSnapshot(snapshots, "taskboard-failed-filter", [
    "Background tasks",
    "Filter: failed",
    "ID: task-tester-fail-01",
    "Error: SMTP_TEST_FAILURE missing DATA terminator",
    "Artifact: .breadboard/subagents/agent-task-tester-fail-01.jsonl",
  ], anomalies)

  requireSnapshot(snapshots, "failed-task-focus-tail", [
    "Task Focus",
    "status: failed",
    "SMTP_TEST_FAILURE missing DATA terminator",
    "Tail: .breadboard/subagents/agent-task-tester-fail-01.jsonl",
    "FAILURE_TAIL_LINE_05 deterministic failure marker",
  ], anomalies)

  const resizedFocus = requireSnapshot(snapshots, "failed-task-focus-after-resize", [
    "Task Focus",
    "status: failed",
  ], anomalies)
  if (
    !resizedFocus.includes("FAILURE_TAIL_LINE_05") &&
    !compactTailLoaded(resizedFocus, ".breadboard/subagents/agent-task-tester-fail-01.jsonl")
  ) {
    anomalies.push({
      id: "missing-failed-task-focus-after-resize-tail-or-compact-loaded-state",
      message:
        "Snapshot failed-task-focus-after-resize must show the deterministic failure tail marker or the explicit compact loaded-tail fallback.",
    })
  }

  requireSnapshot(snapshots, "taskboard-after-failed-focus-return", [
    "Background tasks",
    "Filter: failed",
    "ID: task-tester-fail-01",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "failure-task-controls-closed", ["enter send"], anomalies)
  if (closed.includes("Task Focus") || closed.includes("Background tasks")) {
    anomalies.push({ id: "failure-task-overlay-still-open-after-close", message: "Failed-task overlay remained visible after closing." })
  }

  const forbidden = ["No task selected.", "Unable to load task output.", "Path is outside workspace", "Path is not a file"]
  for (const phrase of forbidden) {
    if (snapshotsText.includes(phrase)) {
      anomalies.push({ id: `forbidden-${idPart(phrase)}`, message: `Failure/retry task controls emitted forbidden failure text: ${phrase}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentFailureRetry(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
