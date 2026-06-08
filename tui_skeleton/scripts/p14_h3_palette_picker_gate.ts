import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h3_palette_picker_gate.ts <pty_snapshots.txt>")
const raw = await fs.readFile(target, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const open = section("palette-open")
const cancelled = section("palette-cancelled")
const filtered = section("palette-filtered")
const confirmed = section("palette-confirmed")
if (!open.includes("Command palette") || !open.includes("/resume")) throw new Error("Palette did not open through picker surface")
if (cancelled.includes("Command palette")) throw new Error("Palette did not close on Escape")
if (!filtered.includes("Command palette") || !filtered.includes("/models")) throw new Error("Palette did not filter to /models")
if (!confirmed.includes("❯ /models")) throw new Error("Palette Enter did not apply selected command to composer")
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
