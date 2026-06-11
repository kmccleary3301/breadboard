import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_diagnostic_resize_matrix_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}

const count = (value: string, pattern: RegExp): number => value.match(pattern)?.length ?? 0
const rawLeakPattern = /\{'error':|"error"\s*:|'type':\s*'(?:insufficient_quota|invalid_request_error|rate_limit_error)'|context_length_exceeded|Error code:\s*(?:400|429)/i
const records = read("state.ndjson")
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
const snapshots = read("snapshots.txt")
const harnessOutput = read("harness_output.txt")
const state = records[records.length - 1]?.state ?? null
const toolText = Array.isArray(state?.toolEvents)
  ? state.toolEvents.map((entry: any) => String(entry?.text ?? "")).join("\n")
  : ""
const conversationText = Array.isArray(state?.conversation)
  ? state.conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")
  : ""
const allStateText = `${toolText}\n${conversationText}`

const quotaToolCount = count(toolText, /Provider quota exceeded/g)
const quotaConversationCount = count(conversationText, /Provider quota exceeded/g)
const contextToolCount = count(toolText, /Provider context limit exceeded/g)
const contextConversationCount = count(conversationText, /Provider context limit exceeded/g)
const rateToolCount = count(toolText, /Provider rate limit hit/g)
const rateConversationCount = count(conversationText, /Provider rate limit hit/g)
const retryToolCount = count(toolText, /Retrying provider route|Provider retry blocked/g)
const rawStateLeak = rawLeakPattern.test(allStateText)
const rawSnapshotLeak = rawLeakPattern.test(snapshots)
const resizeSnapshots = count(snapshots, /^# diagnostic-/gm)
const duplicateSnapshotSections = snapshots
  .split(/^# /m)
  .filter(Boolean)
  .some((section) =>
    count(section, /\[error\] Provider quota exceeded/g) > 2 ||
    count(section, /\[error\] Provider context limit exceeded/g) > 2 ||
    count(section, /\[error\] Provider rate limit hit/g) > 2)
const maxListenersWarning = /MaxListenersExceededWarning/i.test(harnessOutput) || /MaxListenersExceededWarning/i.test(snapshots)
const disconnectedBanner = /Disconnected:|Lost connection to the engine/.test(snapshots)

const report = [
  "# Diagnostic Resize Matrix Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `resizeSnapshots: ${resizeSnapshots}`,
  `quotaToolCount: ${quotaToolCount}`,
  `quotaConversationCount: ${quotaConversationCount}`,
  `contextToolCount: ${contextToolCount}`,
  `contextConversationCount: ${contextConversationCount}`,
  `rateToolCount: ${rateToolCount}`,
  `rateConversationCount: ${rateConversationCount}`,
  `retryToolCount: ${retryToolCount}`,
  `rawStateLeak: ${rawStateLeak}`,
  `rawSnapshotLeak: ${rawSnapshotLeak}`,
  `duplicateSnapshotSections: ${duplicateSnapshotSections}`,
  `maxListenersWarning: ${maxListenersWarning}`,
  `disconnectedBanner: ${disconnectedBanner}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `activity: ${String(state?.activity?.label ?? state?.status ?? "none")}`,
  "",
].join("\n")

writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")
const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (records.length === 0) failures.push("no state records captured")
if (resizeSnapshots < 4) failures.push(`expected at least four diagnostic resize snapshots, saw ${resizeSnapshots}`)
if (quotaToolCount !== 1) failures.push(`expected one quota error tool row, saw ${quotaToolCount}`)
if (quotaConversationCount !== 1) failures.push(`expected one quota system transcript row, saw ${quotaConversationCount}`)
if (contextToolCount !== 1) failures.push(`expected one context error tool row, saw ${contextToolCount}`)
if (contextConversationCount !== 1) failures.push(`expected one context system transcript row, saw ${contextConversationCount}`)
if (rateToolCount !== 1) failures.push(`expected one rate error tool row, saw ${rateToolCount}`)
if (rateConversationCount !== 1) failures.push(`expected one rate system transcript row, saw ${rateConversationCount}`)
if (retryToolCount !== 0) failures.push(`expected no durable provider retry tool rows, saw ${retryToolCount}`)
if (rawStateLeak) failures.push("raw provider payload leaked into final reducer state")
if (rawSnapshotLeak) failures.push("raw provider payload leaked into terminal snapshots")
if (duplicateSnapshotSections) failures.push("diagnostic error row duplicated inside a visible snapshot section")
if (maxListenersWarning) failures.push("MaxListenersExceededWarning surfaced")
if (disconnectedBanner) failures.push("disconnect banner polluted diagnostic transcript")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), `${failures.join("\n")}\n`, "utf8")
  console.error(report)
  console.error(failures.map((item) => `[diagnostic-resize] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
