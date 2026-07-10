import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const caseDir = process.argv[2] ? path.resolve(process.argv[2]) : ""
if (!caseDir) throw new Error("Usage: tsx tools/assertions/liveTranscriptViewerCheck.ts <case-dir>")

const read = async (file: string) => fs.readFile(file, "utf8").catch(() => "")
const parseRecords = (raw: string): any[] =>
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line)]
      } catch {
        return []
      }
    })

const snapshots = await read(path.join(caseDir, "pty_snapshots.txt"))
const records = parseRecords(await read(path.join(caseDir, "repl_state.ndjson")))
const latest = records.at(-1)?.state ?? {}
const cells = Array.isArray(latest.transcriptCells) ? latest.transcriptCells : []
const roles = new Set(cells.map((cell: any) => String(cell?.role ?? "")))
const findings: string[] = []

for (const marker of [
  "# live-transcript-viewer",
  "breadboard transcript viewer",
  "LIVE_TRANSCRIPT_TURN1_ASSISTANT",
  "LIVE_TRANSCRIPT_TURN2_ASSISTANT",
  "# live-transcript-dismissed",
]) {
  if (!snapshots.includes(marker)) findings.push(`missing snapshot marker: ${marker}`)
}
for (const role of ["user-request", "assistant-message"]) {
  if (!roles.has(role)) findings.push(`missing transcript role: ${role}`)
}
if (snapshots.includes("Log · file://")) findings.push("raw log-link artifact leaked into transcript snapshots")
if (latest.pendingResponse !== false) findings.push("final state still has pendingResponse=true")

const report = {
  caseDir,
  ok: findings.length === 0,
  findings,
  finalStatus: latest.status ?? null,
  conversationCount: latest.counts?.conversation ?? null,
}
await fs.writeFile(path.join(caseDir, "live_transcript_viewer_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
if (!report.ok) process.exitCode = 1
