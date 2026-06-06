import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_stop_during_recovery_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const count = (value: string, needle: string): number => value.split(needle).length - 1
const prompt = read("prompt.txt").trim()
const killLog = read("kill.log")
const stateRaw = read("state.ndjson")
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
const states = records.map((record) => record?.state).filter(Boolean)
const recoveryState = states.find((state) => state?.disconnected === true && /Recovery needed/i.test(String(state?.status ?? "")))
const finalState = states[states.length - 1] ?? null
const finalLifecycle = finalState?.lifecycle ?? null
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const allTools = states.flatMap((state) => Array.isArray(state?.toolEvents) ? state.toolEvents : [])
const stopTools = allTools.filter((tool: any) => tool?.kind === "status" && /recovery=stopped/.test(String(tool?.text ?? "")))
const hints = Array.isArray(finalState?.hints) ? finalState.hints.map((hint: any) => String(hint ?? "")).join("\n") : ""
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const assistantCount = conversation.filter((entry: any) => entry?.speaker === "assistant").length
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const stoppedStatus = /Recovery stopped/i.test(String(finalState?.status ?? ""))
const stopHint = /Recovery stopped/i.test(hints) || /use \/retry to resubmit/i.test(hints) || /Recovery stopped/i.test(snapshots)
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  const recovery = state?.disconnected === true || /Recovery needed|Recovery stopped|Reconnecting|Restarting/i.test(status)
  return recovery && /\bReady\b/i.test(status)
})
const duplicateStopRows = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((section) => count(section, "recovery=stopped") > 1)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Stop During Recovery Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `killedPid: ${Number.isFinite(killedPid) ? killedPid : "none"}`,
  `finalPid: ${String(finalLifecycle?.pid ?? "none")}`,
  `pidChanged: ${pidChanged}`,
  `sawRecoveryState: ${Boolean(recoveryState)}`,
  `finalStatus: ${String(finalState?.status ?? "none")}`,
  `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(finalState?.disconnected ?? "none")}`,
  `userPromptCount: ${userPromptCount}`,
  `assistantCount: ${assistantCount}`,
  `stopToolCount: ${stopTools.length}`,
  `stoppedStatus: ${stoppedStatus}`,
  `stopHint: ${stopHint}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateStopRows: ${duplicateStopRows}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine during recovery setup")
if (!recoveryState) failures.push("did not observe recovery-needed state before /stop")
if (!pidChanged) failures.push("final pid did not reflect owned engine restart after kill")
if (finalState?.pendingResponse === true) failures.push("/stop left pendingResponse true")
if (finalState?.disconnected !== true) failures.push("/stop during recovery should remain disconnected/recovery-needed")
if (userPromptCount !== 1) failures.push(`expected one preserved user prompt, saw ${userPromptCount}`)
if (assistantCount !== 0) failures.push(`expected no assistant response after stop, saw ${assistantCount}`)
if (stopTools.length !== 1) failures.push(`expected exactly one recovery=stopped status tool, saw ${stopTools.length}`)
if (!stoppedStatus) failures.push("final status does not say Recovery stopped")
if (!stopHint) failures.push("missing recovery stopped hint/next action")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("state shows Ready during recovery/stopped state")
if (duplicateStopRows) failures.push("recovery=stopped duplicated in visible snapshot")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][stop-during-recovery] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
