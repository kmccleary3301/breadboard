import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface PtyMetadata {
  readonly startedAt?: number
  readonly finishedAt?: number
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

const readJson = async <T>(filePath: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8")) as T
  } catch {
    return null
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

const resolveTuiRoot = (caseDir: string): string => {
  const marker = `${path.sep}docs_tmp${path.sep}`
  const index = caseDir.indexOf(marker)
  if (index > 0) return path.join(caseDir.slice(0, index), "tui_skeleton")
  return path.resolve(process.cwd())
}

export const evaluateMultiagentTaskLogExport = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText =
    (await readText(path.join(caseDir, "pty_snapshots.txt"))) ||
    (await readText(path.join(caseDir, "terminal_snapshots.txt")))
  const snapshots = parseSnapshots(snapshotsText)
  const exported = snapshots.get("task-focus-log-exported") ?? ""
  const closed = snapshots.get("task-log-export-closed") ?? ""

  for (const required of [
    "Task Focus",
    "O export",
    "Inspector tabs: Tools 1 | Diffs 0 | Approvals 0 | Artifacts 1 | Attempts 1 | Failures 0",
    "Task log saved to",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ]) {
    if (!exported.includes(required)) {
      anomalies.push({
        id: `missing-export-snapshot-${required.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`,
        message: `Task log export snapshot is missing expected text: ${required}`,
      })
    }
  }
  if (closed.includes("Task Focus") || closed.includes("Background tasks")) {
    anomalies.push({ id: "task-log-export-overlay-still-open", message: "Task overlays remained visible after closing." })
  }

  const metadata = await readJson<PtyMetadata>(path.join(caseDir, "pty_metadata.json"))
  const startedAt = typeof metadata?.startedAt === "number" ? metadata.startedAt - 5_000 : 0
  const finishedAt = typeof metadata?.finishedAt === "number" ? metadata.finishedAt + 5_000 : Date.now() + 5_000
  const exportDir = path.join(resolveTuiRoot(caseDir), "artifacts", "task-logs")

  let candidates: Array<{ readonly filePath: string; readonly mtimeMs: number; readonly contents: string }> = []
  try {
    const entries = await fs.readdir(exportDir, { withFileTypes: true })
    candidates = (
      await Promise.all(
        entries
          .filter((entry) => entry.isFile() && entry.name.startsWith("task-log-task-implementer-01-") && entry.name.endsWith(".jsonl"))
          .map(async (entry) => {
            const filePath = path.join(exportDir, entry.name)
            const stat = await fs.stat(filePath)
            const contents = await readText(filePath)
            return { filePath, mtimeMs: stat.mtimeMs, contents }
          }),
      )
    ).filter((entry) => entry.mtimeMs >= startedAt && entry.mtimeMs <= finishedAt)
  } catch {
    anomalies.push({ id: "task-log-export-dir-missing", message: `Task log export directory was not readable: ${exportDir}` })
    return anomalies
  }

  const matching = candidates.find(
    (entry) =>
      entry.contents.includes("SMTP_FOCUS_TAIL_LINE_01 create socket") &&
      entry.contents.includes("SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker"),
  )
  if (!matching) {
    anomalies.push({
      id: "task-log-export-content-missing",
      message: "No task log export file created during the case contained the deterministic implementer task log.",
    })
  }
  if (snapshotsText.includes("Task log export failed") || snapshotsText.includes("Task log export unavailable")) {
    anomalies.push({ id: "task-log-export-failure-notice", message: "Task log export emitted a failure/unavailable notice." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMultiagentTaskLogExport(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
