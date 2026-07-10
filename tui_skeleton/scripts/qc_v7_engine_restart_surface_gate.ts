import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const artifactDir = process.argv[2]
const harnessStatus = Number(process.argv[3] ?? 0)
if (!artifactDir) throw new Error("Usage: qc_v7_engine_restart_surface_gate.ts <artifact-dir> [harness-status]")

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
const firstOwnedState = states.find((state) => state?.lifecycle?.mode === "local-owned" && state?.lifecycle?.owned === true && typeof state?.lifecycle?.pid === "number")
const restartState = states.find((state) => String(state?.status ?? "") === "Restarting engine")
const lastState = states[states.length - 1] ?? null
const lastLifecycle = lastState?.lifecycle ?? null
const allTools = states.flatMap((state) => Array.isArray(state?.toolEvents) ? state.toolEvents : [])
const logTools = allTools.filter((tool: any) => tool?.kind === "status" && String(tool?.text ?? "").includes("log="))
const restartTools = allTools.filter((tool: any) => tool?.kind === "status" && String(tool?.text ?? "").includes("restart=ok"))
const initialPid = firstOwnedState?.lifecycle?.pid
const restartPid = restartState?.lifecycle?.pid
const finalPid = lastLifecycle?.pid
const pidChanged = typeof initialPid === "number" && typeof finalPid === "number" && initialPid !== finalPid
const restartedFromOriginal = typeof restartPid === "number" && restartPid === initialPid
const recovered = /Recovered \(new session\)/i.test(String(lastState?.status ?? ""))
const sessionRecovered = restartTools.some((tool: any) => /sessionRecovered=yes/.test(String(tool?.text ?? "")))
const restartCount = Number(lastState?.lifecycleRestartCount ?? 0)
const disconnected = lastState?.disconnected === true
const pending = lastState?.pendingResponse === true
const snapshotHasRestart = /restart=ok/.test(snapshots)
const snapshotHasLog = /log=/.test(snapshots)
const bodyPolluted = /Disconnected:|Lost connection to the engine|Stream interruption:/.test(snapshots)
const duplicateRestartRows = (snapshots.match(/restart=ok/g) ?? []).length > 1
const commandTimedOut = /Watchdog timeout|Harness failed/i.test(harnessOutput)

const report = [
  "# V7 Engine Restart Surface Gate Report",
  "",
  `artifactDir: ${artifactDir}`,
  `harnessStatus: ${Number.isFinite(harnessStatus) ? harnessStatus : "unknown"}`,
  `records: ${records.length}`,
  `initialPid: ${initialPid ?? "none"}`,
  `restartStatePid: ${restartPid ?? "none"}`,
  `finalPid: ${finalPid ?? "none"}`,
  `pidChanged: ${pidChanged}`,
  `restartedFromOriginal: ${restartedFromOriginal}`,
  `restartCount: ${restartCount}`,
  `lastStatus: ${String(lastState?.status ?? "none")}`,
  `lastLifecycleMode: ${lastLifecycle?.mode ?? "none"}`,
  `lastLifecycleOwned: ${String(lastLifecycle?.owned ?? "none")}`,
  `pendingResponse: ${String(pending)}`,
  `disconnected: ${String(disconnected)}`,
  `logToolCount: ${logTools.length}`,
  `restartToolCount: ${restartTools.length}`,
  `sessionRecovered: ${sessionRecovered}`,
  `snapshotHasLog: ${snapshotHasLog}`,
  `snapshotHasRestart: ${snapshotHasRestart}`,
  `bodyPolluted: ${bodyPolluted}`,
  `duplicateRestartRows: ${duplicateRestartRows}`,
  `commandTimedOut: ${commandTimedOut}`,
  "",
].join("\n")
writeFileSync(path.join(artifactDir, "gate_report.md"), report, "utf8")

const failures: string[] = []
if (harnessStatus !== 0) failures.push(`harness exited ${harnessStatus}`)
if (commandTimedOut) failures.push("harness timed out or failed before observing restart")
if (records.length === 0) failures.push("no state dump records captured")
if (!firstOwnedState) failures.push("no initial local-owned lifecycle pid captured")
if (!restartState) failures.push("no Restarting engine state captured")
if (!restartedFromOriginal) failures.push("restart state did not reference the original pid before restart")
if (!pidChanged) failures.push("final lifecycle pid did not change after manual restart")
if (restartCount < 1) failures.push("restart count did not increment")
if (!recovered) failures.push("manual restart did not recover to a new session")
if (pending) failures.push("manual restart left pendingResponse true")
if (disconnected) failures.push("manual restart left disconnected true")
if (logTools.length < 1) failures.push("engine logs command did not render a status tool")
if (restartTools.length !== 1) failures.push(`expected exactly one restart=ok status tool, saw ${restartTools.length}`)
if (!sessionRecovered) failures.push("restart=ok status did not report sessionRecovered=yes")
if (!snapshotHasLog) failures.push("snapshot does not show engine log path")
if (!snapshotHasRestart) failures.push("snapshot does not show restart=ok")
if (bodyPolluted) failures.push("lifecycle/disconnect noise polluted the visible body")
if (duplicateRestartRows) failures.push("restart=ok duplicated in visible snapshot")

if (failures.length > 0) {
  writeFileSync(path.join(artifactDir, "gate_failures.txt"), failures.join("\n") + "\n", "utf8")
  console.error(report)
  console.error(failures.map((item) => `[v7][engine-restart-surface] ${item}`).join("\n"))
  process.exit(1)
}

console.log(report)
