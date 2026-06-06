import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) {
  throw new Error("Usage: tsx scripts/p14_h1_help_state_gate.ts <pty_snapshots.txt>")
}

const raw = await fs.readFile(target, "utf8")
const sections = raw.split(/^# /m).filter(Boolean)
const byLabel = new Map<string, string>()
for (const section of sections) {
  const [labelLine = "", ...rest] = section.split(/\r?\n/)
  byLabel.set(labelLine.trim(), rest.join("\n"))
}

const open = byLabel.get("help-open") ?? ""
const dismissed = byLabel.get("help-dismissed") ?? ""
if (!open) throw new Error("Missing help-open snapshot")
if (!dismissed) throw new Error("Missing help-dismissed snapshot")

const openMarkers = ["shortcuts", "Enter submit", "Shift+Enter newline", "Ctrl+O"]
const hasOpenMarker = openMarkers.some((marker) => open.toLowerCase().includes(marker.toLowerCase()))
if (!hasOpenMarker) {
  throw new Error(`Help-open snapshot does not contain a recognizable shortcuts marker: ${openMarkers.join(", ")}`)
}
if (!dismissed.includes("Type your request") && !dismissed.includes("enter send")) {
  throw new Error("Help-dismissed snapshot did not return to the composer-ready surface")
}
if (dismissed.includes("Shortcuts") || dismissed.includes("Press ? or Esc to close")) {
  throw new Error("Help-dismissed viewport still contains the shortcuts modal")
}
if (dismissed.includes("[200~") || dismissed.includes("[201~")) {
  throw new Error("Help-state scenario leaked bracketed paste markers")
}

console.log(JSON.stringify({ verdict: "pass", snapshots: target, openMarkers }, null, 2))
