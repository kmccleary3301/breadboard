import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const snapshotsPath = process.argv[2]
const statePath = process.argv[3]
if (!snapshotsPath || !statePath) {
  throw new Error("Usage: tsx scripts/p14_h4_transcript_raw_copy_gate.ts <pty_snapshots.txt> <repl_state.ndjson>")
}

const raw = await fs.readFile(snapshotsPath, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const populated = section("multiturn-populated-transcript-viewer")
const rawViewer = section("multiturn-raw-transcript-viewer")
const copyResult = section("multiturn-copy-export-result")

const requireIncludes = (text: string, needle: string, message: string) => {
  if (!text.includes(needle)) throw new Error(message)
}

requireIncludes(populated, "breadboard transcript viewer", "Transcript viewer did not open in populated scenario")
requireIncludes(populated, "H4_TURN1_USER_REQUEST", "Transcript viewer is missing turn 1 user request")
requireIncludes(populated, "H4_TURN1_ASSISTANT_READY", "Transcript viewer is missing turn 1 assistant response")
requireIncludes(populated, "H4_TURN2_USER_REQUEST", "Transcript viewer is missing turn 2 user request")
requireIncludes(populated, "H4_TURN2_ASSISTANT_FINAL", "Transcript viewer is missing turn 2 assistant response")
requireIncludes(populated, "H4_TOOL_SENTINEL", "Transcript viewer is missing tool summary content")
requireIncludes(rawViewer, "[raw]", "Raw viewer did not include raw transcript rows")
requireIncludes(rawViewer, "tool.exec.stdout.delta", "Raw viewer did not expose raw tool stdout event")
requireIncludes(rawViewer, "H4_TURN2_USER_R", "Raw viewer did not expose turn 2 user event")
requireIncludes(copyResult, "Transcript saved", "/copy did not surface transcript export confirmation")
if (copyResult.includes("TASK COMPLETE")) throw new Error("/copy appears to have submitted to the model")

const stateRaw = await fs.readFile(statePath, "utf8")
const records = stateRaw
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as any)
const latestWithCells = [...records].reverse().find((record) => Array.isArray(record.state?.transcriptCells))
const cells = latestWithCells?.state?.transcriptCells ?? []
const roles = cells.map((cell: any) => cell.role)
for (const role of ["user-request", "assistant-message", "tool-result"]) {
  if (!roles.includes(role)) throw new Error(`State dump missing transcript role ${role}`)
}
if (!records.some((record) => record.state?.viewPrefs?.rawStream === true)) {
  throw new Error("State dump never observed rawStream=true after /raw")
}

const transcriptDir = path.join(process.cwd(), "artifacts", "transcripts")
const transcriptFiles = await fs.readdir(transcriptDir).catch(() => [])
const candidates: Array<{ file: string; mtimeMs: number }> = []
for (const file of transcriptFiles) {
  if (!file.endsWith(".txt")) continue
  const fullPath = path.join(transcriptDir, file)
  const stat = await fs.stat(fullPath)
  candidates.push({ file: fullPath, mtimeMs: stat.mtimeMs })
}
candidates.sort((a, b) => b.mtimeMs - a.mtimeMs)
const exportPath = candidates[0]?.file
if (!exportPath) throw new Error("No transcript export artifact was written")
const exportText = await fs.readFile(exportPath, "utf8")
requireIncludes(exportText, "H4_TURN1_USER_REQUEST", "Transcript export missing turn 1 user request")
requireIncludes(exportText, "H4_TURN2_USER_REQUEST", "Transcript export missing turn 2 user request")
requireIncludes(exportText, "tool.exec.stdout.delta", "Transcript export missing raw event content after /raw")

console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, state: statePath, exportPath }, null, 2))
