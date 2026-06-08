import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const caseDir = process.argv[2] ? path.resolve(process.argv[2]) : ""
if (!caseDir) throw new Error("Usage: tsx tools/assertions/liveStopRetrySettledCheck.ts <case-dir>")

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
const text = cells.map((cell: any) => String(cell?.textPreview ?? "")).join("\n")

const findings: string[] = []
if (!snapshots.includes("# after-stop")) findings.push("missing after-stop snapshot")
if (!snapshots.includes("# after-retry-settled")) findings.push("missing after-retry-settled snapshot")
if (!snapshots.includes("[command] stop")) findings.push("stop command was not visible")
if (!snapshots.includes("Resubmitting prompt")) findings.push("retry command did not surface resubmission")
if (!snapshots.includes("STOP_RETRY_DONE") && !text.includes("STOP_RETRY_DONE")) {
  findings.push("settled retry answer did not include STOP_RETRY_DONE")
}
if (latest.pendingResponse !== false) findings.push("final state still has pendingResponse=true")
if (latest.lastConversation?.speaker !== "assistant") findings.push("final conversation speaker is not assistant")
if (latest.lastConversation?.phase !== "final") findings.push("final conversation is not final")

const report = {
  caseDir,
  ok: findings.length === 0,
  findings,
  finalStatus: latest.status ?? null,
  conversationCount: latest.counts?.conversation ?? null,
}
await fs.writeFile(path.join(caseDir, "live_stop_retry_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
if (!report.ok) process.exitCode = 1
