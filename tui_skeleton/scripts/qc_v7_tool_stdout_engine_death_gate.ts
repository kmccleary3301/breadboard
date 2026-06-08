import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) throw new Error("Usage: qc_v7_tool_stdout_engine_death_gate.ts <artifact-dir>")
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
const liveSlots = Array.isArray(state?.liveSlots) ? state.liveSlots : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const toolCellCount = transcriptCells.filter((cell: any) => String(cell?.text ?? cell?.textPreview ?? "").includes("V7_TOOL_STDOUT_LINE_A")).length
const toolEventCount = toolEvents.filter((entry: any) => String(entry?.text ?? "").includes("V7_TOOL_STDOUT_LINE_A")).length
const liveSlotCount = liveSlots.filter((entry: any) => String(entry?.text ?? "").includes("V7_TOOL_STDOUT_LINE_A")).length
const lineAVisible = /V7_TOOL_STDOUT_LINE_A/.test(snapshots)
const lineBPresent = transcriptCells.some((cell: any) => String(cell?.text ?? cell?.textPreview ?? "").includes("V7_TOOL_STDOUT_LINE_B")) ||
  toolEvents.some((entry: any) => String(entry?.text ?? "").includes("V7_TOOL_STDOUT_LINE_B")) ||
  liveSlots.some((entry: any) => String(entry?.text ?? "").includes("V7_TOOL_STDOUT_LINE_B")) ||
  /V7_TOOL_STDOUT_LINE_B/.test(snapshots)
const looksReady = /ready/i.test(status)
const explicitRecovery = /recover|restart|reconnect|disconnect|engine|session missing/i.test(status)
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = /\[ready\]/i.test(snapshots) && /Engine interrupted|session is no longer available|Restarting owned engine/i.test(snapshots)
const duplicateToolLineInFinalCells = toolCellCount > 1 || toolEventCount > 1

const report = [
  "# V7 Tool Stdout Engine Death Gate Report",
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
  `toolCellCount: ${toolCellCount}`,
  `toolEventCount: ${toolEventCount}`,
  `liveSlotCount: ${liveSlotCount}`,
  `lineAVisible: ${lineAVisible}`,
  `lineBPresent: ${lineBPresent}`,
  `looksReady: ${looksReady}`,
  `explicitRecovery: ${explicitRecovery}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateToolLineInFinalCells: ${duplicateToolLineInFinalCells}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine during tool stdout")
if (records.length === 0) failures.push("no state dump records captured")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (toolCellCount + toolEventCount + liveSlotCount < 1) failures.push("expected line A to remain present in transcript, tool events, or live slots")
if (!lineAVisible) failures.push("expected line A to remain visible in snapshots")
if (lineBPresent) failures.push("line B should not appear because engine was killed before second stdout delta")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("snapshot shows [ready] while engine/tool recovery is unresolved")
if (looksReady && !explicitRecovery) failures.push("final status looks ready without explicit recovery semantics")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (duplicateToolLineInFinalCells) failures.push("tool stdout line duplicated in final state")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][tool-stdout-engine-death] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
