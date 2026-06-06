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

export const evaluateMultiagentTaskActionGuards = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText = await readText(path.join(caseDir, "pty_snapshots.txt"))
  const snapshots = parseSnapshots(snapshotsText)

  requireSnapshot(snapshots, "taskboard-action-hints", [
    "Background tasks",
    "Task controls live in Task Focus",
    "F then X cancel",
    "Y retry",
    "U pause/resume",
    "M merge",
    "Mutation: unavailable until engine task-control endpoints exist.",
  ], anomalies)


  requireSnapshot(snapshots, "task-focus-action-hints", [
    "Task Focus",
    "Task controls: X cancel",
    "Mutation: unavailable until engine task-control endpoints exist.",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-cancel-unavailable", [
    "Task Focus",
    "cancel unavailable for task-implementer-01.",
    "Reason: engine task mutation endpoint is not exposed yet.",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-retry-unavailable", [
    "Task Focus",
    "retry unavailable for task-implementer-01.",
    "Reason: engine task mutation endpoint is not exposed yet.",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-pause-resume-unavailable", [
    "Task Focus",
    "pause/resume unavailable for task-implementer-01.",
    "Reason: engine task mutation endpoint is not exposed yet.",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-merge-unavailable", [
    "Task Focus",
    "merge unavailable for task-implementer-01.",
    "Reason: engine task mutation endpoint is not exposed yet.",
  ], anomalies)

  requireSnapshot(snapshots, "task-focus-action-guards-after-resize", [
    "Task Focus",
    "Task controls: X cancel",
    "Mutation: unavailable until engine task-control endpoints exist.",
  ], anomalies)

  requireSnapshot(snapshots, "taskboard-after-action-guard-return", [
    "Background tasks",
    "Task controls live in Task Focus",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "task-action-guards-closed", ["enter send"], anomalies)
  if (closed.includes("Task Focus") || closed.includes("Background tasks")) {
    anomalies.push({ id: "task-action-overlay-still-open-after-close", message: "Task action guard overlays remained visible after closing." })
  }

  const forbidden = [
    "cancelled task-implementer-01",
    "retrying task-implementer-01",
    "merged task-implementer-01",
    "No task selected.",
    "Unable to load task output.",
  ]
  for (const phrase of forbidden) {
    if (snapshotsText.includes(phrase)) {
      anomalies.push({ id: `forbidden-${idPart(phrase)}`, message: `Task action guard emitted forbidden text: ${phrase}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentTaskActionGuards(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
