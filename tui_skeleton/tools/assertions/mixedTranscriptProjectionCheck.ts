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

const readOptional = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const section = (snapshots: string, label: string): string =>
  snapshots.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""

const latestTranscriptCells = async (statePath: string): Promise<any[]> => {
  const raw = await fs.readFile(statePath, "utf8")
  const records = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as any)
  const latest = [...records].reverse().find((record) => Array.isArray(record.state?.transcriptCells))
  return latest?.state?.transcriptCells ?? []
}

const requireIncludes = (anomalies: LayoutAnomaly[], id: string, text: string, needle: string, message: string) => {
  if (!text.includes(needle)) anomalies.push({ id, message })
}

const requireExcludes = (anomalies: LayoutAnomaly[], id: string, text: string, needle: string, message: string) => {
  if (text.includes(needle)) anomalies.push({ id, message })
}

export const evaluateMixedTranscriptProjection = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshots = await readOptional(path.join(caseDir, "pty_snapshots.txt"))
  const toolSnapshot = section(snapshots, "tool-diff-transcript-before-permission")
  const permissionSummary = section(snapshots, "permission-summary-modal")
  const permissionDiff = section(snapshots, "permission-diff-modal")
  const transcriptViewer = section(snapshots, "h5-transcript-viewer")

  const visibleEvidence = `${snapshots}\n${toolSnapshot}\n${transcriptViewer}`
  requireIncludes(anomalies, "permission-modal-missing", permissionSummary, "Permission required", "Permission modal did not open.")
  requireIncludes(anomalies, "permission-tool-missing", permissionSummary, "write_files", "Permission modal did not identify the requested tool.")
  requireIncludes(anomalies, "permission-diff-context-missing", `${permissionDiff}\n${permissionSummary}`, "src/h5_example.ts", "Permission modal did not expose diff file context.")
  requireIncludes(anomalies, "viewer-missing", transcriptViewer, "Press / to search", "Transcript viewer did not open or did not render stable viewer controls.")
  requireIncludes(anomalies, "viewer-approval-row-missing", transcriptViewer, "[permission]", "Transcript viewer did not include approval rows.")
  requireExcludes(anomalies, "permission-decision-leak", transcriptViewer, "permission_decision", "Internal permission_decision command leaked into readable transcript viewer.")
  requireExcludes(anomalies, "log-link-leak", snapshots, "Log · file://", "Raw log-link artifact leaked into H5 readable snapshots.")

  const cells = await latestTranscriptCells(path.join(caseDir, "repl_state.ndjson"))
  const roles = cells.map((cell) => cell.role)
  for (const role of ["user-request", "assistant-message", "tool-result", "tool-error", "diff", "approval"]) {
    if (!roles.includes(role)) {
      anomalies.push({ id: `state-role-${role}-missing`, message: `State dump missing transcript role ${role}.` })
    }
  }
  const previews = cells.map((cell) => String(cell.textPreview ?? "")).join("\n")
  const combinedEvidence = `${visibleEvidence}\n${previews}`
  requireIncludes(anomalies, "tool-success-missing", combinedEvidence, "H5_TOOL_SUCCESS", "Tool success output is missing from transcript evidence.")
  requireIncludes(anomalies, "tool-error-missing", combinedEvidence, "H5_TOOL_ERROR", "Tool error output is missing from transcript evidence.")
  requireIncludes(anomalies, "tool-truncation-missing", combinedEvidence, "H5_DETAIL_TRUNCATED", "Long-output truncation affordance is missing.")
  requireIncludes(anomalies, "diff-row-missing", combinedEvidence, "Patch(src/h5_example.ts)", "Diff tool row is missing.")
  requireIncludes(anomalies, "state-user-request-missing", previews, "H5_USER_REQUEST", "State dump missing durable user request.")
  requireIncludes(anomalies, "state-final-assistant-missing", previews, "H5_ASSISTANT_FINAL", "State dump missing final assistant message.")
  requireExcludes(anomalies, "state-permission-decision-leak", previews, "permission_decision", "Internal permission_decision command leaked into transcript cells.")
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMixedTranscriptProjection(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
