import { promises as fs } from "node:fs"

const [snapshotPath] = process.argv.slice(2)

if (!snapshotPath) {
  console.error("Usage: node --import tsx scripts/qc_reasoning_projection_gate.ts <snapshot-path>")
  process.exit(1)
}

const raw = await fs.readFile(snapshotPath, "utf8")

const extractSnapshot = (label: string): string => {
  const marker = `# ${label}\n`
  const start = raw.indexOf(marker)
  if (start < 0) {
    throw new Error(`Missing snapshot section: ${label}`)
  }
  const rest = raw.slice(start + marker.length)
  const nextHeader = rest.indexOf("\n# ")
  return (nextHeader >= 0 ? rest.slice(0, nextHeader) : rest).trim()
}

const hot = extractSnapshot("reasoning-hot")
const settled = extractSnapshot("reasoning-settled")
const artifact = extractSnapshot("thinking-artifact")

if (!hot.includes("Reasoning stays in the preview.")) {
  throw new Error("Hot frame never showed the assistant answer.")
}
if (!hot.includes("[task tree]")) {
  throw new Error("Hot frame never surfaced the reasoning preview widget.")
}
if (hot.includes("[reasoning]")) {
  throw new Error("Hot frame leaked reasoning into transcript/tool rails.")
}

if (!settled.includes("Reasoning stays in the preview.")) {
  throw new Error("Settled frame lost the committed assistant answer.")
}
if (settled.includes("[reasoning]") || settled.includes("[task tree]") || settled.includes("Internal chain step one.") || settled.includes("Plan the answer in brief.")) {
  throw new Error("Settled ready frame still contains hot reasoning surfaces or leaked reasoning text.")
}
if (!settled.includes("enter send")) {
  throw new Error("Settled frame did not return to the ready footer.")
}

if (!artifact.includes("Thinking ·")) {
  throw new Error("Explicit /thinking command did not surface the thinking artifact.")
}
if (!artifact.includes("mode=summary")) {
  throw new Error("Thinking artifact did not surface the expected summary-mode header.")
}
if (!artifact.includes("Plan the answer in brief.")) {
  throw new Error("Thinking artifact did not preserve the summary content.")
}
if (artifact.includes("[raw]")) {
  throw new Error("Summary-mode /thinking surfaced raw reasoning unexpectedly.")
}

console.log("[qc] PASS: reasoning stayed in hot preview/artifact surfaces and did not leak into settled transcript history")
