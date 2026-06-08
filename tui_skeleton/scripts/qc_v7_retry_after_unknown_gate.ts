import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_retry_after_unknown_gate.ts <artifact-dir> [harness-status]")

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
const joinedConversation = conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
const hints = Array.isArray(finalState?.hints) ? finalState.hints.map((hint: any) => String(hint ?? "")).join("\n") : ""
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const assistantCount = count(joinedConversation, "V7_RETRY_RECOVERED_RESPONSE")
const unknownHint = /Submitted prompt outcome is unknown/i.test(hints) || /Submitted prompt outcome is unknown/i.test(snapshots)
const retryHint = /Resubmitting prompt/i.test(hints) || /Resubmitting prompt/i.test(snapshots)
const recoveredHint = /Owned engine recovered with a new session/i.test(hints) || /Owned engine recovered with a new session/i.test(snapshots)
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  const disconnected = state?.disconnected === true
  const pending = state?.pendingResponse === true
  const recoveryState = disconnected || /Recovery needed|Reconnecting|Restarting/i.test(status)
  return recoveryState && !pending && /\bReady\b/i.test(status)
})
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Retry After Unknown Gate Report",
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
  `unknownHint: ${unknownHint}`,
  `retryHint: ${retryHint}`,
  `recoveredHint: ${recoveredHint}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine during unknown prompt")
if (!recoveryState) failures.push("did not observe recovery-needed unknown state before retry")
if (!pidChanged) failures.push("final pid did not change after engine restart/retry")
if (finalState?.pendingResponse === true) failures.push("retry left pendingResponse true")
if (finalState?.disconnected === true) failures.push("retry left disconnected true")
if (userPromptCount < 2) failures.push(`expected original and retry user prompts, saw ${userPromptCount}`)
if (assistantCount !== 1) failures.push(`expected one recovered assistant response, saw ${assistantCount}`)
if (!unknownHint) failures.push("unknown-outcome hint was not visible/preserved")
if (!retryHint) failures.push("retry command did not report resubmission")
if (!recoveredHint) failures.push("retry did not recover a new owned session before resubmitting")
if (bodyPolluted) failures.push("disconnect body pollution appeared in retry snapshots")
if (readyDuringRecovery) failures.push("snapshot shows ready while recovery/unknown state is visible")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][retry-after-unknown] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
