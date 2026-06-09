import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_owned_engine_idle_recovery_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
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
const finalState = states[states.length - 1] ?? null
const finalLifecycle = finalState?.lifecycle ?? null
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const joinedConversation = conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
const hints = Array.isArray(finalState?.hints) ? finalState.hints.map((hint: any) => String(hint ?? "")).join("\n") : ""
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const assistantCount = joinedConversation.split("OWNED_INPUT_RECOVERED_RESPONSE").length - 1
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const recoveredHint = /Owned engine recovered with a new idle session|Owned engine recovered with a new session/i.test(hints) || /Owned engine recovered with a new idle session|Owned engine recovered with a new session/i.test(snapshots)
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Owned Engine Idle Recovery Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `killedPid: ${Number.isFinite(killedPid) ? killedPid : "none"}`,
  `finalPid: ${String(finalLifecycle?.pid ?? "none")}`,
  `pidChanged: ${pidChanged}`,
  `finalStatus: ${String(finalState?.status ?? "none")}`,
  `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(finalState?.disconnected ?? "none")}`,
  `userPromptCount: ${userPromptCount}`,
  `assistantCount: ${assistantCount}`,
  `recoveredHint: ${recoveredHint}`,
  `bodyPolluted: ${bodyPolluted}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine before prompt")
if (!pidChanged) failures.push("final pid did not change after owned engine restart")
if (finalState?.pendingResponse === true) failures.push("final state left pendingResponse true")
if (finalState?.disconnected === true) failures.push("final state left disconnected true")
if (userPromptCount !== 1) failures.push(`expected one user prompt, saw ${userPromptCount}`)
if (assistantCount !== 1) failures.push(`expected one recovered assistant response, saw ${assistantCount}`)
if (!recoveredHint) failures.push("recovered owned-engine hint was not visible/preserved")
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][owned-engine-idle-recovery] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
