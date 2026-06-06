import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_live_multiturn_endurance_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}

const stateRaw = read("state.ndjson")
const snapshots = read("snapshots.txt")
const harnessOutput = read("harness_output.txt")
const records = stateRaw
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => {
    try {
      return JSON.parse(line) as any
    } catch {
      return null
    }
  })
  .filter(Boolean)
const states = records.map((record) => record?.state).filter(Boolean)
const finalState = states.at(-1) ?? null
const transcriptCells = Array.isArray(finalState?.transcriptCells) ? finalState.transcriptCells : []
const textCorpus = [stateRaw, snapshots].join("\n")
const markerCount = (marker: string) => (textCorpus.match(new RegExp(marker, "g")) ?? []).length
const assistantMarkersPresent = Array.from({ length: 10 }, (_, index) => `V7_ENDURANCE_TURN_${index + 1}`).filter(
  (marker) => markerCount(marker) > 0,
).length
const userPromptCells = transcriptCells.filter((cell: any) => cell?.role === "user-request" || cell?.speaker === "user")
const assistantCells = transcriptCells.filter((cell: any) => cell?.role === "assistant-message" || cell?.speaker === "assistant")
const toolCount = Number(finalState?.counts?.toolEvents ?? 0)
const lifecycle = finalState?.lifecycle ?? null
const snapshotLabels = (snapshots.match(/^# .+$/gm) ?? []).map((line) => line.replace(/^# /, ""))
const hasTranscriptViewer = /transcript-viewer-after-turn5/.test(snapshots) && /transcript/i.test(snapshots)
const hasModelPicker = /model-picker-after-turn10/.test(snapshots) && /model/i.test(snapshots)
const hasResizeEvidence = /turn2-final-resized-narrow|turn3-final-resized-wide|turn8-final-resized/.test(snapshots)
const bodyPolluted = /Lost connection to the engine|Disconnected:|Log link available|file:\/\/logging/.test(snapshots)
const rawEscapeVisible = /\^\[|\u001b/.test(snapshots)
const readyLie = states.some((state) => {
  const status = String(state?.status ?? "")
  return state?.pendingResponse === true && /\[?ready\]?/i.test(status)
})
const duplicateUserTextInFinal = (() => {
  const seen = new Set<string>()
  for (const cell of userPromptCells) {
    const text = String(cell?.textPreview ?? cell?.text ?? "").trim()
    if (!text) continue
    if (seen.has(text)) return true
    seen.add(text)
  }
  return false
})()
const markerFileSeen = /V7_ENDURANCE_MARKER/.test(textCorpus)
const commandTimedOut = /Harness failed|Timed out|Watchdog timeout/i.test(harnessOutput)

const report = [
  "# V7 Live Multiturn Endurance Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `finalStatus: ${String(finalState?.status ?? "none")}`,
  `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(finalState?.disconnected ?? "none")}`,
  `lifecycleMode: ${String(lifecycle?.mode ?? "none")}`,
  `lifecycleOwned: ${String(lifecycle?.owned ?? "none")}`,
  `lifecyclePid: ${String(lifecycle?.pid ?? "none")}`,
  `userPromptCells: ${userPromptCells.length}`,
  `assistantCells: ${assistantCells.length}`,
  `assistantMarkersPresent: ${assistantMarkersPresent}`,
  `toolCount: ${toolCount}`,
  `snapshotCount: ${snapshotLabels.length}`,
  `hasTranscriptViewer: ${hasTranscriptViewer}`,
  `hasModelPicker: ${hasModelPicker}`,
  `hasResizeEvidence: ${hasResizeEvidence}`,
  `markerFileSeen: ${markerFileSeen}`,
  `bodyPolluted: ${bodyPolluted}`,
  `rawEscapeVisible: ${rawEscapeVisible}`,
  `readyLie: ${readyLie}`,
  `duplicateUserTextInFinal: ${duplicateUserTextInFinal}`,
  `commandTimedOut: ${commandTimedOut}`,
  `harnessOutputBytes: ${harnessOutput.length}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (records.length < 10) failures.push(`expected state records, saw ${records.length}`)
if (finalState?.pendingResponse !== false) failures.push("final pendingResponse is not false")
if (finalState?.disconnected === true) failures.push("final state is disconnected")
if (lifecycle?.mode !== "local-owned") failures.push(`expected local-owned lifecycle, saw ${String(lifecycle?.mode ?? "none")}`)
if (lifecycle?.owned !== true) failures.push("expected owned lifecycle")
if (userPromptCells.length < 10) failures.push(`expected at least 10 user prompt cells, saw ${userPromptCells.length}`)
if (assistantCells.length < 10) failures.push(`expected at least 10 assistant cells, saw ${assistantCells.length}`)
if (assistantMarkersPresent < 8) failures.push(`expected most turn markers visible, saw ${assistantMarkersPresent}`)
if (toolCount < 3) failures.push(`expected at least 3 tool events, saw ${toolCount}`)
if (snapshotLabels.length < 10) failures.push(`expected at least 10 snapshots, saw ${snapshotLabels.length}`)
if (!hasTranscriptViewer) failures.push("transcript viewer evidence missing")
if (!hasModelPicker) failures.push("model picker evidence missing")
if (!hasResizeEvidence) failures.push("resize evidence missing")
if (!markerFileSeen) failures.push("created marker file was not observed")
if (bodyPolluted) failures.push("transcript/body pollution visible")
if (rawEscapeVisible) failures.push("raw escape text visible")
if (readyLie) failures.push("ready status while pending")
if (duplicateUserTextInFinal) failures.push("duplicate user prompt text in final transcript cells")
if (commandTimedOut) failures.push("harness timeout/failure text present")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][live-multiturn-endurance] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
