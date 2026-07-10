import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_stream_stall_policy_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}

const prompt = read("prompt.txt").trim()
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
const finalState = states.at(-1) ?? null
const transcriptCells = Array.isArray(finalState?.transcriptCells) ? finalState.transcriptCells : []
const userPromptCount = transcriptCells.filter((entry: any) =>
  (entry?.speaker === "user" || entry?.role === "user-request") &&
  String(entry?.textPreview ?? entry?.text ?? "").includes(prompt.slice(0, 28))
).length
const assistantCount = transcriptCells.filter((entry: any) => entry?.speaker === "assistant" || entry?.role === "assistant-message").length
const toolCount = Number(finalState?.counts?.toolEvents ?? 0)
const hintCount = Number(finalState?.counts?.hints ?? 0)
const reconnectStates = states.filter((state) => /Reconnecting/i.test(String(state?.status ?? ""))).length
const finalStatus = String(finalState?.status ?? "")
const bodyText = snapshots
const lateEventVisible = /V7_STREAM_STALL_LATE_EVENT_SHOULD_NOT_APPEAR/.test([snapshots, stateRaw].join("\n"))
const bodyPolluted = /Lost connection to the engine|Disconnected:/.test(bodyText)
const retryLineCount = (bodyText.match(/Stream interruption|Stream stalled/g) ?? []).length
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  return (state?.disconnected === true || /Reconnecting|Disconnected|stalled/i.test(status)) && /\bReady\b/i.test(status)
})

const report = [
  "# V7 Stream Stall Policy Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `finalStatus: ${finalStatus || "none"}`,
  `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(finalState?.disconnected ?? "none")}`,
  `userPromptCount: ${userPromptCount}`,
  `assistantCount: ${assistantCount}`,
  `toolCount: ${toolCount}`,
  `hintCount: ${hintCount}`,
  `reconnectStates: ${reconnectStates}`,
  `lateEventVisible: ${lateEventVisible}`,
  `bodyPolluted: ${bodyPolluted}`,
  `retryLineCount: ${retryLineCount}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `harnessOutputBytes: ${harnessOutput.length}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (records.length === 0) failures.push("no state records captured")
if (finalState?.disconnected !== true) failures.push("final state is not disconnected after exhausted stall retries")
if (!/Disconnected/.test(finalStatus)) failures.push(`final status is not explicit Disconnected: ${finalStatus}`)
if (finalState?.pendingResponse !== false) failures.push("final pendingResponse is not false")
if (userPromptCount !== 1) failures.push(`expected one user prompt, saw ${userPromptCount}`)
if (assistantCount !== 0) failures.push(`expected zero assistant transcript cells, saw ${assistantCount}`)
if (toolCount !== 0) failures.push(`expected zero tool events, saw ${toolCount}`)
if (hintCount > 6) failures.push(`hint count exceeded bounded policy: ${hintCount}`)
if (reconnectStates < 1) failures.push("did not observe reconnecting states before disconnect")
if (lateEventVisible) failures.push("late stalled event became visible")
if (bodyPolluted) failures.push("disconnect body pollution visible")
if (retryLineCount > 6) failures.push(`retry/stall visible body spam count too high: ${retryLineCount}`)
if (readyDuringRecovery) failures.push("ready status visible during recovery/disconnect")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][stream-stall-policy] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
