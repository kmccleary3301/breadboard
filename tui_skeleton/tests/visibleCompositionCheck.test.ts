import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { evaluateVisibleComposition } from "../tools/assertions/visibleCompositionCheck.ts"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })))
})

const makeCase = async (body: string, name = "after-width-shrink.ghostty_screen.txt") => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-visible-composition-"))
  tempDirs.push(dir)
  await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
  await fs.writeFile(path.join(dir, "observer_text", name), body, "utf8")
  return dir
}

describe("visibleCompositionCheck", () => {
  it("flags duplicate completion status rows", async () => {
    const dir = await makeCase("Run finished.\nRun finished.\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-run-finished")
  })

  it("flags duplicate idle composer prompts", async () => {
    const dir = await makeCase('❯ Try "refactor <filepath>"\n❯ Try "refactor <filepath>"\n')
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-idle-prompt")
  })

  it("flags duplicate bare composer prompts", async () => {
    const dir = await makeCase("❯\n❯\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-bare-prompt")
  })

  it("flags duplicate placeholder composer prompts", async () => {
    const dir = await makeCase("❯   Type your request…\n❯   Type your request…\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-composer-prompt")
  })

  it("flags duplicate footer phase rows", async () => {
    const dir = await makeCase("## Answer\n• [ready] last 6s · enter send\n• [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-footer-phase")
  })

  it("flags duplicate footer phase tokens on one row", async () => {
    const dir = await makeCase("## Answer\n• [ready] last 6s · enter send  • [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-footer-phase")
  })

  it("flags adjacent duplicate footer phase tokens on one row", async () => {
    const dir = await makeCase("## Answer\nold text• [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).not.toContain("visible-composition-duplicate-footer-phase")

    const duplicateDir = await makeCase("## Answer\nold text• [ready] last 6s · enter send• [ready] last 6s · enter send\n")
    const duplicateAnomalies = await evaluateVisibleComposition(duplicateDir)
    expect(duplicateAnomalies.map((entry) => entry.id)).toContain("visible-composition-duplicate-footer-phase")
  })

  it("flags stale right-side footer summary tails left behind on phase rows", async () => {
    const dir = await makeCase("## Answer\n• [ready] last 6s · enter send                                                               resume /sessions · ctrl+o transcript\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-footer-phase-stale-summary-tail")
  })

  it("allows intentionally adjacent compact footer text without a stale wide gap", async () => {
    const dir = await makeCase("## Answer\n• [ready] last 6s · enter send  resume /sessions · ctrl+o transcript\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).not.toContain("visible-composition-footer-phase-stale-summary-tail")
  })

  it("flags footer phase rows that drift horizontally away from the left edge", async () => {
    const dir = await makeCase("## Answer\n                                                                       • [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-footer-phase-horizontal-drift")
  })

  it("allows small intentional indentation before footer phase rows", async () => {
    const dir = await makeCase("## Answer\n  • [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).not.toContain("visible-composition-footer-phase-horizontal-drift")
  })

  it("flags leaked bracketed paste markers", async () => {
    const dir = await makeCase("before\n[200~pasted body[201~\nafter\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-bracketed-paste-marker")
  })

  it("flags separator collisions with prompts or status content", async () => {
    const dir = await makeCase("────────────────────────────❯ Try \"refactor <filepath>\"\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-separator-collision")
  })

  it("flags clipped landing fragments before the first prompt", async () => {
    const dir = await makeCase("                                          │\n╰──────────────────────────╯\n❯ hello\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-clipped-landing-fragment")
  })

  it("flags clipped landing fragments before markdown content even when no prompt is visible", async () => {
    const dir = await makeCase("PRE_APP\n╭── BreadBoard v0.2.0 ─────────\n# Streaming\n- first item\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-clipped-landing-fragment")
  })

  it("flags clipped split landing fragments without box borders", async () => {
    const dir = await makeCase(" ░█▄▄ █▀█ █▀▀ ▄▀█ █▀▄░░░░░    BreadBoard v0.2.0\n ░█▄█ █▀▄ ██▄ █▀█ █▄▀░░░░░    Using Config `Codex`\n# Streaming\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-clipped-split-landing")
  })

  it("allows the complete compact split landing before startup composer", async () => {
    const dir = await makeCase("PRE_APP_ALPHA\nPRE_APP_BETA\n ░█▄▄ █▀█ █▀▀ ▄▀█ █▀▄░░░░░    BreadBoard v0.2.0\n ░█▄█ █▀▄ ██▄ █▀█ █▄▀░░░░░    Using Config `Codex`\n ░░░░░█▄▄ █▀█ ▄▀█ █▀█ █▀▄░    dev · Codex\n ░░░░░█▄█ █▄█ █▀█ █▀▄ █▄▀░    /shared_folders/project\n\n ❯   Type your request…\n\n • [ready] enter send\n", "startup.txt")
    await expect(evaluateVisibleComposition(dir)).resolves.toEqual([])
  })

  it("flags horizontal collisions between table or markdown fragments", async () => {
    const dir = await makeCase("├──────┼─────────┤                                                                           │ key  │  value  │\n- first item                                                                                 - second item\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-horizontal-content-collision")
  })

  it("flags markdown tables interrupted by the composer before the table closes", async () => {
    const dir = await makeCase("┌──────┬─────────┐\n│ key  │  value  │\n├──────┼─────────┤\n❯\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-interrupted-table-before-composer")
  })

  it("flags excessive blank rows immediately before the footer", async () => {
    const dir = await makeCase("## Answer\n- item\n\n\n• [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-excessive-blank-gap-before-footer")
  })

  it("flags excessive blank rows immediately before a visible composer prompt", async () => {
    const dir = await makeCase("## Answer\n- item\n\n\n❯\n\n• [ready] last 6s · enter send\n")
    const anomalies = await evaluateVisibleComposition(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("visible-composition-excessive-blank-gap-before-composer")
  })

  it("allows a single intentional blank row before the footer", async () => {
    const dir = await makeCase("## Answer\n- item\n\n• [ready] last 6s · enter send\n")
    await expect(evaluateVisibleComposition(dir)).resolves.toEqual([])
  })

  it("allows a visible composer prompt after one intentional blank row", async () => {
    const dir = await makeCase("## Answer\n- item\n\n❯\n\n• [ready] last 6s · enter send\n")
    await expect(evaluateVisibleComposition(dir)).resolves.toEqual([])
  })

  it("allows two intentional startup landing spacer rows before the initial composer", async () => {
    const dir = await makeCase("░█▄▄ BreadBoard v0.2.0\n░█▄█ Using Config `Codex`\ngpt-5.4-mini · Codex\n/shared_folders/example\n\n\n❯   Type your request…\n\n• [ready] enter send\n")
    await expect(evaluateVisibleComposition(dir)).resolves.toEqual([])
  })

  it("accepts intact landing followed by one completion row", async () => {
    const dir = await makeCase("╭── BreadBoard v0.2.0 ──╮\n│ Ready                 │\n╰───────────────────────╯\n❯ hello\nRun finished.\n")
    await expect(evaluateVisibleComposition(dir)).resolves.toEqual([])
  })
})
