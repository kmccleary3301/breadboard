import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) throw new Error("Usage: qc_v7_active_after_user_echo_engine_death_gate.ts <artifact-dir>")
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
const hints = Array.isArray(state?.hints) ? state.hints.map((hint: any) => String(hint ?? "")) : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const transcriptCells = Array.isArray(state?.transcriptCells)
  ? state.transcriptCells
  : Array.isArray(lastRecord?.transcriptCells)
    ? lastRecord.transcriptCells
    : []
const userCellCount = transcriptCells.filter((cell: any) => {
  const text = String(cell?.text ?? cell?.textPreview ?? "")
  return cell?.speaker === "user" && text.includes(prompt)
}).length
const looksReady = /ready/i.test(status)
const completedRecovery = state?.pendingResponse === false && state?.disconnected === false && /finish|complete/i.test(status)
const explicitRecovery =
  /recover|restart|reconnect|disconnect|engine|session missing/i.test(status) ||
  hints.some((hint: string) => /recover|restart|reconnect|engine|session/i.test(hint))
const pidChanged = Number.isFinite(killedPid) && typeof lifecycle?.pid === "number" && lifecycle.pid !== killedPid
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyWhileUnresolved =
  state?.pendingResponse === true &&
  /\[ready\]/i.test(snapshots) &&
  /Engine interrupted|Failed to send input|session is no longer available/i.test(snapshots)
const restartAttemptVisible =
  /BreadBoard engine interrupted\. Restarting \(\d+(?:\/\d+)?\)|Restarting owned engine \(attempt \d+(?:\/\d+)?\)/i.test(snapshots) ||
  hints.some((hint: string) => /Restarting owned engine \(attempt \d+(?:\/\d+)?\)/i.test(hint))
const escapedPrompt = prompt.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
const duplicatePromptInSnapshot = prompt
  ? snapshots
      .split(/^# /m)
      .filter(Boolean)
      .some((section) => (section.match(new RegExp(escapedPrompt, "g"))?.length ?? 0) > 1)
  : false
const unknownOutcomeHint = hints.some((hint: string) => /Submitted prompt outcome is unknown/i.test(hint))

const report = [
  "# V7 Active After User Echo Engine Death Gate Report",
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
  `looksReady: ${looksReady}`,
  `completedRecovery: ${completedRecovery}`,
  `explicitRecovery: ${explicitRecovery}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyWhileUnresolved: ${readyWhileUnresolved}`,
  `duplicatePromptInSnapshot: ${duplicatePromptInSnapshot}`,
  `unknownOutcomeHint: ${unknownOutcomeHint}`,
  `restartAttemptVisible: ${restartAttemptVisible}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!Number.isFinite(killedPid)) failures.push("did not observe and kill engine after pending user echo")
if (records.length === 0) failures.push("no state dump records captured")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (bodyPolluted) failures.push("disconnect body pollution appeared in snapshots")
if (readyWhileUnresolved) failures.push("snapshot shows [ready] while engine/input recovery is unresolved")
if (duplicatePromptInSnapshot) failures.push("prompt appears duplicated in visible snapshots")
if (!completedRecovery && !unknownOutcomeHint) failures.push("missing accepted-prompt unknown-outcome recovery hint")
if (!completedRecovery && looksReady && !explicitRecovery) failures.push("final status looks ready without explicit recovery semantics")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")
if (!pidChanged) failures.push("final lifecycle pid did not change from killed pid")
if (!restartAttemptVisible) failures.push("engine restart attempt count was not visible in status or hints")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][active-after-user-echo] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
