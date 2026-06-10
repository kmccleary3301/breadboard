import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_provider_auth_resize_dedupe_gate.ts <artifact-dir> [harness-status]")
const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}
const count = (value: string, pattern: RegExp): number => value.match(pattern)?.length ?? 0
const stateRaw = read("state.ndjson")
const snapshots = read("snapshots.txt")
const harnessOutput = read("harness_output.txt")
const records = stateRaw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
  try { return JSON.parse(line) as any } catch { return null }
}).filter(Boolean)
const state = records[records.length - 1]?.state ?? null
const toolText = Array.isArray(state?.toolEvents) ? state.toolEvents.map((entry: any) => String(entry?.text ?? "")).join("\n") : ""
const conversationText = Array.isArray(state?.conversation) ? state.conversation.map((entry: any) => String(entry?.text ?? "")).join("\n") : ""
const warningStateCount = count(toolText, /Provider retry blocked/g)
const errorStateCount = count(toolText, /\[error\].*api\.responses\.write/g)
const systemErrorCount = count(conversationText, /\[error\].*api\.responses\.write/g)
const duplicateWarningSection = snapshots.split(/^# /m).filter(Boolean).some((section) => count(section, /Provider retry blocked/g) > 1)
const duplicateErrorSection = snapshots.split(/^# /m).filter(Boolean).some((section) => count(section, /\[error\]/g) > 1)
const maxListenersWarning = /MaxListenersExceededWarning/i.test(harnessOutput) || /MaxListenersExceededWarning/i.test(snapshots)
const resizeSnapshots = count(snapshots, /^# provider-auth-/gm)
const bodyPolluted = /Disconnected:|Lost connection to the engine/.test(snapshots)
const report = [
  "# Provider Auth Resize Dedupe Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `resizeSnapshots: ${resizeSnapshots}`,
  `warningStateCount: ${warningStateCount}`,
  `errorStateCount: ${errorStateCount}`,
  `systemErrorCount: ${systemErrorCount}`,
  `duplicateWarningSection: ${duplicateWarningSection}`,
  `duplicateErrorSection: ${duplicateErrorSection}`,
  `maxListenersWarning: ${maxListenersWarning}`,
  `bodyPolluted: ${bodyPolluted}`,
  `pendingResponse: ${String(state?.pendingResponse ?? "none")}`,
  `activity: ${String(state?.activity?.label ?? state?.status ?? "none")}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")
const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (records.length === 0) failures.push("no state records captured")
if (resizeSnapshots < 4) failures.push(`expected at least four provider-auth resize snapshots, saw ${resizeSnapshots}`)
if (warningStateCount !== 1) failures.push(`expected one provider retry warning in final state, saw ${warningStateCount}`)
if (errorStateCount !== 1) failures.push(`expected one provider auth error tool row in final state, saw ${errorStateCount}`)
if (systemErrorCount !== 1) failures.push(`expected one provider auth system transcript row, saw ${systemErrorCount}`)
if (duplicateWarningSection) failures.push("provider retry warning duplicated inside a visible snapshot section")
if (duplicateErrorSection) failures.push("provider auth error duplicated inside a visible snapshot section")
if (maxListenersWarning) failures.push("MaxListenersExceededWarning surfaced")
if (bodyPolluted) failures.push("disconnect banner polluted transcript")
if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[provider-auth-resize] ${item}`).join("\n"))
  process.exit(1)
}
console.log(report)
