import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_doctor_footer_gate.ts <artifact-dir> [harness-status]")

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
    try { return JSON.parse(line) as any } catch { return null }
  })
  .filter(Boolean)
const states = records.map((record) => record?.state).filter(Boolean)
const lastState = states[states.length - 1] ?? null
const allTools = states.flatMap((state) => Array.isArray(state?.toolEvents) ? state.toolEvents : [])
const doctorTools = allTools.filter((tool: any) => tool?.kind === "status" && String(tool?.text ?? "").includes("[doctor]"))
const latestDoctorText = String(doctorTools.at(-1)?.text ?? "")

const hasMode = /engineMode=local-owned/.test(latestDoctorText)
const hasOwned = /owned=yes/.test(latestDoctorText)
const hasRestartAvailable = /restartAvailable=yes/.test(latestDoctorText)
const hasHealth = /health=/.test(latestDoctorText)
const hasLog = /log=/.test(latestDoctorText)
const hasRecommendation = /recommendation=/.test(latestDoctorText)
const snapshotHasDoctor = /\[doctor\]|\bDoctor\b/.test(snapshots)
const snapshotHasReadyFooter = /\[ready\]/i.test(snapshots)
const bodyPolluted = /Disconnected:|Lost connection to the engine|Stream interruption:/.test(snapshots)
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Doctor/Footer Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `lastStatus: ${String(lastState?.status ?? "none")}`,
  `pendingResponse: ${String(lastState?.pendingResponse ?? "none")}`,
  `disconnected: ${String(lastState?.disconnected ?? "none")}`,
  `doctorToolCount: ${doctorTools.length}`,
  `hasMode: ${hasMode}`,
  `hasOwned: ${hasOwned}`,
  `hasRestartAvailable: ${hasRestartAvailable}`,
  `hasHealth: ${hasHealth}`,
  `hasLog: ${hasLog}`,
  `hasRecommendation: ${hasRecommendation}`,
  `snapshotHasDoctor: ${snapshotHasDoctor}`,
  `snapshotHasReadyFooter: ${snapshotHasReadyFooter}`,
  `bodyPolluted: ${bodyPolluted}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed")
if (records.length === 0) failures.push("no state dump records captured")
if (doctorTools.length !== 1) failures.push(`expected exactly one doctor status tool, saw ${doctorTools.length}`)
if (!hasMode) failures.push("doctor report did not include local-owned engine mode")
if (!hasOwned) failures.push("doctor report did not include owned=yes")
if (!hasRestartAvailable) failures.push("doctor report did not include restartAvailable=yes")
if (!hasHealth) failures.push("doctor report did not include health status")
if (!hasLog) failures.push("doctor report did not include log path")
if (!hasRecommendation) failures.push("doctor report did not include a recommendation")
if (!snapshotHasDoctor) failures.push("snapshot does not show doctor report")
if (!snapshotHasReadyFooter) failures.push("snapshot did not show ready footer after doctor command")
if (bodyPolluted) failures.push("doctor snapshot includes lifecycle/disconnect body pollution")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][doctor-footer] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
