import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateTranscriptResultActionability } from "../tools/assertions/transcriptResultActionabilityCheck.ts"

const writeCase = async (snapshots: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-transcript-result-actionability-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  return dir
}

describe("evaluateTranscriptResultActionability", () => {
  it("accepts transcript-driven result and artifact inspection evidence", async () => {
    const dir = await writeCase(
      [
        "# tool-selected",
        "Write(report.txt)",
        "o inspect",
        "Enter open artifact",
        "# result-detail-open",
        "Result detail",
        "Artifact persisted for transcript inspection.",
        "# artifact-preview-open",
        "Artifact preview",
        "scripts/fixtures/p3_result_actionability_report.txt",
        "P3 artifact preview line 01",
        "Preview truncated to 4 lines.",
        "",
      ].join("\n"),
    )
    await expect(evaluateTranscriptResultActionability(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing actionability contract details", async () => {
    const dir = await writeCase(
      [
        "# tool-selected",
        "Write(report.txt)",
        "# result-detail-open",
        "Result detail",
        "# artifact-preview-open",
        "Artifact preview",
        "",
      ].join("\n"),
    )
    const anomalies = await evaluateTranscriptResultActionability(dir)
    expect(anomalies.map((item) => item.id)).toContain("tool-action-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("artifact-action-hint-missing")
    expect(anomalies.map((item) => item.id)).toContain("result-detail-missing")
    expect(anomalies.map((item) => item.id)).toContain("artifact-preview-header-missing")
    expect(anomalies.map((item) => item.id)).toContain("artifact-preview-content-missing")
    await rm(dir, { recursive: true, force: true })
  })
})
