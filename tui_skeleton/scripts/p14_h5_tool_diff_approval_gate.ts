import { promises as fs } from "node:fs"
import process from "node:process"

const [snapshotsPath, statePath] = process.argv.slice(2)
if (!snapshotsPath || !statePath) {
  throw new Error("Usage: tsx scripts/p14_h5_tool_diff_approval_gate.ts <pty_snapshots.txt> <repl_state.ndjson>")
}

const snapshots = await fs.readFile(snapshotsPath, "utf8")
const section = (label: string): string => snapshots.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const toolSnapshot = section("tool-diff-transcript-before-permission")
const permissionSummary = section("permission-summary-modal")
const permissionDiff = section("permission-diff-modal")
const transcriptViewer = section("h5-transcript-viewer")

const requireIncludes = (text: string, needle: string, message: string) => {
  if (!text.includes(needle)) throw new Error(message)
}
const requireExcludes = (text: string, needle: string, message: string) => {
  if (text.includes(needle)) throw new Error(message)
}

requireIncludes(toolSnapshot, "H5_TOOL_SUCCESS", "Tool success output is missing from PTY transcript")
requireIncludes(toolSnapshot, "H5_TOOL_ERROR", "Tool error output is missing from PTY transcript")
requireIncludes(toolSnapshot, "H5_DETAIL_TRUNCATED", "Long-output truncation affordance is missing")
requireIncludes(toolSnapshot, "Patch(src/h5_example.ts)", "Diff tool row is missing")
requireIncludes(permissionSummary, "Permission required", "Permission modal did not open")
requireIncludes(permissionSummary, "write_files", "Permission modal did not identify the requested tool")
requireIncludes(permissionDiff + permissionSummary, "src/h5_example.ts", "Permission modal did not expose diff file context")
requireIncludes(transcriptViewer, "breadboard transcript viewer", "Transcript viewer did not open")
requireIncludes(transcriptViewer, "[permission]", "Transcript viewer did not include approval rows")
requireExcludes(transcriptViewer, "permission_decision", "Internal permission_decision command leaked into readable transcript")
requireExcludes(snapshots, "Log · file://", "Raw log-link artifact leaked into H5 readable snapshots")

const records = (await fs.readFile(statePath, "utf8"))
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as any)
const latestWithCells = [...records].reverse().find((record) => Array.isArray(record.state?.transcriptCells))
const cells = latestWithCells?.state?.transcriptCells ?? []
const roles = cells.map((cell: any) => cell.role)
for (const role of ["user-request", "assistant-message", "tool-result", "tool-error", "diff", "approval"]) {
  if (!roles.includes(role)) throw new Error(`State dump missing transcript role ${role}`)
}
const previews = cells.map((cell: any) => String(cell.textPreview ?? "")).join("\n")
requireIncludes(previews, "H5_USER_REQUEST", "State dump missing durable user request")
requireIncludes(previews, "H5_ASSISTANT_FINAL", "State dump missing final assistant message")
requireExcludes(previews, "permission_decision", "Internal permission_decision command leaked into transcript cells")

console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, state: statePath, roles }, null, 2))
