import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h1_history_recall_gate.ts <pty_snapshots.txt>")

const raw = await fs.readFile(target, "utf8")
const sections = raw.split(/^# /m).filter(Boolean)
const byLabel = new Map<string, string>()
for (const section of sections) {
  const [labelLine = "", ...rest] = section.split(/\r?\n/)
  byLabel.set(labelLine.trim(), rest.join("\n"))
}

const requireSection = (label: string) => {
  const section = byLabel.get(label) ?? ""
  if (!section) throw new Error(`Missing ${label} snapshot`)
  return section
}

const last = requireSection("history-last")
const first = requireSection("history-first")
const forward = requireSection("history-forward")

if (!last.includes("second history")) throw new Error("Up recall did not restore the latest submitted prompt")
if (!first.includes("first history")) throw new Error("Second Up recall did not restore the previous submitted prompt")
if (!forward.includes("second history")) throw new Error("Down recall did not move forward to the newer submitted prompt")
if (last.includes("[200~") || first.includes("[200~") || forward.includes("[200~")) {
  throw new Error("History recall snapshots contain bracketed paste marker leakage")
}

console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
