import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

const args = process.argv.slice(2)
const defaultPaths = [
  "scripts/_tmp_qc_markdown_wrapper_render.txt",
  "scripts/_tmp_qc_two_turn_long_markdown.txt",
  "scripts/_tmp_qc_markdown_equivalence_stream.txt",
  "scripts/_tmp_qc_markdown_equivalence_final.txt",
  "scripts/_tmp_qc_streaming_markdown_pty.txt",
]
const targets = args.length > 0 ? args : defaultPaths

const forbidden = [
  ["code · markdown", "markdown wrapper rendered as code"],
  ["```markdown", "raw markdown wrapper fence"],
  ["# Sample Markdown", "raw sample heading marker"],
  ["# Long Markdown Response", "raw long-response heading marker"],
  ["# Equivalence Heading", "raw equivalence heading marker"],
  ["**Bullet**", "raw sample bold delimiter"],
  ["*Italic*", "raw sample italic delimiter"],
  ["**Deep Bold**", "raw long bold delimiter"],
  ["*Deep Italic*", "raw long italic delimiter"],
  ["**Equivalence Bold**", "raw equivalence bold delimiter"],
  ["*Equivalence Italic*", "raw equivalence italic delimiter"],
  ["**Bold equivalence item**", "raw equivalence list bold delimiter"],
  ["*Italic equivalence item*", "raw equivalence list italic delimiter"],
  ["> A blockquote", "raw sample blockquote marker"],
  ["> Long quote", "raw long blockquote marker"],
  ["> Equivalence quote", "raw equivalence blockquote marker"],
  ["Assistant latest", "latest placeholder"],
  ["Assistant streaming", "streaming placeholder"],
] as const

const failures: string[] = []
const checked: string[] = []
for (const target of targets) {
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  if (!existsSync(resolved)) continue
  checked.push(target)
  const text = readFileSync(resolved, "utf8")
  for (const [needle, label] of forbidden) {
    if (text.includes(needle)) failures.push(`${target}: forbidden ${label}: ${needle}`)
  }
}

if (checked.length === 0) {
  failures.push(`no snapshot files found among: ${targets.join(", ")}`)
}

if (failures.length > 0) {
  console.error(`[qc] markdown artifact sentinel failed (${failures.length})`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log(`[qc] markdown artifact sentinel passed (${checked.length} file(s) checked)`)
