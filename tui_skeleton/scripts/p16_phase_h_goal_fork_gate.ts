import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import process from "node:process"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: tsx scripts/p16_phase_h_goal_fork_gate.ts <artifact-dir> [harness-status]")

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}

const section = (snapshots: string, label: string): string => {
  const parts = snapshots.split(/^# /m)
  for (const part of parts) {
    const trimmed = part.trimEnd()
    if (!trimmed) continue
    const newline = trimmed.indexOf("\n")
    if (newline === -1) continue
    const currentLabel = trimmed.slice(0, newline).trim()
    if (currentLabel === label) return trimmed.slice(newline + 1)
  }
  return ""
}

const count = (value: string, needle: string): number => value.split(needle).length - 1

const snapshots = read("snapshots.txt")
const rawOutput = read("raw_output.txt")
const harnessOutput = read("harness_output.txt")
const stateRaw = read("state.ndjson")
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
const finalState = records.at(-1)?.state ?? null
const conversation = Array.isArray(finalState?.conversation) ? finalState.conversation : []
const allConversationText = conversation.map((entry: any) => String(entry?.text ?? "")).join("\n")

const defaults = section(snapshots, "slash-defaults-no-goal-fork")
const goal = section(snapshots, "goal-feature-gated-exact")
const fork = section(snapshots, "fork-feature-gated-exact")
const doctor = section(snapshots, "doctor-after-deferred-commands")
const compact = section(snapshots, "deferred-and-doctor-compact-resize")

const body = `${snapshots}\n${rawOutput}\n${harnessOutput}`
const failures: string[] = []

if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (/Watchdog timeout|Harness failed/i.test(harnessOutput)) failures.push("harness timed out or failed")
if (!defaults) failures.push("missing slash defaults snapshot")
if (!goal) failures.push("missing goal fail-closed snapshot")
if (!fork) failures.push("missing fork fail-closed snapshot")
if (!doctor) failures.push("missing doctor snapshot")
if (!compact) failures.push("missing compact resize snapshot")
if (!defaults.includes("/resume") || !defaults.includes("/transcript")) failures.push("default slash suggestions missing expected visible commands")
if (defaults.includes("/goal") || defaults.includes("/fork")) failures.push("/goal or /fork appears in default visible suggestions")
if (!goal.includes("/goal") || !goal.includes("durable goal") || !goal.includes("Status: feature-gated")) {
  failures.push("/goal exact invocation did not show durable-goal feature-gated copy")
}
if (!fork.includes("/fork") || !fork.includes("session graph") || !fork.includes("Status: feature-gated")) {
  failures.push("/fork exact invocation did not show session-graph feature-gated copy")
}
if (!/Doctor|\[doctor\]/.test(doctor) || !doctor.includes("recommendation=")) failures.push("/doctor did not render diagnostic recommendation after deferred commands")
if (!/Doctor|\[doctor\]/.test(compact) || !compact.includes("recommendation=")) failures.push("compact resize lost doctor/recovery diagnostic content")
if (/TASK COMPLETE|Tool execution results|assistant\.message|You are Codex/i.test(body)) failures.push("deferred command path shows evidence of model/tool submission")
if (/finish the P16 final design plan/.test(allConversationText)) failures.push("/goal objective entered model conversation")
if (count(goal, "Status: feature-gated") !== 1) failures.push("/goal feature-gated result duplicated in snapshot")
if (count(fork, "Status: feature-gated") !== 2) {
  failures.push("expected cumulative goal+fork feature-gated rows exactly once each after /fork")
}
if (finalState?.pendingResponse === true) failures.push("final state pendingResponse=true after local-only commands")
if (finalState?.disconnected === true) failures.push("final state disconnected=true after local-only commands")

const report = {
  artifactDir,
  harnessStatus,
  records: records.length,
  conversationCount: conversation.length,
  localOnlyCommandResultCount: count(snapshots, "Status: feature-gated"),
  finalStatus: finalState?.status ?? null,
  pendingResponse: finalState?.pendingResponse ?? null,
  disconnected: finalState?.disconnected ?? null,
  snapshots: {
    defaults: Boolean(defaults),
    goal: Boolean(goal),
    fork: Boolean(fork),
    doctor: Boolean(doctor),
    compact: Boolean(compact),
  },
  failures,
}

writeFileSync(path.join(artifactDir, "goal_fork_gate_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
writeFileSync(
  path.join(artifactDir, "gate_report.md"),
  [
    "# P16 Phase H Goal/Fork Fail-Closed Gate",
    "",
    `artifactDir: ${artifactDir}`,
    `harnessStatus: ${harnessStatus}`,
    `records: ${records.length}`,
    `conversationCount: ${conversation.length}`,
    `localOnlyCommandResultCount: ${count(snapshots, "Status: feature-gated")}`,
    `finalStatus: ${String(finalState?.status ?? "none")}`,
    `pendingResponse: ${String(finalState?.pendingResponse ?? "none")}`,
    `disconnected: ${String(finalState?.disconnected ?? "none")}`,
    `failures: ${failures.length}`,
    "",
  ].join("\n"),
  "utf8",
)

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), `${failures.join("\n")}\n`, "utf8")
  console.error(failures.map((item) => `[p16][phase-h][goal-fork] ${item}`).join("\n"))
  process.exit(1)
}

console.log(JSON.stringify(report, null, 2))
