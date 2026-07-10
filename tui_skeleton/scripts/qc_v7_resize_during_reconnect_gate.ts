import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_resize_during_reconnect_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const count = (value: string, pattern: RegExp): number => value.match(pattern)?.length ?? 0

const prompt = read("prompt.txt").trim()
const stateRaw = read("state.ndjson")
const killLog = read("kill.log")
const snapshots = read("snapshots.txt")
const harnessOutput = read("harness_output.txt")
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
const liveSlots = Array.isArray(state?.liveSlots) ? state.liveSlots : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const lineAVisible = snapshots.includes("V7_TOOL_STDOUT_LINE_A")
const lineBPresent = snapshots.includes("V7_TOOL_STDOUT_LINE_B")
const finalLiveLineCount = count(liveSlots.map((slot: any) => String(slot?.text ?? "")).join("\n"), /V7_TOOL_STDOUT_LINE_A/g)
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const explicitRecovery = /recover|restart|reconnect|disconnect|engine|session missing/i.test(status)
const looksReady = /ready/i.test(status)
const disconnected = state?.disconnected === true
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = /\[ready\]/i.test(snapshots) && /Engine interrupted|session is no longer available|Restarting owned engine/i.test(snapshots)
const brokenHorizontalRule = /[─━═_]{18,}/.test(snapshots)
const duplicatePromptInSnapshot = prompt
  ? snapshots
      .split(/^# /m)
      .filter(Boolean)
      .some((section) => (section.match(new RegExp(prompt.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"))?.length ?? 0) > 1)
  : false
const duplicateLineAInSection = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((section) => count(section, /V7_TOOL_STDOUT_LINE_A/g) > 1)
const resizeSnapshots = count(snapshots, /^# resize-reconnect-/gm)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Resize During Reconnect Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `resizeSnapshots: ${resizeSnapshots}`,
  `killedPid: ${Number.isFinite(killedPid) ? killedPid : "none"}`,
  `lastStatus: ${status || "none"}`,
  `lastLifecycleMode: ${lifecycle?.mode ?? "none"}`,
  `lastLifecycleOwned: ${String(lifecycle?.owned ?? "none")}`,
  `lastLifecyclePid: ${String(lifecycle?.pid ?? "none")}`,
  `pidChanged: ${pidChanged}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `disconnected: ${String(disconnected)}`,
  `userPromptCount: ${userPromptCount}`,
  `userCellCount: ${userCellCount}`,
  `lineAVisible: ${lineAVisible}`,
  `lineBPresent: ${lineBPresent}`,
  `finalLiveLineCount: ${finalLiveLineCount}`,
  `looksReady: ${looksReady}`,
  `explicitRecovery: ${explicitRecovery}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `brokenHorizontalRule: ${brokenHorizontalRule}`,
  `duplicatePromptInSnapshot: ${duplicatePromptInSnapshot}`,
  `duplicateLineAInSection: ${duplicateLineAInSection}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine during visible tool stdout")
if (records.length === 0) failures.push("no state dump records captured")
if (resizeSnapshots < 4) failures.push(`expected at least four resize snapshots, saw ${resizeSnapshots}`)
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (!lineAVisible) failures.push("partial stdout line A was not visible across resize/reconnect snapshots")
if (lineBPresent) failures.push("line B leaked after engine kill")
if (finalLiveLineCount > 1) failures.push(`partial stdout line A duplicated in final live state ${finalLiveLineCount} times`)
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")
if (looksReady && !explicitRecovery) failures.push("final status looks ready without recovery semantics")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("snapshot shows [ready] while recovery is unresolved")
if (brokenHorizontalRule) failures.push("snapshot contains a long broken horizontal rule/border artifact")
if (duplicatePromptInSnapshot) failures.push("prompt appears duplicated in a visible snapshot section")
if (duplicateLineAInSection) failures.push("partial stdout line A appears duplicated in a visible snapshot section")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][resize-during-reconnect] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
