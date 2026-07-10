import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_event_zero_replay_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const count = (value: string, needle: string): number => value.split(needle).length - 1
const countSectionsWithMoreThanOne = (snapshots: string, needle: string): number =>
  snapshots
    .split(/^# /m)
    .filter(Boolean)
    .filter((section) => count(section, needle) > 1).length

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
const hints = Array.isArray(state?.hints) ? state.hints.map((hint: any) => String(hint ?? "")) : []

const joinedConversation = conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
const joinedTranscript = transcriptCells.map((cell: any) => String(cell?.text ?? cell?.textPreview ?? "")).join("\n")
const joinedTools = toolEvents.map((entry: any) => String(entry?.text ?? "")).join("\n")
const joinedLive = liveSlots.map((entry: any) => String(entry?.text ?? "")).join("\n")
const rawIds = rawEvents.map((entry: any) => String(entry?.id ?? entry?.text ?? ""))

const userPromptCount = conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(prompt)).length
const userCellCount = transcriptCells.filter((cell: any) => cell?.speaker === "user" && String(cell?.text ?? cell?.textPreview ?? "").includes(prompt)).length
const assistantConversationCount = count(joinedConversation, "V7_EVENT_ZERO_ASSISTANT_CHUNK")
const assistantTranscriptCount = count(joinedTranscript, "V7_EVENT_ZERO_ASSISTANT_CHUNK")
const stdoutStateCount = count(joinedTools + "\n" + joinedLive, "V7_EVENT_ZERO_STDOUT_LINE")
const resultStateCount = count(joinedTools, "V7_EVENT_ZERO_RESULT_LINE")
const duplicateRawIds = rawIds.length - new Set(rawIds).size
const completionHintCount = hints.filter((hint: string) => /Cooked|Completed/i.test(hint)).length
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)
const completionReached = state?.completionReached === true || state?.completionSeen === true
const reconnectingAfterCompletion = completionReached && /reconnect|restart|recovery|disconnect/i.test(String(state?.status ?? ""))
const assistantSnapshotSectionsDuplicated = countSectionsWithMoreThanOne(snapshots, "V7_EVENT_ZERO_ASSISTANT_CHUNK")
const stdoutSnapshotSectionsDuplicated = countSectionsWithMoreThanOne(snapshots, "V7_EVENT_ZERO_STDOUT_LINE")
const resultSnapshotSectionsDuplicated = countSectionsWithMoreThanOne(snapshots, "V7_EVENT_ZERO_RESULT_LINE")

const report = [
  "# V7 Event-Zero Replay PTY Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `lastStatus: ${String(state?.status ?? "none")}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `completionReached: ${completionReached}`,
  `reconnectingAfterCompletion: ${reconnectingAfterCompletion}`,
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
  `resultStateCount: ${resultStateCount}`,
  `completionHintCount: ${completionHintCount}`,
  `assistantSnapshotSectionsDuplicated: ${assistantSnapshotSectionsDuplicated}`,
  `stdoutSnapshotSectionsDuplicated: ${stdoutSnapshotSectionsDuplicated}`,
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
if (reconnectingAfterCompletion) failures.push(`final status shows recovery after completion: ${String(state?.status ?? "none")}`)
if (userPromptCount !== 1) failures.push(`expected exactly one user prompt in conversation, saw ${userPromptCount}`)
if (userCellCount !== 1) failures.push(`expected exactly one user prompt transcript cell, saw ${userCellCount}`)
if (assistantConversationCount !== 1) failures.push(`expected assistant event-zero chunk once in conversation, saw ${assistantConversationCount}`)
if (assistantTranscriptCount !== 1) failures.push(`expected assistant event-zero chunk once in transcript, saw ${assistantTranscriptCount}`)
if (stdoutStateCount !== 1) failures.push(`expected stdout event-zero line once in state, saw ${stdoutStateCount}`)
if (resultStateCount !== 1) failures.push(`expected result event-zero line once in tool state, saw ${resultStateCount}`)
if (duplicateRawIds !== 0) failures.push(`raw events contain duplicate ids (${duplicateRawIds})`)
if (completionHintCount > 1) failures.push(`completion hint duplicated (${completionHintCount})`)
if (assistantSnapshotSectionsDuplicated > 0) failures.push("assistant event-zero chunk duplicated in a visible snapshot section")
if (stdoutSnapshotSectionsDuplicated > 0) failures.push("stdout event-zero line duplicated in a visible snapshot section")
if (resultSnapshotSectionsDuplicated > 0) failures.push("result event-zero line duplicated in a visible snapshot section")
if (bodyPolluted) failures.push("disconnect body pollution appeared in event-zero replay snapshots")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][event-zero-replay] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
