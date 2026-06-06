import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h1_paste_chip_gate.ts <pty_snapshots.txt>")

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
const before = section("before-paste")
const pasted = section("after-paste")
const undone = section("after-undo")
const redone = section("after-redo")
if (!before.includes("prefix")) throw new Error("Before-paste snapshot lost typed prefix")
if (!pasted.includes("[Pasted Content")) throw new Error("Large paste did not render as collapsed paste chip")
if (undone.includes("[Pasted Content")) throw new Error("Undo did not remove collapsed paste chip")
if (!undone.includes("prefix")) throw new Error("Undo removed the existing typed prefix")
if (!redone.includes("[Pasted Content")) throw new Error("Redo did not restore collapsed paste chip")
if (redone.includes("C-Tree")) throw new Error("Redo leaked through to the global Ctrl+Y C-Tree shortcut")
for (const [label, value] of [["after-paste", pasted], ["after-undo", undone], ["after-redo", redone]] as const) {
  if (value.includes("[200~") || value.includes("[201~")) throw new Error(`${label} leaked bracketed paste markers`)
}
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
