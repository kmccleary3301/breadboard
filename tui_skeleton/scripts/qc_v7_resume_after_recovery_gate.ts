import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_resume_after_recovery_gate.ts <artifact-dir> [harness-status]")
const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const countInSections = (value: string, needle: string): boolean => value
  .split(/^# /m)
  .filter(Boolean)
  .some((section) => section.split(needle).length - 1 > 1)
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
const states = records.map((record) => record?.state).filter(Boolean)
const killedPid = Number(killLog.match(/killed_pid=(\d+)/)?.[1] ?? NaN)
const recoveryState = states.find((state) => state?.disconnected === true && /Recovery needed/i.test(String(state?.status ?? "")))
const finalState = states[states.length - 1] ?? null
const finalLifecycle = finalState?.lifecycle ?? null
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const finalTools = Array.isArray(finalState?.toolEvents) ? finalState.toolEvents : []
const resumeTools = finalTools.filter((tool: any) => tool?.kind === "status" && /resume=recovered-new-session/.test(String(tool?.text ?? "")))
const hints = Array.isArray(finalState?.hints) ? finalState.hints.map((hint: any) => String(hint ?? "")).join("\n") : ""
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const assistantCount = conversation.filter((entry: any) => entry?.speaker === "assistant").length
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const recoveredStatus = /Recovered/i.test(String(finalState?.status ?? ""))
const resumeHint = /Resume recovered a new owned-engine session/i.test(hints) || /resume=recovered-new-session/.test(snapshots)
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  const recovery = state?.disconnected === true || /Recovery needed|Reconnecting|Restarting/i.test(status)
  return recovery && /\bReady\b/i.test(status)
})
const duplicateResumeRows = countInSections(snapshots, "resume=recovered-new-session")
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Resume After Recovery Gate Report",
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
  `resumeToolCount: ${resumeTools.length}`,
  `recoveredStatus: ${recoveredStatus}`,
  `resumeHint: ${resumeHint}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateResumeRows: ${duplicateResumeRows}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine during recovery setup")
if (!recoveryState) failures.push("did not observe recovery-needed state before /resume")
if (!pidChanged) failures.push("final pid did not reflect owned engine restart after kill")
if (finalState?.pendingResponse === true) failures.push("/resume left pendingResponse true")
if (finalState?.disconnected === true) failures.push("/resume left disconnected true")
if (userPromptCount !== 1) failures.push(`expected one preserved user prompt, saw ${userPromptCount}`)
if (assistantCount !== 0) failures.push(`expected no assistant response from resume-only recovery, saw ${assistantCount}`)
if (resumeTools.length !== 1) failures.push(`expected exactly one resume=recovered-new-session status tool, saw ${resumeTools.length}`)
if (!recoveredStatus) failures.push("final status does not indicate recovery")
if (!resumeHint) failures.push("missing resume recovered hint/next action")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("state shows Ready during unresolved recovery state")
if (duplicateResumeRows) failures.push("resume=recovered-new-session duplicated in visible snapshot")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][resume-after-recovery] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
