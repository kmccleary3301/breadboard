import { promises as fs } from "node:fs"
import process from "node:process"

const snapshotsPath = process.argv[2]
const statePath = process.argv[3]
if (!snapshotsPath || !statePath) throw new Error("Usage: tsx scripts/p14_h3_recent_sessions_picker_gate.ts <pty_snapshots.txt> <repl_state.ndjson>")
const raw = await fs.readFile(snapshotsPath, "utf8")
const stateRaw = await fs.readFile(statePath, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const open = section("recent-sessions-open")
const close = section("recent-sessions-close")
let sessionId = ""
for (const line of stateRaw.trim().split(/\r?\n/).reverse()) {
  try {
    const parsed = JSON.parse(line) as { state?: { sessionId?: string } }
    if (parsed.state?.sessionId) { sessionId = parsed.state.sessionId; break }
  } catch {}
}
if (!open.includes("Recent sessions")) throw new Error("Recent sessions picker did not open")
if (!open.includes("Enter attach") || !open.includes("R refresh") || !open.includes("Esc close")) throw new Error("Recent sessions picker missing keyboard contract hints")
if (!sessionId || !open.includes(sessionId)) throw new Error("Recent sessions picker did not include active session row")
if (close.includes("Recent sessions")) throw new Error("Recent sessions picker did not close on Escape")
console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, stateDump: statePath }, null, 2))
