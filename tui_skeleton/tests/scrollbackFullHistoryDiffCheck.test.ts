import { mkdir, mkdtemp, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import { evaluateScrollbackFullHistoryDiff } from "../tools/assertions/scrollbackFullHistoryDiffCheck.ts"

const makeCase = async (scrollback: string, extraFiles: Record<string, string> = {}): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-full-history-diff-"))
  await mkdir(path.join(dir, "terminal_text"), { recursive: true })
  await mkdir(path.join(dir, "observer_text"), { recursive: true })
  await writeFile(path.join(dir, "terminal_text", "scrollback_final.txt"), scrollback, "utf8")
  await writeFile(path.join(dir, "app_start_anchor.txt"), '{"mode":"preserved-scrollback","preAppHistoryPolicy":"untouched"}\n', "utf8")
  for (const [name, body] of Object.entries(extraFiles)) {
    await writeFile(path.join(dir, "observer_text", name), body, "utf8")
  }
  return dir
}

const validScrollback = [
  "BB_PRE_SHORT_ALPHA",
  "BB_PRE_SHORT_BETA",
  "BB_PRE_LONG_GAMMA_BEGIN abcdefghijklmnopqrstuvwxyz BB_PRE_LONG_GAMMA_END",
  "BB_PRE_FINAL_ZETA",
  "BreadBoard · Claude Code",
  "❯ Write a first prompt",
  "## Streaming",
  "- first item",
  "❯ Write a second prompt",
  "## Second Turn",
].join("\n")

const sentinels = ["BB_PRE_SHORT_ALPHA", "BB_PRE_SHORT_BETA", "BB_PRE_LONG_GAMMA_BEGIN", "BB_PRE_FINAL_ZETA"]
const promptMarkers = ["Write a first prompt", "Write a second prompt"]

describe("scrollbackFullHistoryDiffCheck", () => {
  it("passes preserved sentinels, landing, and prompt markers", async () => {
    const dir = await makeCase(validScrollback)
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("pass")
    expect(report.findings).toEqual([])
  })

  it("fails missing pre-app sentinels", async () => {
    const dir = await makeCase(validScrollback.replace("BB_PRE_SHORT_BETA\n", ""))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-001")
  })

  it("fails reordered sentinels", async () => {
    const dir = await makeCase(validScrollback.replace("BB_PRE_SHORT_ALPHA\nBB_PRE_SHORT_BETA", "BB_PRE_SHORT_BETA\nBB_PRE_SHORT_ALPHA"))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-002")
  })

  it("fails duplicated sentinels", async () => {
    const dir = await makeCase(validScrollback.replace("BB_PRE_SHORT_ALPHA", "BB_PRE_SHORT_ALPHA\nBB_PRE_SHORT_ALPHA"))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-006")
  })

  it("fails app output before the pre-app boundary", async () => {
    const dir = await makeCase(validScrollback.replace("BB_PRE_SHORT_BETA", "BreadBoard · Claude Code\nBB_PRE_SHORT_BETA"))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-003")
  })

  it("fails duplicated landing markers", async () => {
    const dir = await makeCase(validScrollback.replace("BreadBoard · Claude Code", "BreadBoard · Claude Code\nBreadBoard · Claude Code"))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-004")
  })

  it("fails duplicated prompt markers", async () => {
    const dir = await makeCase(validScrollback.replace("❯ Write a second prompt", "❯ Write a second prompt\n❯ Write a second prompt"))
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers })
    expect(report.verdict).toBe("fail")
    expect(report.findings.map((finding) => finding.id)).toContain("SCR-HIST-005")
  })

  it("can use Ghostty observer exports when terminal scrollback lacks sentinel lines", async () => {
    const dir = await makeCase("BreadBoard · Claude Code\n❯ Write a first prompt\n", {
      "final.ghostty_scrollback.txt": validScrollback,
    })
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers: ["Write a first prompt"] })
    expect(report.verdict).toBe("pass")
    expect(report.artifactsConsumed).toContain("observer_text/final.ghostty_scrollback.txt")
  })

  it("combines Ghostty native scrollback and screen exports before judging durable history", async () => {
    const dir = await makeCase("BB_PRE_SHORT_ALPHA\n", {
      "final.ghostty_scrollback.txt": "BB_PRE_SHORT_ALPHA\n",
      "final.ghostty_screen.txt": [
        "BB_PRE_SHORT_ALPHA",
        "BB_PRE_SHORT_BETA",
        "BB_PRE_LONG_GAMMA_BEGIN abcdefghijklmnopqrstuvwxyz BB_PRE_LONG_GAMMA_END",
        "BB_PRE_FINAL_ZETA",
        "BreadBoard · Claude Code",
        "❯ Write a first prompt",
        "## Streaming",
      ].join("\n"),
    })
    const report = await evaluateScrollbackFullHistoryDiff(dir, { expectedSentinels: sentinels, promptMarkers: ["Write a first prompt"] })
    expect(report.verdict).toBe("pass")
    expect(report.artifactsConsumed).toContain("observer_text/final.ghostty_combined_state.txt")
  })
})
