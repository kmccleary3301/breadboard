import { promises as fs } from "node:fs"
import process from "node:process"

const [snapshotsPath, eventsPath] = process.argv.slice(2)
if (!snapshotsPath || !eventsPath) {
  throw new Error("Usage: tsx scripts/p14_h1_attachment_semantics_gate.ts <pty_snapshots.txt> <composer_events.ndjson>")
}

const raw = await fs.readFile(snapshotsPath, "utf8")
const sections = raw.split(/^# /m).filter(Boolean)
const byLabel = new Map<string, string>()
for (const section of sections) {
  const [labelLine = "", ...rest] = section.split(/\r?\n/)
  byLabel.set(labelLine.trim(), rest.join("\n"))
}
const queued = byLabel.get("attachment-queued") ?? ""
const after = byLabel.get("attachment-after-submit") ?? ""
if (!queued.includes("Attachments queued")) throw new Error("Attachment queued snapshot does not show attachment queue UI")
if (!queued.includes("image/png")) throw new Error("Attachment queued snapshot does not show pasted image MIME")
if (after.includes("Attachments queued")) throw new Error("Attachment queue remained visible after submit")

const events = (await fs.readFile(eventsPath, "utf8"))
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line) => JSON.parse(line) as { type?: string; mime?: string })
if (!events.some((event) => event.type === "attachment.add" && event.mime === "image/png")) {
  throw new Error("Composer event log did not record image attachment add")
}
if (!events.some((event) => event.type === "attachment.clear")) {
  throw new Error("Composer event log did not record attachment clear after submit")
}

console.log(JSON.stringify({ verdict: "pass", snapshots: snapshotsPath, events: eventsPath }, null, 2))
