import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h2_disabled_slash_gate.ts <pty_snapshots.txt>")

const raw = await fs.readFile(target, "utf8")
if (!raw.includes("# disabled-stop-suggestion")) throw new Error("Missing disabled-stop-suggestion snapshot")
const before = raw.match(/# disabled-stop-suggestion\n([\s\S]*?)(?:\n# |$)/)?.[1] ?? ""
const after = raw.match(/# disabled-stop-after-enter\n([\s\S]*?)(?:\n# |$)/)?.[1] ?? ""
if (!before.includes("/stop")) throw new Error("Disabled stop row did not render /stop")
if (!before.includes("No run")) throw new Error("Disabled stop row did not render disabled reason")
if (!after.includes("❯ /sto")) throw new Error("Enter on disabled stop suggestion mutated or cleared the typed command")
if (after.includes("Unknown command") || after.includes("TASK COMPLETE")) {
  throw new Error("Enter on disabled stop suggestion dispatched a command or request instead of staying inert")
}
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
