import { readFileSync } from "node:fs"
import { buildTranscriptFromEvents } from "../src/repl/transcriptBuilder.js"
import { dumpTranscriptCellRecords } from "../src/repl/transcriptModel.js"
import type { NormalizedEvent } from "../src/repl/transcript/normalizedEvent.js"

const usage = "Usage: tsx scripts/dumpTranscriptCells.ts <normalized-events.json>"

const path = process.argv[2]
if (!path) {
  console.error(usage)
  process.exit(2)
}

const parseEvents = (raw: string): NormalizedEvent[] => {
  const parsed = JSON.parse(raw) as unknown
  if (!Array.isArray(parsed)) {
    throw new Error("Expected top-level JSON array of normalized events")
  }
  return parsed as NormalizedEvent[]
}

try {
  const events = parseEvents(readFileSync(path, "utf8"))
  const transcript = buildTranscriptFromEvents(events)
  const records = dumpTranscriptCellRecords([...transcript.committed, ...transcript.tail])
  process.stdout.write(`${JSON.stringify(records, null, 2)}\n`)
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  console.error(`Failed to dump transcript cells: ${message}`)
  process.exit(1)
}
