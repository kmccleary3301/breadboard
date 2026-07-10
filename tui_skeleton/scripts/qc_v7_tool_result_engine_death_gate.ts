import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) throw new Error("Usage: qc_v7_tool_result_engine_death_gate.ts <artifact-dir>")
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
const toolEvents = Array.isArray(state?.toolEvents) ? state.toolEvents : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const toolResultEventCount = toolEvents.filter((entry: any) => String(entry?.text ?? "").includes("V7-TOOL-RESULT-A") && entry?.status === "success").length
const toolResultCellCount = transcriptCells.filter((cell: any) => String(cell?.text ?? cell?.textPreview ?? "").includes("V7-TOOL-RESULT-A") && cell?.status === "success").length
const lineAVisible = /V7-TOOL-RESULT-A/.test(snapshots)
const finalAssistantPresent = conversation.some((entry: any) => String(entry?.text ?? "").includes("V7-FINAL-AFTER-TOOL")) ||
  transcriptCells.some((cell: any) => String(cell?.text ?? cell?.textPreview ?? "").includes("V7-FINAL-AFTER-TOOL")) ||
  /V7-FINAL-AFTER-TOOL/.test(snapshots)
const looksReady = /ready/i.test(status)
const explicitRecovery = /recover|restart|reconnect|disconnect|engine|session missing/i.test(status)
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = /\[ready\]/i.test(snapshots) && /Engine interrupted|session is no longer available|Restarting owned engine/i.test(snapshots)
const duplicateToolResult = toolResultEventCount > 1 || toolResultCellCount > 1
const completionSeen = state?.completionSeen === true || state?.completionReached === true

const report = [
  "# V7 Tool Result Engine Death Gate Report",
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
  `disconnected: ${String(state?.disconnected ?? "none")}`,
  `userPromptCount: ${userPromptCount}`,
  `userCellCount: ${userCellCount}`,
  `toolResultEventCount: ${toolResultEventCount}`,
  `toolResultCellCount: ${toolResultCellCount}`,
  `lineAVisible: ${lineAVisible}`,
  `finalAssistantPresent: ${finalAssistantPresent}`,
  `completionSeen: ${completionSeen}`,
  `looksReady: ${looksReady}`,
  `explicitRecovery: ${explicitRecovery}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateToolResult: ${duplicateToolResult}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine after tool result")
if (records.length === 0) failures.push("no state dump records captured")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (toolResultEventCount + toolResultCellCount < 1) failures.push("expected tool result line to remain present")
if (!lineAVisible) failures.push("expected tool result line to remain visible in snapshots")
if (finalAssistantPresent) failures.push("final assistant content should not appear because engine was killed before post-tool assistant response")
if (completionSeen) failures.push("completion should not be seen because engine was killed before completion")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("snapshot shows [ready] while engine/tool recovery is unresolved")
if (looksReady && !explicitRecovery) failures.push("final status looks ready without explicit recovery semantics")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (duplicateToolResult) failures.push("tool result duplicated in final state")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][tool-result-engine-death] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
