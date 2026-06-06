import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h3_file_picker_contract_gate.ts <pty_snapshots.txt>")
const raw = await fs.readFile(target, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const open = section("file-picker-open")
const resized = section("file-picker-resized")
const filtered = section("file-picker-filtered")
const closed = section("file-picker-closed")
for (const [label, snapshot] of [["open", open], ["resized", resized], ["filtered", filtered]] as const) {
  if (!snapshot.includes("Attach file")) throw new Error(`File picker ${label} snapshot missing title`)
  if (!snapshot.includes("Tab/Enter") && !snapshot.includes("Tab/Ent")) throw new Error(`File picker ${label} snapshot missing completion hint`)
}
if (!filtered.includes("❯ @")) throw new Error("File picker filtered snapshot did not preserve active @ query")
const lastPrompt = closed.lastIndexOf("❯")
const lastPicker = closed.lastIndexOf("Attach file")
if (lastPrompt < 0 || lastPicker > lastPrompt) throw new Error("File picker did not close on Escape")
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
