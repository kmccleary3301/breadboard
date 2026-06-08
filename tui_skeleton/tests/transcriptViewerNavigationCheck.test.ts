import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateTranscriptViewerNavigation } from "../tools/assertions/transcriptViewerNavigationCheck.ts"

const writeCase = async (snapshots: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-transcript-nav-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  return dir
}

describe("evaluateTranscriptViewerNavigation", () => {
  it("accepts follow/top/tail transcript viewer navigation evidence", async () => {
    const dir = await writeCase(
      [
        "# viewer-follow",
        "follow tail · g top",
        "# viewer-search",
        "Search: ello",
        "# viewer-top",
        "inspect 1-10/30 · G tail",
        "# viewer-user-anchor",
        "Hello transcript",
        "# viewer-assistant-anchor",
        "Mock assistant: hello world",
        "# viewer-tail",
        "follow tail · g top",
        "# back",
        "ctrl+o transcript",
        "# viewer-reopen",
        "inspect 20-30/30",
        "",
      ].join("\n"),
    )
    await expect(evaluateTranscriptViewerNavigation(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing inspect or return-to-tail evidence", async () => {
    const dir = await writeCase(
      [
        "# viewer-follow",
        "# viewer-search",
        "# viewer-top",
        "# viewer-user-anchor",
        "# viewer-assistant-anchor",
        "# viewer-tail",
        "breadboard transcript viewer",
        "# back",
        "footer",
        "# viewer-reopen",
        "follow tail",
        "",
      ].join("\n"),
    )
    const anomalies = await evaluateTranscriptViewerNavigation(dir)
    expect(anomalies.map((item) => item.id)).toContain("initial-follow-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("search-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("top-contract-missing")
    expect(anomalies.map((item) => item.id)).toContain("user-anchor-missing")
    expect(anomalies.map((item) => item.id)).toContain("assistant-anchor-missing")
    expect(anomalies.map((item) => item.id)).toContain("tail-return-missing")
    expect(anomalies.map((item) => item.id)).toContain("footer-transcript-hint-missing")
    expect(anomalies.map((item) => item.id)).toContain("reopen-position-memory-missing")
    await rm(dir, { recursive: true, force: true })
  })
})
