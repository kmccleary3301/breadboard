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

const assertNoModelSubmissionForLocalCommands = (
  text: string,
  command: string,
  anomalies: LayoutAnomaly[],
) => {
  const commandIndex = text.indexOf(command)
  if (commandIndex < 0) {
    return
  }
  const tail = text.slice(commandIndex, commandIndex + 1800)
  for (const forbidden of ["Mock assistant:", "TASK COMPLETE", "Tool execution results", "● Tool", "Here’s", "I’m ready"]) {
    if (tail.includes(forbidden)) {
      anomalies.push({
        id: `local-command-submitted-to-model-${idPart(command)}-${idPart(forbidden)}`,
        message: `${command} appears to have submitted to the model or produced model/tool transcript output: ${forbidden}`,
      })
    }
  }
}

export const evaluateMultiagentTaskSlashInspectFollow = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText = await readText(path.join(caseDir, "pty_snapshots.txt"))
  const plain = await readText(path.join(caseDir, "pty_plain.txt"))
  const events = await readText(path.join(caseDir, "events.ndjson"))
  const replState = await readText(path.join(caseDir, "repl_state.ndjson"))
  const snapshots = parseSnapshots(snapshotsText)

  requireSnapshot(snapshots, "slash-inspect-task-open", [
    "Task Focus",
    "Implement SMTP server core",
    "Tail: .breadboard/subagents/agent-task-implementer-01.jsonl",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ], anomalies)

  const resized = requireSnapshot(snapshots, "slash-inspect-task-after-resize", [
    "Task Focus",
    "Implement SMTP server",
  ], anomalies)
  if (
    !resized.includes("SMTP_FOCUS_TAIL_LINE_11") &&
    !resized.includes("Tail loaded; output hidden to fit terminal height.")
  ) {
    anomalies.push({
      id: "slash-inspect-task-resize-lost-tail-state",
      message: "/inspect task compact resize must preserve either the task tail marker or explicit compact loaded-tail fallback.",
    })
  }

  requireSnapshot(snapshots, "slash-follow-task-paused", [], anomalies)
  for (const needle of [
    "Task Focus follow paused.",
    "Scope: local Task Focus tail only; no task state mutation or model submission occurred.",
  ]) {
    if (!plain.includes(needle)) {
      anomalies.push({ id: `missing-plain-${idPart(needle)}`, message: `Readable PTY output is missing expected local command copy: ${needle}` })
    }
  }

  requireSnapshot(snapshots, "slash-follow-task-status-paused", [
    "Task Focus follow: paused.",
  ], anomalies)

  requireSnapshot(snapshots, "slash-follow-task-resumed", ["Task Focus"], anomalies)
  if (!plain.includes("Task Focus follow resumed.")) {
    anomalies.push({
      id: "missing-plain-task-focus-follow-resumed",
      message: "Readable PTY output is missing expected local command copy: Task Focus follow resumed.",
    })
  }

  requireSnapshot(snapshots, "slash-follow-task-status-resumed", [
    "Task Focus follow: resumed.",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "slash-task-commands-closed", ["enter send"], anomalies)
  if (closed.includes("│  Task Focus") || closed.includes("│  Background tasks")) {
    anomalies.push({ id: "slash-task-command-overlay-still-open", message: "Task command overlays remained visible after closing." })
  }

  for (const command of ["/follow task", "/follow task status"]) {
    assertNoModelSubmissionForLocalCommands(plain, command, anomalies)
  }
  if (/"lastTurn":2\b/.test(replState) || replState.includes('"preview":"follow task status"') || plain.includes("❯ follow task status")) {
    anomalies.push({
      id: "local-task-follow-command-submitted-as-model-turn",
      message: "A Task Focus follow command was submitted as a model turn instead of remaining UI-local.",
    })
  }

  if (!events.includes('"type":"task_event"') || !events.includes("task-implementer-01")) {
    anomalies.push({ id: "missing-task-event-input-state", message: "SSE event log did not establish task-implementer-01 before slash command inspection." })
  }

  for (const forbidden of [
    "Unknown slash command",
    "Unknown command",
    "No task selected.",
    "Run /tasks after multiagent/background work starts",
    "Unable to load task output.",
    "Path is outside workspace",
    "Path is not a file",
  ]) {
    if (snapshotsText.includes(forbidden) || plain.includes(forbidden)) {
      anomalies.push({ id: `forbidden-${idPart(forbidden)}`, message: `Slash task command lane emitted forbidden text: ${forbidden}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentTaskSlashInspectFollow(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
