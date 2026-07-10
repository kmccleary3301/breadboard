import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const lane = process.argv[3] ?? "unknown"
const harnessStatus = Number(process.argv[4] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_real_terminal_resume_lane_gate.ts <artifact-dir> <lane> [harness-status]")
const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const prompt = read("prompt.txt").trim()
const killLog = read("kill.log")
const stateRaw = read("state.ndjson")
const snapshots = read("snapshots.txt")
const visible = read("visible_text_final.txt")
const scrollback = read("scrollback_text_final.txt")
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
const finalTools = Array.isArray(finalState?.toolEvents) ? finalState.toolEvents : []
const lastToolEvent = finalState?.lastToolEvent
const resumeTools = [
  ...finalTools,
  ...(lastToolEvent ? [lastToolEvent] : []),
].filter((tool: any) => tool?.kind === "status" && /resume=recovered-new-session/.test(String(tool?.text ?? "")))
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const transcriptCells = Array.isArray(finalState?.transcriptCells) ? finalState.transcriptCells : []
const promptNeedle = prompt.slice(0, 28)
const userPromptCount =
  conversation.filter((entry: any) => entry?.speaker === "user" && String(entry?.text ?? "").includes(promptNeedle)).length +
  transcriptCells.filter((entry: any) =>
    (entry?.speaker === "user" || entry?.role === "user-request") &&
    String(entry?.textPreview ?? entry?.text ?? "").includes(promptNeedle)
  ).length
const assistantCount =
  conversation.filter((entry: any) => entry?.speaker === "assistant").length +
  transcriptCells.filter((entry: any) => entry?.speaker === "assistant" || entry?.role === "assistant-message").length
const strippedResumePrompt = transcriptCells.some((entry: any) =>
  (entry?.speaker === "user" || entry?.role === "user-request") &&
  String(entry?.textPreview ?? entry?.text ?? "").trim() === "resume"
)
const pidChanged = Number.isFinite(killedPid) && typeof finalLifecycle?.pid === "number" && finalLifecycle.pid !== killedPid
const bodyText = [snapshots, visible, scrollback].join("\n")
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(bodyText)
const rawEscapeVisible = /\u001b\[|\x1b\[|\[\?25/.test(bodyText)
const readyDuringRecovery = states.some((state) => {
  const status = String(state?.status ?? "")
  const recovery = state?.disconnected === true || /Recovery needed|Reconnecting|Restarting/i.test(status)
  return recovery && /\bReady\b/i.test(status)
})
const commandTimedOut = /Watchdog timeout|Terminal adapter CLI failed|Timed out|Harness failed/i.test(harnessOutput)
const metadata = read("metadata.json")
const hasLaneMetadata = metadata.includes(`"lane": "${lane}"`) || metadata.includes(`"integrationMode": "${lane}"`)

const report = [
  `# V7 Real Terminal Resume Lane Report (${lane})`,
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
  `strippedResumePrompt: ${strippedResumePrompt}`,
  `bodyPolluted: ${bodyPolluted}`,
  `rawEscapeVisible: ${rawEscapeVisible}`,
  `readyDuringRecovery: ${readyDuringRecovery}`,
  `commandTimedOut: ${commandTimedOut}`,
  `hasLaneMetadata: ${hasLaneMetadata}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")
const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("terminal adapter timed out or failed")
if (!hasLaneMetadata) failures.push("missing real-terminal lane metadata")
if (!Number.isFinite(killedPid)) failures.push("did not kill owned engine in real terminal lane")
if (!recoveryState) failures.push("did not observe recovery-needed state")
if (!pidChanged) failures.push("final pid did not change")
if (finalState?.pendingResponse === true) failures.push("final pendingResponse true")
if (finalState?.disconnected === true) failures.push("final disconnected true")
if (userPromptCount !== 1) failures.push(`expected one prompt, saw ${userPromptCount}`)
if (assistantCount !== 0) failures.push(`expected no assistant response from resume-only recovery, saw ${assistantCount}`)
if (resumeTools.length !== 1) failures.push(`expected one resume=recovered-new-session status tool, saw ${resumeTools.length}`)
if (strippedResumePrompt) failures.push("slash command was stripped and submitted as user prompt 'resume'")
if (bodyPolluted) failures.push("disconnect body pollution visible")
if (rawEscapeVisible) failures.push("raw escape/control sequence visible")
if (readyDuringRecovery) failures.push("state shows Ready during recovery")
if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][real-terminal-resume][${lane}] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
