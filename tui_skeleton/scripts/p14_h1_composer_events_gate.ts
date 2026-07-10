import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h1_composer_events_gate.ts <composer_events.ndjson>")

const records = (await fs.readFile(target, "utf8"))
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as { type?: string; [key: string]: unknown })

const types = new Set(records.map((record) => record.type))
for (const required of ["line.edit", "history.push", "history.search"]) {
  if (!types.has(required)) throw new Error(`Missing composer event type ${required}`)
}
const search = records.find((record) => record.type === "history.search")
if (typeof search?.preview !== "string" || !search.preview.includes("event log probe")) {
  throw new Error("History search event did not record the matched prompt preview")
}

console.log(JSON.stringify({ verdict: "pass", events: target, count: records.length }, null, 2))
