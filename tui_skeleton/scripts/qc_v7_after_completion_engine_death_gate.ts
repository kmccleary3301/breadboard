import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) throw new Error("Usage: qc_v7_after_completion_engine_death_gate.ts <artifact-dir>")
const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const prompt = read("prompt.txt").trim()
const stateRaw = read("state.ndjson")
const killLog = read("kill.log")
const snapshots = read("snapshots.txt")
const records = stateRaw
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => {
    try { return JSON.parse(line) as any } catch { return null }
  })
  .filter(Boolean)
const killedPid = Number(killLog.match(/killed_pid=(\d+)/)?.[1] ?? NaN)
const lastRecord = records[records.length - 1] ?? null
const state = lastRecord?.state ?? null
const lifecycle = state?.lifecycle ?? null
const status = String(state?.status ?? "")
const conversation = Array.isArray(state?.conversation) ? state.conversation : []
const transcriptCells = Array.isArray(state?.transcriptCells)
  ? state.transcriptCells
  : Array.isArray(lastRecord?.transcriptCells)
    ? lastRecord.transcriptCells
    : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const assistantCount = conversation.filter((entry: any) => entry?.speaker === "assistant" && String(entry?.text ?? "").includes("V7-COMPLETED-RESPONSE-A")).length
const assistantCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "assistant" && String(cell?.text ?? cell?.textPreview ?? "").includes("V7-COMPLETED-RESPONSE-A")).length
const lineAVisible = /V7-COMPLETED-RESPONSE-A/.test(snapshots)
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const recovered = /Recovered \(new session\)/i.test(status)
const disconnected = state?.disconnected === true
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = !recovered && /\[ready\]/i.test(snapshots) && /Engine interrupted|session is no longer available|Restarting owned engine/i.test(snapshots)
const duplicateAssistant = assistantCount > 1 || assistantCellCount > 1

const report = [
  "# V7 After Completion Engine Death Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `records: ${records.length}`,
  `killedPid: ${Number.isFinite(killedPid) ? killedPid : "none"}`,
  `lastStatus: ${status || "none"}`,
  `lastLifecycleMode: ${lifecycle?.mode ?? "none"}`,
  `lastLifecycleOwned: ${String(lifecycle?.owned ?? "none")}`,
  `lastLifecyclePid: ${String(lifecycle?.pid ?? "none")}`,
  `pidChanged: ${pidChanged}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `disconnected: ${String(disconnected)}`,
  `recovered: ${recovered}`,
  `userPromptCount: ${userPromptCount}`,
  `userCellCount: ${userCellCount}`,
  `assistantCount: ${assistantCount}`,
  `assistantCellCount: ${assistantCellCount}`,
  `lineAVisible: ${lineAVisible}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateAssistant: ${duplicateAssistant}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine after completed turn")
if (records.length === 0) failures.push("no state dump records captured")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (assistantCount !== 1) failures.push(`expected exactly one completed assistant response in conversation, saw ${assistantCount}`)
if (assistantCellCount !== 1) failures.push(`expected exactly one completed assistant response transcript cell, saw ${assistantCellCount}`)
if (!lineAVisible) failures.push("expected completed assistant response to remain visible in snapshots")
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (!recovered) failures.push("completed-turn engine death should recover to a new usable session")
if (disconnected) failures.push("completed-turn engine death should not leave the TUI disconnected")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("snapshot shows [ready] while engine recovery is unresolved")
if (duplicateAssistant) failures.push("completed assistant response duplicated in final state")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][after-completion-engine-death] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
