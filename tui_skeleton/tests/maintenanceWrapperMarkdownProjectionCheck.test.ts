import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateMaintenanceWrapperMarkdownProjection } from "../tools/assertions/maintenanceWrapperMarkdownProjectionCheck.ts"

const writeCase = async (snapshots: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-markdown-projection-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  return dir
}

describe("evaluateMaintenanceWrapperMarkdownProjection", () => {
  it("accepts stacked table and fenced code cues", async () => {
    const dir = await writeCase(
      [
        "# after-answer",
        "table · stacked 2 cols",
        "Column: Alpha",
        "Value: 1",
        "code · typescript",
        "const answer = 42",
        "Done.",
        "",
      ].join("\n"),
    )
    await expect(evaluateMaintenanceWrapperMarkdownProjection(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing cues and body content", async () => {
    const dir = await writeCase(["# after-answer", "Done.", ""].join("\n"))
    const anomalies = await evaluateMaintenanceWrapperMarkdownProjection(dir)
    expect(anomalies.map((item) => item.id)).toContain("missing-stacked-table-cue")
    expect(anomalies.map((item) => item.id)).toContain("missing-stacked-table-content")
    expect(anomalies.map((item) => item.id)).toContain("missing-code-language-cue")
    await rm(dir, { recursive: true, force: true })
  })

  it("flags duplicated raw markdown headings after rich projection", async () => {
    const dir = await writeCase(
      [
        "# after-answer",
        "# Projection",
        "table · stacked 2 cols",
        "Column: Alpha",
        "Value: 1",
        "code · typescript",
        "const answer = 42",
        "Done.",
        "### Projection",
        "",
      ].join("\n"),
    )
    const anomalies = await evaluateMaintenanceWrapperMarkdownProjection(dir)
    expect(anomalies.map((item) => item.id)).toContain("duplicate-projection-heading")
    await rm(dir, { recursive: true, force: true })
  })
})
