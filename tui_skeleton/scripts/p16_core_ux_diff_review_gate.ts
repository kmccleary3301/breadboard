import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

const [snapshotPath, outPath] = process.argv.slice(2)
if (!snapshotPath || !outPath) {
  throw new Error("Usage: tsx scripts/p16_core_ux_diff_review_gate.ts <snapshots.txt> <out.json>")
}

const raw = readFileSync(snapshotPath, "utf8")
const labels = [
  "diff-review-wide",
  "diff-review-file-target",
  "diff-review-hunk-target",
  "diff-review-export",
  "diff-review-copy",
  "diff-review-compact-after-resize",
  "diff-review-wide-restored",
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

for (const label of labels) {
  const body = section(label)
  if (!body.trim()) fail("missing-snapshot", `Missing snapshot section: ${label}`)
  const fullDiffSnapshot = label === "diff-review-wide"
  const resizeFrameSnapshot = ["diff-review-compact-after-resize", "diff-review-wide-restored"].includes(label)
  if (fullDiffSnapshot && !body.includes("Working-tree diff")) {
    fail("diff-title-missing", `${label} does not show working-tree diff title`)
  }
  if (fullDiffSnapshot && !body.includes("src/app.ts")) fail("diff-file-list-missing", `${label} does not show changed tracked file`)
  if (fullDiffSnapshot && !body.includes("Dirty state")) fail("dirty-summary-missing", `${label} does not show dirty summary`)
  if (resizeFrameSnapshot && !body.includes("src/server.c")) {
    fail("resize-frame-diff-context-missing", `${label} does not retain visible diff context after resize`)
  }
  if (!body.includes("Review/approval actions: read-only") && !body.includes("read-only;") && !body.includes("Navigation:")) fail("diff-readonly-truth-missing", `${label} does not show read-only approval truth copy`)
  if (body.includes("No transcript diff found")) fail("diff-fell-through-to-transcript", `${label} fell through to transcript-diff no-op despite dirty git state`)
  if (!body.includes("Type your request") && !body.includes("enter send")) fail("composer-footer-missing", `${label} lost composer/footer after diff command`)
}

const wide = section("diff-review-wide")
if (!wide.includes("```diff") || !wide.includes("+export const added")) {
  fail("patch-preview-missing", "Wide diff snapshot does not include patch preview fenced as diff.")
}
if (!wide.includes("src/server.c") || !wide.includes("+  return 1;")) {
  fail("c-edit-missing", "Wide diff snapshot does not include the dummy C edit.")
}
if (!wide.includes("notes.txt") || !wide.includes("untracked")) {
  fail("untracked-warning-missing", "Wide diff snapshot does not include untracked file warning/list.")
}
if (!section("diff-review-file-target").includes("Working-tree diff file 2") || !section("diff-review-file-target").includes("src/server.c")) {
  fail("file-navigation-missing", "File-target snapshot does not show /diff file navigation for the C file.")
}
if (!section("diff-review-hunk-target").includes("Working-tree diff hunk 2") || !section("diff-review-hunk-target").includes("src/server.c")) {
  fail("hunk-navigation-missing", "Hunk-target snapshot does not show /diff hunk navigation for the C hunk.")
}
if (!section("diff-review-export").includes("Working-tree patch exported.") || !section("diff-review-export").includes("Bytes:")) {
  fail("patch-export-missing", "Export snapshot does not show patch export.")
}
if (!section("diff-review-copy").includes("Working-tree patch copied.") || !section("diff-review-copy").includes("fake-file")) {
  fail("patch-copy-missing", "Copy snapshot does not show patch copy through fake clipboard sink.")
}

const verdict = findings.some((item) => item.severity === "blocker") ? "fail" : "pass"
const report = { verdict, snapshotPath: path.resolve(snapshotPath), labels, findings }
writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`, "utf8")
if (verdict !== "pass") throw new Error(`P16 diff review gate failed: ${JSON.stringify(findings, null, 2)}`)
