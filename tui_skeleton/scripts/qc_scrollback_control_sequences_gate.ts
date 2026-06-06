import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const ESC = "\u001b"

const main = async () => {
  const target = process.argv[2]
  if (!target) throw new Error("usage: qc_scrollback_control_sequences_gate.ts <raw-capture>")
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const failures: string[] = []
  const forbidden = [
    { label: "alt-buffer enter", sequence: `${ESC}[?1049h` },
    { label: "clear screen", sequence: `${ESC}[2J` },
    { label: "clear scrollback", sequence: `${ESC}[3J` },
    { label: "full terminal reset", sequence: `${ESC}c` },
  ]
  for (const item of forbidden) {
    const count = raw.split(item.sequence).length - 1
    if (count > 0) failures.push(`forbidden ${item.label} sequence appeared ${count} time(s)`)
  }
  if (!raw.includes("PRE_BB_HISTORY_SENTINEL")) {
    failures.push("missing pre-app history sentinel in raw capture")
  }
  if (!raw.includes("BreadBoard v0.2.0") && !raw.includes("BreadBoard v0.0.0a")) {
    failures.push("missing landing header in raw capture")
  }
  if (failures.length > 0) {
    throw new Error(`Scrollback control-sequence gate failed:\n${failures.join("\n")}`)
  }
  console.log("[qc] scrollback control-sequence gate passed")
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
