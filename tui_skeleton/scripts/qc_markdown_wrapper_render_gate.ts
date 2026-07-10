import { readFileSync } from "node:fs"

const path = process.argv[2]
if (!path) {
  console.error("Usage: qc_markdown_wrapper_render_gate.ts <snapshots.txt>")
  process.exit(2)
}

const snapshot = readFileSync(path, "utf8")
const required = [
  ["Sample Markdown", "heading text"],
  ["Section Heading", "subheading text"],
  ["- Bullet item one", "bold list item without delimiters"],
  ["- Italic item two", "italic list item without delimiters"],
  ["A blockquote for emphasis.", "blockquote content"],
  ["def greet(name):", "nested code content"],
] as const
const forbidden = [
  ["code · markdown", "markdown wrapper rendered as code"],
  ["```markdown", "raw markdown fence"],
  ["# Sample Markdown", "raw heading marker"],
  ["**Bullet**", "raw bold marker"],
  ["*Italic*", "raw italic marker"],
  ["> A blockquote", "raw blockquote marker"],
  ["Assistant latest", "height-pressure placeholder hiding final answer"],
] as const

const failures: string[] = []
for (const [needle, label] of required) {
  if (!snapshot.includes(needle)) failures.push(`missing ${label}: ${needle}`)
}
for (const [needle, label] of forbidden) {
  if (snapshot.includes(needle)) failures.push(`forbidden ${label}: ${needle}`)
}

if (failures.length > 0) {
  console.error(`[qc] markdown wrapper render gate failed (${failures.length})`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log("[qc] markdown wrapper render gate passed")
