import { mkdtemp, mkdir, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import {
  compareWidthEquivalenceTexts,
  evaluateScrollbackWidthEquivalence,
  extractWidthEquivalenceRegion,
  normalizeWidthEquivalenceLine,
} from "../tools/assertions/scrollbackWidthEquivalenceCheck.ts"

const sample = [
  "PRE_APP_ALPHA",
  "PRE_APP_BETA",
  "BreadBoard · Claude Code",
  "gpt-5.4-mini   ·   /shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_tui_runtime_20260409   ·   s f4f7df42",
  "",
  "❯ Write a markdown answer",
  "",
  "## Streaming",
  "- first item",
  "- second item",
  "┌────┬───────┐",
  "│ key│ value │",
  "└────┴───────┘",
  "code · ts",
  "console.log('ghostty')",
  "❯",
].join("\n")

describe("scrollbackWidthEquivalenceCheck", () => {
  it("normalizes dynamic roots and session ids without changing semantic content", () => {
    expect(normalizeWidthEquivalenceLine("/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_tui_runtime_20260409   ·   s abcdef12   ")).toBe("$ROOT   ·   s $SESSION")
  })

  it("extracts owned region without pre-app sentinel lines", () => {
    const region = extractWidthEquivalenceRegion(sample, "owned-visible-region")
    expect(region[0]).toContain("BreadBoard")
    expect(region.join("\n")).not.toContain("PRE_APP_ALPHA")
  })

  it("passes equivalent captures after legitimate normalization", () => {
    const a = sample
    const b = sample.replace("s f4f7df42", "s abcdef12").replace("breadboard_repo_tui_runtime_20260409", "breadboard_repo_tui_runtime_20260409")
    const report = compareWidthEquivalenceTexts("equivalent", a, b, "owned-visible-region")
    expect(report.verdict).toBe("pass")
    expect(report.differences).toEqual([])
  })

  it("fails missing semantic lines", () => {
    const report = compareWidthEquivalenceTexts("missing-line", sample, sample.replace("- second item\n", ""), "owned-visible-region")
    expect(report.verdict).toBe("fail")
    expect(report.differences.some((entry) => entry.reason.includes("differ") || entry.reason.includes("missing"))).toBe(true)
  })

  it("fails duplicate prompts instead of normalizing them away", () => {
    const corrupted = `${sample}\n❯`
    const report = compareWidthEquivalenceTexts("duplicate-prompt", corrupted, sample, "owned-visible-region")
    expect(report.verdict).toBe("fail")
    expect(report.differences.some((entry) => entry.reason.includes("bare prompt"))).toBe(true)
  })

  it("fails duplicate landing headers instead of normalizing them away", () => {
    const corrupted = sample.replace("BreadBoard · Claude Code\n", "BreadBoard · Claude Code\nBreadBoard · Claude Code\n")
    const report = compareWidthEquivalenceTexts("duplicate-landing", corrupted, sample, "owned-visible-region")
    expect(report.verdict).toBe("fail")
    expect(report.differences.some((entry) => entry.reason.includes("landing/header"))).toBe(true)
  })

  it("fails interior blank gaps over budget", () => {
    const corrupted = sample.replace("## Streaming\n", "## Streaming\n\n\n")
    const report = compareWidthEquivalenceTexts("blank-gap", corrupted, sample, "owned-visible-region")
    expect(report.verdict).toBe("fail")
    expect(report.differences.some((entry) => entry.reason.includes("blank gap"))).toBe(true)
  })

  it("fails leaked bracketed paste markers", () => {
    const corrupted = sample.replace("Write a markdown answer", "[200~Write a markdown answer[201~")
    const report = compareWidthEquivalenceTexts("paste-marker", corrupted, sample, "owned-visible-region")
    expect(report.verdict).toBe("fail")
    expect(report.differences.some((entry) => entry.reason.includes("bracketed paste"))).toBe(true)
  })

  it("evaluates a pair-config against real files", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "bb-width-equivalence-"))
    const aCase = path.join(root, "wide")
    const bCase = path.join(root, "narrow")
    await mkdir(path.join(aCase, "observer_text"), { recursive: true })
    await mkdir(path.join(bCase, "observer_text"), { recursive: true })
    await writeFile(path.join(aCase, "observer_text", "after-width-shrink.ghostty_screen.txt"), sample, "utf8")
    await writeFile(path.join(bCase, "observer_text", "after-width-shrink.ghostty_screen.txt"), sample.replace("s f4f7df42", "s 1234abcd"), "utf8")
    const pairConfig = path.join(root, "pair.json")
    await writeFile(pairConfig, JSON.stringify({ id: "pair", scope: "owned-visible-region", pathA: { caseDir: aCase, checkpoint: "after-width-shrink" }, pathB: { caseDir: bCase, checkpoint: "after-width-shrink" } }), "utf8")

    const report = await evaluateScrollbackWidthEquivalence(pairConfig)
    expect(report.verdict).toBe("pass")
  })
})
