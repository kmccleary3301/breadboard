import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const [snapshotPath, outPath] = process.argv.slice(2)
if (!snapshotPath || !outPath) {
  throw new Error("Usage: tsx scripts/p16_core_ux_composer_input_gate.ts <snapshots.txt> <out.json>")
}

const raw = readFileSync(snapshotPath, "utf8")
const labels = [
  "multiline-compact",
  "multiline-wide-after-resize",
  "bracketed-paste",
  "bracketed-paste-compact-resize",
  "history-recall",
] as const
const section = (label: string): string => {
  const marker = `# ${label}`
  const start = raw.indexOf(marker)
  if (start < 0) return ""
  const next = raw.indexOf("\n# ", start + marker.length)
  return raw.slice(start, next < 0 ? raw.length : next)
}
const findings: Array<{ id: string; severity: "blocker" | "warning"; detail: string }> = []
const fail = (id: string, detail: string) => findings.push({ id, severity: "blocker", detail })
const countPromptRows = (body: string, text: string): number =>
  body.split(/\r?\n/).filter((line) => line.trim() === `❯ ${text}` || line.trim() === `› ${text}`).length

for (const label of labels) {
  const body = section(label)
  if (!body.trim()) fail("missing-snapshot", `Missing snapshot section: ${label}`)
  if (!body.includes("enter send")) fail("composer-missing-footer", `${label} lost footer send affordance`)
  if (body.includes("[13;2u")) fail("composer-shift-enter-literalized", `${label} leaked Shift+Enter residue`)
}

for (const label of ["multiline-compact", "multiline-wide-after-resize"] as const) {
  const body = section(label)
  if (!body.includes("first line")) fail("composer-missing-first-line", `${label} lost first multiline line`)
  if (!body.includes("second line")) fail("composer-missing-second-line", `${label} lost second multiline line`)
  const firstLinePrompts = countPromptRows(body, "first line")
  if (firstLinePrompts > 1) fail("composer-duplicate-first-line-prompt", `${label} contains ${firstLinePrompts} active first-line prompt rows`)
}

for (const label of ["bracketed-paste", "bracketed-paste-compact-resize"] as const) {
  const body = section(label)
  if (!body.includes("pasted alpha")) fail("composer-missing-paste-alpha", `${label} lost pasted alpha payload`)
  if (!body.includes("pasted beta")) fail("composer-missing-paste-beta", `${label} lost pasted beta payload`)
  if (body.includes("[200~") || body.includes("[201~")) fail("composer-paste-marker-leak", `${label} leaked bracketed paste markers`)
  const staleFirstLinePrompts = countPromptRows(body, "first line")
  if (staleFirstLinePrompts > 0) fail("composer-stale-first-line-prompt", `${label} retained stale first-line prompt rows after paste`)
  const pastedPrompts = countPromptRows(body, "pasted alpha")
  if (pastedPrompts > 1) fail("composer-duplicate-paste-prompt", `${label} contains ${pastedPrompts} active pasted prompt rows`)
}

const history = section("history-recall")
if (!history.includes("history")) fail("history-query-missing", "history recall snapshot lost query text")
if (!history.includes("history scratch")) fail("history-recall-missing-stashed-entry", "history recall did not surface the stashed history entry")
if (countPromptRows(history, "first line") > 0) fail("history-stale-first-line-prompt", "history recall retained stale first-line prompt rows")

const verdict = findings.some((item) => item.severity === "blocker") ? "fail" : "pass"
const report = { verdict, snapshotPath: path.resolve(snapshotPath), labels, findings }
writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`, "utf8")
if (verdict !== "pass") throw new Error(`P16 composer input gate failed: ${JSON.stringify(findings, null, 2)}`)
