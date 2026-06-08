import { promises as fs } from "node:fs"
import * as path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
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

export const evaluateAgentsTaskboardEntrypoint = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const raw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []

  requireSnapshot(snapshots, "agents-taskboard-open", [
    "Background tasks",
    "Implement SMTP server core",
    "Run SMTP smoke tests",
    "Reviewer finished requirements pass",
    "Task controls live in Task Focus",
  ], anomalies)

  requireSnapshot(snapshots, "agents-taskboard-after-resize", [
    "Background tasks",
    "Implement SMTP server",
    "ID: task-implementer-01",
    "Artifact: .breadboard/subagents/agent-task-implementer-01.jsonl",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "agents-taskboard-closed", ["enter send"], anomalies)
  if (closed.includes("Background tasks") || closed.includes("Task Focus")) {
    anomalies.push({ id: "agents-taskboard-still-open-after-close", message: "/agents taskboard remained visible after Escape." })
  }

  const forbidden = [
    "Multiagent controls are deferred",
    "Unknown slash command",
    "Unknown command",
    "Mock assistant: hello world",
    "TASK COMPLETE",
    "Tool execution results",
  ]
  for (const phrase of forbidden) {
    if (raw.includes(phrase)) {
      anomalies.push({ id: `forbidden-${idPart(phrase)}`, message: `/agents entrypoint emitted forbidden text: ${phrase}` })
    }
  }

  return anomalies
}

const run = async () => {
  const args = process.argv.slice(2)
  const caseDir = args[args.indexOf("--case-dir") + 1]
  if (!caseDir || args.indexOf("--case-dir") === -1) throw new Error("--case-dir is required")
  const anomalies = await evaluateAgentsTaskboardEntrypoint(path.resolve(caseDir))
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
