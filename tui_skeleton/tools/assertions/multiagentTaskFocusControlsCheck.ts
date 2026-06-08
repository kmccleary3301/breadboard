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
    const label = trimmed.slice(0, newline).trim()
    const body = trimmed.slice(newline + 1)
    snapshots.set(label, body)
  }
  return snapshots
}

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
      anomalies.push({ id: `missing-${label}-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`, message: `Snapshot ${label} is missing expected text: ${needle}` })
    }
  }
  return body
}

const compactTailLoaded = (body: string, tailPath: string): boolean =>
  body.includes(`Loaded ${tailPath}`) &&
  body.includes("Tail loaded; output hidden to fit terminal height.")

export const evaluateMultiagentTaskFocusControls = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText =
    (await readText(path.join(caseDir, "pty_snapshots.txt"))) ||
    (await readText(path.join(caseDir, "terminal_snapshots.txt")))
  const snapshots = parseSnapshots(snapshotsText)

  requireSnapshot(snapshots, "taskboard-all-open", [
    "Background tasks",
    "Filter: all",
    "Implement SMTP server core",
    "Run SMTP smoke tests",
    "Reviewer finished requirements pass",
  ], anomalies)

  const blocked = requireSnapshot(snapshots, "taskboard-blocked-filter", [
    "Filter: blocked",
    "Run SMTP smoke tests",
  ], anomalies)
  if (blocked.includes("Implement SMTP server core")) {
    anomalies.push({ id: "blocked-filter-leaked-running-task", message: "Blocked task filter still shows the running implementer task." })
  }

  requireSnapshot(snapshots, "taskboard-lane-grouping", [
    "Group: lane",
    "reviewer",
    "implementer",
    "tester",
  ], anomalies)

  const tester = requireSnapshot(snapshots, "taskboard-lane-filter-tester", [
    "Lane: tester",
    "Run SMTP smoke tests",
  ], anomalies)
  if (tester.includes("Implement SMTP server core")) {
    anomalies.push({ id: "tester-lane-leaked-implementer", message: "Tester lane filter still shows the implementer task." })
  }

  requireSnapshot(snapshots, "taskboard-lane-filter-implementer", [
    "Lane: implementer",
    "Implement SMTP server core",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-snippet-tail", [
    "Task Focus",
    "implementer",
    "View: snippet",
    "Inspector tabs: Tools 1 | Diffs 0 | Approvals 0 | Artifacts 1 | Attempts 1 | Failures 0",
    "Tail: .breadboard/subagents/agent-task-implementer-01.jsonl",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-raw-tail", [
    "Task Focus",
    "View: raw",
    "Inspector tabs: Tools 1 | Diffs 0 | Approvals 0 | Artifacts 1 | Attempts 1 | Failures 0",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-follow-paused", [
    "Task Focus",
    "P follow",
    "View: snippet",
  ], anomalies)

  const resizedFocus = requireSnapshot(snapshots, "task-focus-after-resize", [
    "Task Focus",
  ], anomalies)
  if (
    !resizedFocus.includes("SMTP_FOCUS_TAIL_LINE_11") &&
    !compactTailLoaded(resizedFocus, ".breadboard/subagents/agent-task-implementer-01.jsonl")
  ) {
    anomalies.push({
      id: "missing-task-focus-after-resize-tail-or-compact-loaded-state",
      message:
        "Snapshot task-focus-after-resize must show the deterministic tail marker or the explicit compact loaded-tail fallback.",
    })
  }

  const returnedTaskboard = requireSnapshot(snapshots, "taskboard-after-focus-return", [
    "Background tasks",
    "ID: task-implementer-01",
    "Artifact: .breadboard/subagents/agent-task-implementer-01.jsonl",
  ], anomalies)
  if (!returnedTaskboard.includes("Lane: implementer") && !returnedTaskboard.includes("▾ implementer")) {
    anomalies.push({
      id: "missing-taskboard-after-focus-return-implementer-lane-identity",
      message:
        "Snapshot taskboard-after-focus-return must preserve implementer lane identity via the header or compact group label.",
    })
  }

  const closed = requireSnapshot(snapshots, "task-focus-controls-closed", [
    "enter send",
  ], anomalies)
  if (closed.includes("Task Focus") || closed.includes("Background tasks")) {
    anomalies.push({ id: "task-overlay-still-open-after-close", message: "Task focus/taskboard overlay remained visible after closing." })
  }

  const forbidden = ["No task selected.", "Unable to load task output.", "Path is outside workspace", "Path is not a file"]
  for (const phrase of forbidden) {
    if (snapshotsText.includes(phrase)) {
      anomalies.push({ id: `forbidden-${phrase.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`, message: `Task focus controls emitted forbidden failure text: ${phrase}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentTaskFocusControls(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
