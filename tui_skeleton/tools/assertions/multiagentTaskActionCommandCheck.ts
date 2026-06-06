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

export const evaluateMultiagentTaskActionCommand = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText = await readText(path.join(caseDir, "pty_snapshots.txt"))
  const plain = await readText(path.join(caseDir, "pty_plain.txt"))
  const events = await readText(path.join(caseDir, "events.ndjson"))
  const snapshots = parseSnapshots(snapshotsText)

  requireSnapshot(snapshots, "task-focus-action-command-before", [
    "Task Focus",
    "status: running",
    "Mutation: engine-backed task controls enabled.",
    "Task controls: X cancel",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-action-command-cancelled", [
    "Task Focus",
    "cancelled",
    "Implement SMTP server core",
    "cancel requested for task-implementer-01.",
    "Mutation: engine-backed task controls enabled.",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-action-command-after-resize", [
    "Task Focus",
    "cancelled",
    "cancel requested for task-implementer-01.",
  ], anomalies)

  requireSnapshot(snapshots, "taskboard-after-action-command-return", [
    "Background tasks",
    "cancelled",
    "task-implementer-01",
    "Mutation: engine-backed task controls enabled.",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "task-action-command-closed", ["enter send"], anomalies)
  if (closed.includes("Task Focus") || closed.includes("Background tasks")) {
    anomalies.push({ id: "task-action-command-overlay-still-open", message: "Task action command overlays remained visible after closing." })
  }

  if (!events.includes('"type":"task_event"') || !events.includes('"status":"cancelled"')) {
    anomalies.push({ id: "missing-cancelled-task-event", message: "SSE event log did not include the cancelled task_event emitted after task_action." })
  }
  if (plain.includes("[command] task_action") || snapshotsText.includes("[command] task_action")) {
    anomalies.push({ id: "task-action-command-polluted-transcript", message: "Internal task_action command leaked into the readable transcript/tool rail." })
  }
  for (const forbidden of [
    "Mutation: unavailable until engine task-control endpoints exist.",
    "Reason: engine task mutation endpoint is not exposed yet.",
    "Unknown slash command",
    "Unknown command",
    "Command failed",
  ]) {
    if (snapshotsText.includes(forbidden)) {
      anomalies.push({ id: `forbidden-${idPart(forbidden)}`, message: `Enabled task-action lane included forbidden text: ${forbidden}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentTaskActionCommand(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
