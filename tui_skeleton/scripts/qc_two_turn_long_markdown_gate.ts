import { readFileSync } from "node:fs"

const [snapshotPath, stateDumpPath] = process.argv.slice(2)
if (!snapshotPath) {
  console.error("Usage: qc_two_turn_long_markdown_gate.ts <snapshots.txt> [state-dump.jsonl]")
  process.exit(2)
}

const snapshot = readFileSync(snapshotPath, "utf8")
const failures: string[] = []

const requireIncludes = (needle: string, label: string) => {
  if (!snapshot.includes(needle)) failures.push(`missing ${label}: ${needle}`)
}
const forbidIncludes = (needle: string, label: string) => {
  if (snapshot.includes(needle)) failures.push(`forbidden ${label}: ${needle}`)
}

for (const [needle, label] of [
  ["Hi! How are you?", "turn one user request"],
  ["Doing well", "turn one assistant response"],
  ["Show me some markdown.", "turn two user request"],
  ["Sample Markdown", "turn two assistant heading"],
  ["Section Heading", "turn two assistant subheading"],
  ["- Bullet item one", "turn two rendered bold list item"],
  ["A blockquote for emphasis.", "turn two rendered blockquote"],
  ["Show me a **lot** more markdown.", "turn three user request"],
  ["Long Markdown Response", "turn three long heading"],
  ["Deep Bold item should render without delimiters.", "turn three rendered bold item"],
  ["Deep Italic item should render without delimiters.", "turn three rendered italic item"],
  ["deep_inline_code", "turn three inline code text"],
  ["Long quote block", "turn three quote content"],
  ["LONG-MARKDOWN-CODE-SENTINEL", "turn three nested code sentinel"],
  ["END-LONG-MARKDOWN-SENTINEL", "turn three final sentinel"],
] as const) {
  requireIncludes(needle, label)
}

for (const [needle, label] of [
  ["code · markdown", "markdown wrapper rendered as code"],
  ["```markdown", "raw markdown wrapper fence"],
  ["# Sample Markdown", "raw turn two heading marker"],
  ["# Long Markdown Response", "raw turn three heading marker"],
  ["**Bullet**", "raw turn two bold delimiter"],
  ["**Deep Bold**", "raw turn three bold delimiter"],
  ["*Deep Italic*", "raw turn three italic delimiter"],
  ["> A blockquote", "raw turn two quote marker"],
  ["> Long quote", "raw turn three quote marker"],
  ["Assistant latest", "placeholder hiding final assistant content"],
  ["Assistant streaming", "placeholder hiding streaming assistant content"],
] as const) {
  forbidIncludes(needle, label)
}

const finalSection = snapshot.split("# after-turn-three-history").at(-1) ?? snapshot
if (!finalSection.includes("END-LONG-MARKDOWN-SENTINEL")) {
  failures.push("final history snapshot does not contain the long-response sentinel")
}
if (!finalSection.includes("❯ Show me a **lot** more markdown.")) {
  failures.push("final history snapshot lost the third user request")
}
for (const [needle, maxCount, label] of [
  ["Long Markdown Response", 1, "long response heading"],
  ["Deep Section One", 1, "long response section"],
  ["Code Section", 1, "long response code section"],
  ["code · python", 2, "nested python code label"],
  ["END-LONG-MARKDOWN-SENTINEL", 1, "long response final sentinel"],
] as const) {
  const count = finalSection.split(needle).length - 1
  if (count > maxCount) {
    failures.push(`final history has ${count} copies of ${label}; expected <= ${maxCount}`)
  }
}

if (stateDumpPath) {
  const rawState = readFileSync(stateDumpPath, "utf8").trim()
  if (!rawState) {
    failures.push("state dump is empty")
  } else {
    const records = rawState.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line))
    const final = records.at(-1)
    const state = final?.state
    const conversation = Array.isArray(state?.conversation) ? state.conversation : []
    const userTexts = conversation.filter((entry: any) => entry?.speaker === "user").map((entry: any) => String(entry.text ?? ""))
    const assistantTexts = conversation.filter((entry: any) => entry?.speaker === "assistant").map((entry: any) => String(entry.text ?? ""))
    if (userTexts.length < 3) failures.push(`state dump has ${userTexts.length} user entries; expected at least 3`)
    if (assistantTexts.length < 3) failures.push(`state dump has ${assistantTexts.length} assistant entries; expected at least 3`)
    if (!userTexts.some((text) => text.includes("Show me a **lot** more markdown."))) {
      failures.push("state dump lost the third user request")
    }
    if (!assistantTexts.some((text) => text.includes("END-LONG-MARKDOWN-SENTINEL"))) {
      failures.push("state dump lost the final long assistant response")
    }
    const lastAssistant = [...conversation].reverse().find((entry: any) => entry?.speaker === "assistant")
    if (!lastAssistant || String(lastAssistant.text ?? "").trim().length === 0) {
      failures.push("state dump final assistant entry is empty or missing")
    }
    if (lastAssistant && lastAssistant.phase !== "final") {
      failures.push(`state dump final assistant phase is ${String(lastAssistant.phase)}; expected final`)
    }
  }
}

if (failures.length > 0) {
  console.error(`[qc] two-turn long markdown gate failed (${failures.length})`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log("[qc] two-turn long markdown dropout gate passed")
