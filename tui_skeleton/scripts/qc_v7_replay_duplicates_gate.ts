import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_replay_duplicates_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const count = (value: string, needle: string): number => value.split(needle).length - 1
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

const lastRecord = records[records.length - 1] ?? null
const state = lastRecord?.state ?? null
const conversation = Array.isArray(state?.conversation) ? state.conversation : []
const transcriptCells = Array.isArray(state?.transcriptCells)
  ? state.transcriptCells
  : Array.isArray(lastRecord?.transcriptCells)
    ? lastRecord.transcriptCells
    : []
const rawEvents = Array.isArray(state?.rawEvents) ? state.rawEvents : []
const toolEvents = Array.isArray(state?.toolEvents) ? state.toolEvents : []
const liveSlots = Array.isArray(state?.liveSlots) ? state.liveSlots : []
const joinedConversation = conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
const joinedTranscript = transcriptCells.map((cell: any) => String(cell?.text ?? cell?.textPreview ?? "")).join("\n")
const joinedTools = toolEvents.map((entry: any) => String(entry?.text ?? "")).join("\n")
const joinedLive = liveSlots.map((entry: any) => String(entry?.text ?? "")).join("\n")
const rawIds = rawEvents.map((entry: any) => String(entry?.id ?? entry?.text ?? ""))

const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const assistantConversationCount = count(joinedConversation, "V7_REPLAY_ASSISTANT_CHUNK")
const assistantTranscriptCount = count(joinedTranscript, "V7_REPLAY_ASSISTANT_CHUNK")
const stdoutStateCount = count(joinedTools + "\n" + joinedLive, "V7_REPLAY_STDOUT_LINE")
const stdoutSnapshotSectionsDuplicated = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((section) => count(section, "V7_REPLAY_STDOUT_LINE") > 1)
const resultStateCount = count(joinedTools, "V7_REPLAY_RESULT_LINE")
const resultSnapshotSectionsDuplicated = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((section) => count(section, "V7_REPLAY_RESULT_LINE") > 1)
const duplicateRawIds = rawIds.length - new Set(rawIds).size
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)
const completionReached = state?.completionReached === true || state?.completionSeen === true

const report = [
  "# V7 Replay Duplicates PTY Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `lastStatus: ${String(state?.status ?? "none")}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `completionReached: ${completionReached}`,
  `conversationCount: ${conversation.length}`,
  `transcriptCellCount: ${transcriptCells.length}`,
  `toolEventCount: ${toolEvents.length}`,
  `liveSlotCount: ${liveSlots.length}`,
  `rawEventCount: ${rawEvents.length}`,
  `duplicateRawIds: ${duplicateRawIds}`,
  `userPromptCount: ${userPromptCount}`,
  `userCellCount: ${userCellCount}`,
  `assistantConversationCount: ${assistantConversationCount}`,
  `assistantTranscriptCount: ${assistantTranscriptCount}`,
  `stdoutStateCount: ${stdoutStateCount}`,
  `stdoutSnapshotSectionsDuplicated: ${stdoutSnapshotSectionsDuplicated}`,
  `resultStateCount: ${resultStateCount}`,
  `resultSnapshotSectionsDuplicated: ${resultSnapshotSectionsDuplicated}`,
  `bodyPolluted: ${bodyPolluted}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (records.length === 0) failures.push("no state dump records captured")
if (!completionReached) failures.push("completion was not reached")
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (assistantConversationCount !== 1) failures.push(`expected assistant replay chunk once in conversation, saw ${assistantConversationCount}`)
if (assistantTranscriptCount !== 1) failures.push(`expected assistant replay chunk once in transcript, saw ${assistantTranscriptCount}`)
if (stdoutStateCount !== 1) failures.push(`expected stdout replay line once in state, saw ${stdoutStateCount}`)
if (stdoutSnapshotSectionsDuplicated) failures.push("stdout replay line duplicated in a visible snapshot section")
if (resultStateCount !== 1) failures.push(`expected result replay line once in tool state, saw ${resultStateCount}`)
if (resultSnapshotSectionsDuplicated) failures.push("result replay line duplicated in a visible snapshot section")
if (duplicateRawIds !== 0) failures.push(`raw events contain duplicate ids (${duplicateRawIds})`)
if (bodyPolluted) failures.push("disconnect body pollution appeared in replay duplicate snapshots")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][replay-duplicates] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
