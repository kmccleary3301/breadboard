import { promises as fs } from "node:fs"
import process from "node:process"

const [snapshotsPath, statePath] = process.argv.slice(2)
if (!snapshotsPath || !statePath) {
  throw new Error("Usage: tsx scripts/p14_h1_shell_prefix_gate.ts <pty_snapshots.txt> <repl_state.ndjson>")
}

const snapshots = await fs.readFile(snapshotsPath, "utf8")
if (!snapshots.includes("shell-prefix-typed")) throw new Error("Missing shell-prefix typed snapshot")
if (!snapshots.includes("!pwd")) throw new Error("Typed shell-prefix input was not visible before submit")
if (snapshots.includes("[200~") || snapshots.includes("[201~")) {
  throw new Error("Shell-prefix snapshots contain bracketed paste marker leakage")
}

const records = (await fs.readFile(statePath, "utf8"))
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as any)

const userRecord = records.find((record) => {
  const conversation = record?.state?.lastConversation
  return (
    conversation?.speaker === "user" &&
    typeof conversation.preview === "string" &&
    conversation.preview.includes("Run this shell command") &&
    conversation.preview.includes("pwd")
  )
})
if (!userRecord) {
  throw new Error("State dump never recorded shell-prefix input as an explicit shell intent payload")
}

console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, state: statePath }, null, 2))
