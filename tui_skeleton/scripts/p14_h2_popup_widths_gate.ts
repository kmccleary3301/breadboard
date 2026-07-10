import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h2_popup_widths_gate.ts <pty_snapshots.txt>")
const raw = await fs.readFile(target, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
for (const label of ["popup-narrow", "popup-normal", "popup-wide"]) {
  const snapshot = section(label)
  if (!snapshot) throw new Error(`Missing ${label} snapshot`)
  if (!snapshot.includes("❯ /") || !snapshot.includes("/resume") || !snapshot.includes("/transcript")) {
    throw new Error(`${label} did not render the root slash popup`) 
  }
  if (snapshot.includes("/debug-config")) throw new Error(`${label} leaked hidden debug command`)
}
console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
