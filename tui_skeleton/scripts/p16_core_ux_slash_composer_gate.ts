import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const [snapshotPath, outPath] = process.argv.slice(2)
if (!snapshotPath || !outPath) {
  throw new Error("Usage: tsx scripts/p16_core_ux_slash_composer_gate.ts <snapshots.txt> <out.json>")
}

const raw = readFileSync(snapshotPath, "utf8")
const requireHistorySearch = process.env.P16_SLASH_GATE_REQUIRE_HISTORY !== "0"

const section = (label: string): string => {
  const marker = `# ${label}`
  const start = raw.indexOf(marker)
  if (start < 0) return ""
  const next = raw.indexOf("\n# ", start + marker.length)
  return raw.slice(start, next < 0 ? raw.length : next)
}

const labels = [
  "slash-root-wide",
  "slash-root-compact-resize",
  "slash-filter-models",
  "slash-filter-models-after-resize",
  "composer-multiline-compact-height",
  "composer-multiline-wide-after-resize",
  ...(requireHistorySearch ? ["composer-history-search" as const] : []),
] as const

const findings: Array<{ id: string; severity: "blocker" | "warning"; detail: string }> = []
const fail = (id: string, detail: string) => findings.push({ id, severity: "blocker", detail })
const countPromptRows = (body: string, text: string): number =>
  body.split(/\r?\n/).filter((line) => line.trim() === `❯ ${text}` || line.trim() === `› ${text}`).length

for (const label of labels) {
  if (!section(label).trim()) fail("missing-snapshot", `Missing snapshot section: ${label}`)
}

for (const label of ["slash-root-wide", "slash-root-compact-resize"] as const) {
  const body = section(label)
  if (!body.includes("/resume")) fail("slash-root-missing-resume", `${label} does not show /resume`)
  if (!body.includes("/transcript")) fail("slash-root-missing-transcript", `${label} does not show /transcript`)
  if (!body.includes("/attach")) fail("slash-root-missing-attach", `${label} does not show /attach`)
  const promptMatches = body.split(/\r?\n/).filter((line) => /^\s*[❯›]\s*\/\s*$/.test(line)).length
  if (promptMatches > 1) fail("slash-root-duplicate-active-prompt", `${label} contains ${promptMatches} active slash prompt rows`)
}

for (const label of ["slash-filter-models", "slash-filter-models-after-resize"] as const) {
  const body = section(label)
  if (!body.includes("/models")) fail("slash-filter-missing-models", `${label} does not preserve /models`)
  if ((body.match(/\/models/g) ?? []).length > 3) fail("slash-filter-duplicated", `${label} repeats /models more than expected`)
  const promptMatches = body.split(/\r?\n/).filter((line) => /^\s*[❯›]\s*\/mo\s*$/.test(line)).length
  if (promptMatches > 1) fail("slash-filter-duplicate-active-prompt", `${label} contains ${promptMatches} active filtered prompt rows`)
}

for (const label of ["composer-multiline-compact-height", "composer-multiline-wide-after-resize"] as const) {
  const body = section(label)
  if (!body.includes("first line")) fail("composer-missing-first-line", `${label} lost first multiline input line`)
  if (!body.includes("second line")) fail("composer-missing-second-line", `${label} lost second multiline input line`)
  if (body.includes("[13;2u")) fail("composer-shift-enter-literalized", `${label} leaked Shift+Enter control sequence as text`)
  if (!body.includes("enter send")) fail("composer-missing-footer", `${label} lost footer send affordance`)
  const firstLinePrompts = countPromptRows(body, "first line")
  if (firstLinePrompts > 1) fail("composer-duplicate-first-line-prompt", `${label} contains ${firstLinePrompts} active first-line prompt rows`)
  const promptOccurrences = (body.match(/Type your request|❯|›/g) ?? []).length
  if (promptOccurrences > 6) fail("composer-duplicated-prompt", `${label} has suspicious prompt duplication count ${promptOccurrences}`)
}

if (requireHistorySearch) {
  const history = section("composer-history-search")
  if (!history.includes("history")) fail("history-search-missing-query", "history search snapshot lost the recalled/search text")
  if (!history.includes("enter send")) fail("history-search-missing-footer", "history search snapshot lost footer")
  if (countPromptRows(history, "first line") > 1) fail("history-duplicate-first-line-prompt", "history search retained duplicate first-line prompt rows")
}

const verdict = findings.some((item) => item.severity === "blocker") ? "fail" : "pass"
const report = {
  verdict,
  snapshotPath: path.resolve(snapshotPath),
  mode: requireHistorySearch ? "strict-with-history" : "host-subset-no-history",
  labels,
  findings,
}
writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`, "utf8")
if (verdict !== "pass") {
  throw new Error(`P16 slash/composer gate failed: ${JSON.stringify(findings, null, 2)}`)
}
