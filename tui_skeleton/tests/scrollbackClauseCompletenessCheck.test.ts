import { mkdir, mkdtemp, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import { evaluateScrollbackClauseCompleteness } from "../tools/assertions/scrollbackClauseCompletenessCheck.ts"

const makeCase = async (body: unknown, id = "case_a"): Promise<string> => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bb-clause-completeness-"))
  const dir = path.join(root, id)
  await mkdir(dir, { recursive: true })
  await writeFile(path.join(dir, "scrollback_clause_verdicts.json"), JSON.stringify(body), "utf8")
  return root
}

describe("scrollbackClauseCompletenessCheck", () => {
  it("passes a non-empty clause/verdict file", async () => {
    const root = await makeCase({
      caseId: "case_a",
      clausesEvaluated: ["GNS-01"],
      verdicts: [{ id: "GNS-01", status: "pass", blocking: true, evidence: [] }],
    })
    const report = await evaluateScrollbackClauseCompleteness(root)
    expect(report.verdict).toBe("pass")
    expect(report.findings).toEqual([])
  })

  it("fails empty clause verdicts", async () => {
    const root = await makeCase({ caseId: "case_a", clausesEvaluated: [], verdicts: [] })
    const report = await evaluateScrollbackClauseCompleteness(root)
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("clause-completeness-empty-clauses")
    expect(report.findings.map((finding) => finding.id)).toContain("clause-completeness-empty-verdicts")
  })

  it("fails missing blocking fields", async () => {
    const root = await makeCase({
      caseId: "case_a",
      clausesEvaluated: ["GNS-01"],
      verdicts: [{ id: "GNS-01", status: "pass" }],
    })
    const report = await evaluateScrollbackClauseCompleteness(root)
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("clause-completeness-verdict-blocking-missing")
  })

  it("fails count mismatches", async () => {
    const root = await makeCase({
      caseId: "case_a",
      clausesEvaluated: ["GNS-01", "GNS-02"],
      verdicts: [{ id: "GNS-01", status: "pass", blocking: true }],
    })
    const report = await evaluateScrollbackClauseCompleteness(root)
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("clause-completeness-count-mismatch")
  })
})
