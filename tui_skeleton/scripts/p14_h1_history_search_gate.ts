import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h1_history_search_gate.ts <pty_snapshots.txt>")

const raw = await fs.readFile(target, "utf8")
const sections = raw.split(/^# /m).filter(Boolean)
const byLabel = new Map<string, string>()
for (const section of sections) {
  const [labelLine = "", ...rest] = section.split(/\r?\n/)
  byLabel.set(labelLine.trim(), rest.join("\n"))
}
const section = (label: string) => {
  const value = byLabel.get(label) ?? ""
  if (!value) throw new Error(`Missing ${label} snapshot`)
  return value
}

const latest = section("history-search-latest-alpha")
const older = section("history-search-older-alpha")
const forward = section("history-search-forward-alpha")
if (!latest.includes("alpha second search")) throw new Error("Ctrl+R did not recall the latest matching alpha prompt")
if (!older.includes("alpha history search")) throw new Error("Second Ctrl+R did not recall the older matching alpha prompt")
if (!forward.includes("alpha second search")) throw new Error("Ctrl+S did not cycle forward to the newer matching alpha prompt")
for (const [label, value] of [["latest", latest], ["older", older], ["forward", forward]] as const) {
  if (value.includes("[200~") || value.includes("[201~")) throw new Error(`${label} snapshot leaked bracketed paste markers`)
}

console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
