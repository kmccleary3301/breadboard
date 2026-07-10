import { promises as fs } from "node:fs"

const [snapshotPath] = process.argv.slice(2)

if (!snapshotPath) {
  console.error("Usage: node --import tsx scripts/qc_tool_projection_gate.ts <snapshot-path>")
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

const prelude = extractSnapshot("tool-prelude-hot")
const finalHot = extractSnapshot("tool-final-hot")
const settled = extractSnapshot("tool-settled")

if (!prelude.includes("Running a tool with streaming output.")) {
  throw new Error("Hot prelude frame never showed the assistant tool prelude.")
}
if (prelude.includes("[reasoning]") || prelude.includes("Thinking about the tool call.")) {
  throw new Error("Reasoning leaked into the default transcript during the hot prelude frame.")
}

if (!finalHot.includes("Command completed with a warning.")) {
  throw new Error("Hot final frame never showed the assistant follow-up.")
}

if (!settled.includes("Bash(ls -la)")) {
  throw new Error("Settled history lost the final tool row title.")
}
if (!settled.includes("stdout:") || !settled.includes("file_a.txt") || !settled.includes("file_b.log")) {
  throw new Error("Settled history lost stdout detail lines.")
}
if (!settled.includes("stderr:") || !settled.includes("warn: hidden file")) {
  throw new Error("Settled history lost stderr detail lines.")
}
if (!settled.includes("Running a tool with streaming output.")) {
  throw new Error("Settled history lost the assistant tool prelude.")
}
if (!settled.includes("Command completed with a warning.")) {
  throw new Error("Settled history lost the assistant follow-up.")
}
if (
  settled.includes("[reasoning]") ||
  settled.includes("Thinking about the tool call.") ||
  settled.includes("[task tree]") ||
  settled.includes("Tool completed; composing response.")
) {
  throw new Error("Reasoning preview content leaked into the settled ready frame.")
}
if (!settled.includes("enter send")) {
  throw new Error("Settled history did not return to the ready footer.")
}

console.log("[qc] PASS: tool projection preserved assistant prelude/follow-up, durable tool output, and kept reasoning out of transcript history")
