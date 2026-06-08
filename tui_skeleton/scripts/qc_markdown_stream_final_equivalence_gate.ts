import { readFileSync } from "node:fs"

const [streamPath, finalPath] = process.argv.slice(2)
if (!streamPath || !finalPath) {
  console.error("Usage: qc_markdown_stream_final_equivalence_gate.ts <stream-snapshot> <final-snapshot>")
  process.exit(2)
}

const stream = readFileSync(streamPath, "utf8")
const final = readFileSync(finalPath, "utf8")
const failures: string[] = []

const required = [
  ["Equivalence Heading", "heading"],
  ["Paragraph with Equivalence Bold and Equivalence Italic plus equiv_inline.", "inline rendered paragraph"],
  ["Equivalence quote should render without a raw marker.", "blockquote content"],
  ["- Bold equivalence item", "bold list item"],
  ["- Italic equivalence item", "italic list item"],
  ["STREAM-FINAL-CODE-SENTINEL", "code sentinel"],
  ["+new", "diff addition"],
  ["END-STREAM-FINAL-EQUIVALENCE", "final sentinel"],
] as const

const forbidden = [
  ["code · markdown", "markdown wrapper rendered as code"],
  ["```markdown", "raw markdown wrapper fence"],
  ["# Equivalence Heading", "raw heading marker"],
  ["**Equivalence Bold**", "raw bold delimiter"],
  ["*Equivalence Italic*", "raw italic delimiter"],
  ["**Bold equivalence item**", "raw list bold delimiter"],
  ["*Italic equivalence item*", "raw list italic delimiter"],
  ["> Equivalence quote", "raw blockquote marker"],
  ["Assistant latest", "latest placeholder"],
  ["Assistant streaming", "streaming placeholder"],
] as const

for (const [label, text] of [["stream", stream], ["final", final]] as const) {
  for (const [needle, desc] of required) {
    if (!text.includes(needle)) failures.push(`${label} missing ${desc}: ${needle}`)
  }
  for (const [needle, desc] of forbidden) {
    if (text.includes(needle)) failures.push(`${label} contains forbidden ${desc}: ${needle}`)
  }
}

const semanticLines = (text: string) =>
  required
    .map(([needle]) => needle)
    .filter((needle) => text.includes(needle))
    .sort()
    .join("\n")

if (semanticLines(stream) !== semanticLines(final)) {
  failures.push("stream and final snapshots expose different semantic marker sets")
}

if (failures.length > 0) {
  console.error(`[qc] markdown stream/final equivalence gate failed (${failures.length})`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log("[qc] markdown stream/final equivalence gate passed")
