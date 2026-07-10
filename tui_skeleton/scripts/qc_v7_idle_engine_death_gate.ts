import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
if (!artifactDir) {
  throw new Error("Usage: qc_v7_idle_engine_death_gate.ts <artifact-dir>")
}

const read = (name: string): string => {
  const target = path.join(artifactDir, name)
  return existsSync(target) ? readFileSync(target, "utf8") : ""
}

const stateRaw = read("state.ndjson")
const killLog = read("kill.log")
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

const killedPidMatch = killLog.match(/killed_pid=(\d+)/)
const killedPid = killedPidMatch ? Number(killedPidMatch[1]) : null
const afterKillRecords = killedPid
  ? records.filter((record) => Number(record?.timestamp ?? 0) >= Number(killLog.match(/timestamp=(\d+)/)?.[1] ?? 0))
  : []
const lastRecord = records[records.length - 1] ?? null
const lifecycle = lastRecord?.state?.lifecycle ?? null
const status = String(lastRecord?.state?.status ?? "")
const pidStillRecorded = killedPid != null && lifecycle?.pid === killedPid
const looksReady = /ready/i.test(status)
const explicitRecovery = /recover|restart|reconnect|disconnect|engine/i.test(status)

const report = [
  "# V7 Idle Engine Death Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `records: ${records.length}`,
  `killedPid: ${killedPid ?? "none"}`,
  `lastStatus: ${status || "none"}`,
  `lastLifecycleMode: ${lifecycle?.mode ?? "none"}`,
  `lastLifecycleOwned: ${String(lifecycle?.owned ?? "none")}`,
  `lastLifecyclePid: ${String(lifecycle?.pid ?? "none")}`,
  `afterKillRecords: ${afterKillRecords.length}`,
  `pidStillRecorded: ${pidStillRecorded}`,
  `looksReady: ${looksReady}`,
  `explicitRecovery: ${explicitRecovery}`,
  `snapshotHasDisconnectBody: ${/Disconnected:|Lost connection to the engine/.test(snapshots)}`,
  `harnessOutputBytes: ${harnessOutput.length}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (!killedPid) failures.push("did not observe and kill a local-owned lifecycle pid")
if (records.length === 0) failures.push("no state dump records captured")
if (!lifecycle) failures.push("final state dump lacks lifecycle snapshot")
if (pidStillRecorded && looksReady && !explicitRecovery) {
  failures.push("TUI still reports ready with the killed lifecycle pid and no explicit recovery state")
}
if (pidStillRecorded) failures.push("final lifecycle pid still points at killed engine")
if (!explicitRecovery) failures.push("final status does not expose restart/reconnect/disconnect/recovery semantics")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][idle-engine-death] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
