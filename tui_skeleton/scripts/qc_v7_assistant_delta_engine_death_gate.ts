import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) throw new Error("Usage: qc_v7_assistant_delta_engine_death_gate.ts <artifact-dir>")
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
const assistantAConversationCount = conversation.filter((entry: any) => entry?.speaker === "assistant" && String(entry?.text ?? "").includes("V7-ASSISTANT-DELTA-A")).length
const assistantACellCount = transcriptCells.filter((cell: any) => cell?.speaker === "assistant" && String(cell?.text ?? cell?.textPreview ?? "").includes("V7-ASSISTANT-DELTA-A")).length
const lineAVisible = /V7-ASSISTANT-DELTA-A/.test(snapshots)
const lineBPresent = conversation.some((entry: any) => String(entry?.text ?? "").includes("V7-ASSISTANT-DELTA-B")) ||
  transcriptCells.some((cell: any) => String(cell?.text ?? cell?.textPreview ?? "").includes("V7-ASSISTANT-DELTA-B")) ||
  /V7-ASSISTANT-DELTA-B/.test(snapshots)
const looksReady = /ready/i.test(status)
const explicitRecovery = /recover|restart|reconnect|disconnect|engine|session missing/i.test(status)
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = /\[ready\]/i.test(snapshots) && /Engine interrupted|session is no longer available|Restarting owned engine/i.test(snapshots)
const duplicateAssistantAInFinalState = assistantAConversationCount > 1 || assistantACellCount > 1

const report = [
  "# V7 Assistant Delta Engine Death Gate Report",
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
  `assistantAConversationCount: ${assistantAConversationCount}`,
  `assistantACellCount: ${assistantACellCount}`,
  `lineAVisible: ${lineAVisible}`,
  `lineBPresent: ${lineBPresent}`,
  `looksReady: ${looksReady}`,
  `explicitRecovery: ${explicitRecovery}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateAssistantAInFinalState: ${duplicateAssistantAInFinalState}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine during assistant delta")
if (records.length === 0) failures.push("no state dump records captured")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (assistantAConversationCount < 1 && assistantACellCount < 1) failures.push("expected assistant delta A to remain present in conversation or transcript cells")
if (!lineAVisible) failures.push("expected assistant delta A to remain visible in snapshots")
if (lineBPresent) failures.push("assistant delta B should not appear because engine was killed before second delta")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("snapshot shows [ready] while engine/assistant recovery is unresolved")
if (looksReady && !explicitRecovery) failures.push("final status looks ready without explicit recovery semantics")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (duplicateAssistantAInFinalState) failures.push("assistant delta A duplicated in final state")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][assistant-delta-engine-death] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
