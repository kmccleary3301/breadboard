import { promises as fs } from "node:fs"
import process from "node:process"

const [snapshotsPath, statePath] = process.argv.slice(2)
if (!snapshotsPath || !statePath) {
  throw new Error("Usage: tsx scripts/p14_h1_queue_running_gate.ts <pty_snapshots.txt> <repl_state.ndjson>")
}

const snapshots = await fs.readFile(snapshotsPath, "utf8")
if (!snapshots.includes("queued-preview")) throw new Error("Missing queued-preview snapshot")
if (!snapshots.includes("Queued prompt")) throw new Error("Queued prompt preview was not visible while the first turn was running")
if (!snapshots.includes("second queued prompt")) throw new Error("Queued prompt preview did not include the queued prompt text")
if (snapshots.includes("[200~") || snapshots.includes("[201~")) {
  throw new Error("Queue snapshots contain bracketed paste marker leakage")
}

const records = (await fs.readFile(statePath, "utf8"))
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as any)

const sawFirstPending = records.some((record) => record?.state?.pendingResponse === true)
if (!sawFirstPending) throw new Error("State dump never observed the first turn in pending/running state")

const sawQueuedDispatch = records.some((record) => {
  const conversation = record?.state?.lastConversation
  return conversation?.speaker === "user" && typeof conversation.preview === "string" && conversation.preview.includes("second queued prompt")
})
if (!sawQueuedDispatch) throw new Error("Queued prompt was not dispatched as a durable user conversation entry")

console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, state: statePath }, null, 2))
