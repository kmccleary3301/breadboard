import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h3_model_picker_widths_gate.ts <pty_snapshots.txt>")
const raw = await fs.readFile(target, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
for (const label of ["model-picker-64", "model-picker-80", "model-picker-120"]) {
  const snapshot = section(label)
  if (!snapshot.includes("Models") && !snapshot.includes("Select a model")) throw new Error(`${label} missing model picker title`)
  if (!snapshot.includes("gpt") && !snapshot.includes("openrouter")) throw new Error(`${label} missing model rows`)
  if (!snapshot.includes("Esc")) throw new Error(`${label} missing cancel hint`)
}
const closed = section("model-picker-closed")
const lastPrompt = closed.lastIndexOf("❯")
const lastModel = Math.max(closed.lastIndexOf("Select a model"), closed.lastIndexOf("Provider:"))
if (lastPrompt < 0 || lastModel > lastPrompt) throw new Error("Model picker did not close on Escape")
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
