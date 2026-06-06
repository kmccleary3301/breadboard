import { promises as fs } from "node:fs"

const [snapshotPath] = process.argv.slice(2)

if (!snapshotPath) {
  console.error("Usage: node --import tsx scripts/qc_streaming_markdown_gate.ts <snapshot-path>")
  process.exit(1)
}

const raw = await fs.readFile(snapshotPath, "utf8")
const SNAPSHOT_LABELS = new Set(["streaming-partial", "streaming-final-hot", "streaming-settled"])

const parseSnapshots = (): Map<string, string> => {
  const lines = raw.split(/\r?\n/)
  const sections = new Map<string, string[]>()
  let active: string | null = null

  for (const line of lines) {
    if (line.startsWith("# ")) {
      const label = line.slice(2).trim()
      if (SNAPSHOT_LABELS.has(label)) {
        active = label
        sections.set(label, [])
        continue
      }
    }
    if (active) {
      sections.get(active)!.push(line)
    }
  }

  return new Map(Array.from(sections.entries()).map(([label, body]) => [label, body.join("\n").trim()]))
}

const parsedSnapshots = parseSnapshots()

const extractSnapshot = (label: string): string => {
  const value = parsedSnapshots.get(label)
  if (!value) {
    throw new Error(`Missing snapshot section: ${label}`)
  }
  return value
}

const includesStreamingHeading = (text: string): boolean =>
  text.includes("## Streaming") || text.includes("# Streaming") || text.includes("Streaming")

const partial = extractSnapshot("streaming-partial")
const finalHot = extractSnapshot("streaming-final-hot")
const settled = extractSnapshot("streaming-settled")

if (!includesStreamingHeading(partial)) {
  throw new Error("Partial snapshot never surfaced the assistant markdown heading.")
}
if (!partial.includes("- first")) {
  throw new Error("Partial snapshot never surfaced the first streamed bullet.")
}
if (partial.includes("console.log") || partial.includes("code · ts")) {
  throw new Error("Partial snapshot already contains the final code block; hot-tail streaming is not visible.")
}
if (!includesStreamingHeading(finalHot)) {
  throw new Error("Hot final snapshot lost the assistant markdown heading.")
}
if (!finalHot.includes("- second")) {
  throw new Error("Hot final snapshot lost the second streamed bullet.")
}
if (!finalHot.includes("console.log") && !finalHot.includes("code · ts")) {
  throw new Error("Hot final snapshot never reached the completed code block.")
}
if (!includesStreamingHeading(settled)) {
  throw new Error("Settled snapshot lost the assistant markdown heading after commit.")
}
if (!settled.includes("- second") || (!settled.includes("console.log") && !settled.includes("code · ts"))) {
  throw new Error("Settled snapshot lost committed assistant markdown content.")
}
if (!settled.includes("enter send")) {
  throw new Error("Settled snapshot did not return to the ready footer.")
}

console.log("[qc] PASS: streaming markdown surfaced hot partial/final frames and settled committed output in PTY")
