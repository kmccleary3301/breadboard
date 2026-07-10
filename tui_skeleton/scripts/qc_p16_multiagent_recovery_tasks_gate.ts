import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_p16_multiagent_recovery_tasks_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const section = (snapshots: string, label: string): string => {
  const parts = snapshots.split(/^# /m).filter(Boolean)
  return parts.find((part) => part.startsWith(label)) ?? ""
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
const states = records.map((record) => record?.state).filter(Boolean)
const killedPid = Number(killLog.match(/killed_pid=(\d+)/)?.[1] ?? NaN)
const recoveryStates = states.filter((state) => state?.disconnected === true && /Recovery needed/i.test(String(state?.status ?? "")))
const finalState = states[states.length - 1] ?? null
const finalLifecycle = finalState?.lifecycle ?? null
const allTasks = states.flatMap((state) => Array.isArray(state?.tasks) ? state.tasks : [])
const finalTasks = Array.isArray(finalState?.tasks) ? finalState.tasks : []
const allTools = states.flatMap((state) => Array.isArray(state?.toolEvents) ? state.toolEvents : [])
const recoveryTaskTools = allTools.filter((tool: any) =>
  tool?.kind === "status" && /\[recovery tasks\]/.test(String(tool?.text ?? "")),
)
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const assistantCount = conversation.filter((entry: any) => entry?.speaker === "assistant").length
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const commandTimedOut = /Watchdog timeout|Harness failed|Timed out waiting/i.test(harnessOutput)
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  const recovery = state?.disconnected === true || /Recovery needed|Reconnecting|Restarting/i.test(status)
  return recovery && /\bReady\b/i.test(status)
})
const duplicateRecoveryTaskRows = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((snap) => count(snap, "[recovery tasks]") > 1 || count(snap, "state=preserved-local") > 1)
const recoveryCopy = section(snapshots, "multiagent-recovery-copy")
const taskboard = section(snapshots, "multiagent-recovery-taskboard")
const taskboardCompact = section(snapshots, "multiagent-recovery-taskboard-compact")
const hasTaskSummaryCounts = /tasks=3 running=1 blocked=1 failed=0 completed=1 stopped=0/.test(snapshots)
const hasPreservedLocal = /state=preserved-local/.test(snapshots)
const hasTaskIds = /recovery-reviewer-01/.test(snapshots) && /recovery-implementer-01/.test(snapshots) && /recovery-tester-01/.test(snapshots)
const hasArtifactRef = /\.breadboard\/subagents\/recovery-implementer-01\.jsonl/.test(snapshots)
const taskboardVisible = /Background tasks/.test(taskboard) && /Implement recovery task state/.test(taskboard) && /Test recovery task state/.test(taskboard)
const compactTaskboardVisible = /Background tasks/.test(taskboardCompact) && /recovery-implementer-01/.test(taskboardCompact)
const lineBLeaked = /TASK_RECOVERY_STDOUT_B/.test(snapshots)
const taskCountEver = new Set(allTasks.map((task: any) => String(task?.id ?? "")).filter(Boolean)).size
const finalTaskCount = new Set(finalTasks.map((task: any) => String(task?.id ?? "")).filter(Boolean)).size

const report = [
  "# P16 Multiagent Recovery Tasks Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `killedPid: ${Number.isFinite(killedPid) ? killedPid : "none"}`,
  `finalPid: ${String(finalLifecycle?.pid ?? "none")}`,
  `pidChanged: ${pidChanged}`,
  `recoveryStates: ${recoveryStates.length}`,
  `finalStatus: ${String(finalState?.status ?? "none")}`,
  `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(finalState?.disconnected ?? "none")}`,
  `userPromptCount: ${userPromptCount}`,
  `assistantCount: ${assistantCount}`,
  `taskCountEver: ${taskCountEver}`,
  `finalTaskCount: ${finalTaskCount}`,
  `recoveryTaskToolCount: ${recoveryTaskTools.length}`,
  `hasTaskSummaryCounts: ${hasTaskSummaryCounts}`,
  `hasPreservedLocal: ${hasPreservedLocal}`,
  `hasTaskIds: ${hasTaskIds}`,
  `hasArtifactRef: ${hasArtifactRef}`,
  `taskboardVisible: ${taskboardVisible}`,
  `compactTaskboardVisible: ${compactTaskboardVisible}`,
  `lineBLeaked: ${lineBLeaked}`,
  `bodyPolluted: ${bodyPolluted}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `duplicateRecoveryTaskRows: ${duplicateRecoveryTaskRows}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine after task events and stdout")
if (!pidChanged) failures.push("final pid did not reflect owned engine restart after kill")
if (recoveryStates.length === 0) failures.push("did not observe recovery-needed state")
if (finalState?.pendingResponse === true) failures.push("recovery left pendingResponse true")
if (finalState?.disconnected !== true) failures.push("expected final state to remain disconnected/recovery-needed for this gate")
if (userPromptCount !== 1) failures.push(`expected one preserved user prompt, saw ${userPromptCount}`)
if (assistantCount !== 1) failures.push(`expected one pre-recovery assistant message, saw ${assistantCount}`)
if (taskCountEver < 3) failures.push(`expected at least three task ids over state history, saw ${taskCountEver}`)
if (finalTaskCount < 3) failures.push(`expected final state to preserve three task ids, saw ${finalTaskCount}`)
if (recoveryTaskTools.length !== 1) failures.push(`expected exactly one recovery task summary status tool, saw ${recoveryTaskTools.length}`)
if (!hasTaskSummaryCounts) failures.push("missing exact recovery task count summary")
if (!hasPreservedLocal) failures.push("missing state=preserved-local recovery task copy")
if (!hasTaskIds) failures.push("missing recovery task ids in snapshots")
if (!hasArtifactRef) failures.push("missing preserved artifact ref in snapshots")
if (!taskboardVisible) failures.push("taskboard did not open with preserved tasks during recovery")
if (!compactTaskboardVisible) failures.push("compact taskboard did not preserve task identity during recovery")
if (lineBLeaked) failures.push("post-kill stdout B leaked into snapshots")
if (bodyPolluted) failures.push("generic disconnect body pollution appeared in snapshots")
if (readyDuringRecovery) failures.push("state shows Ready during unresolved recovery")
if (duplicateRecoveryTaskRows) failures.push("recovery task summary duplicated within a visible snapshot")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[p16][multiagent-recovery-tasks] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
