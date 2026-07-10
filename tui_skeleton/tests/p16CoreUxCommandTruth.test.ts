import { describe, expect, it } from "vitest"

import {
  buildCommandTruthMarkdown,
  buildFeatureDispositionMarkdown,
  buildP16CommandTruthReport,
} from "../scripts/p16_core_ux_command_truth.js"
import { SLASH_COMMAND_REGISTRY } from "../src/repl/slashCommandRegistry.js"

describe("P16 core UX command truth report", () => {
  it("classifies every slash command into a P16 disposition", () => {
    const report = buildP16CommandTruthReport("2026-06-07T00:00:00.000Z")
    expect(report.commandCount).toBe(SLASH_COMMAND_REGISTRY.length)
    expect(report.rows).toHaveLength(SLASH_COMMAND_REGISTRY.length)
    expect(report.dispositionCounts.implemented).toBeGreaterThan(0)
    expect(report.dispositionCounts.bounded).toBeGreaterThan(0)
    expect(report.dispositionCounts.deferred).toBe(2)
    expect(report.rows.find((row) => row.name === "/goal")).toMatchObject({
      disposition: "deferred",
      modelSubmissionPolicy: expect.stringContaining("fail-closed locally"),
    })
    expect(report.rows.find((row) => row.name === "/fork")).toMatchObject({
      disposition: "deferred",
      disabledReason: expect.stringContaining("session graph"),
    })
    expect(report.rows.find((row) => row.name === "/permissions")).toMatchObject({
      disposition: "bounded",
      notes: expect.stringContaining("read-only"),
    })
    expect(report.rows.find((row) => row.name === "/diff")).toMatchObject({
      disposition: "bounded",
      notes: expect.stringContaining("working-tree diff"),
    })
    expect(report.rows.find((row) => row.name === "/debug-config")).toMatchObject({
      disposition: "hidden",
      visibility: "debug",
    })
  })

  it("renders markdown matrices with command truth and warnings", () => {
    const report = buildP16CommandTruthReport("2026-06-07T00:00:00.000Z")
    const disposition = buildFeatureDispositionMarkdown(report)
    const truth = buildCommandTruthMarkdown(report)
    expect(disposition).toContain("# P16 Feature Disposition Matrix")
    expect(disposition).toContain("/goal")
    expect(disposition).toContain("alias /model collides with exact command")
    expect(truth).toContain("# P16 Command Truth Matrix")
    expect(truth).toContain("Local, debug-local, deferred, malformed, unavailable, and unknown slash commands")
    expect(truth).toContain("/copy")
  })
})
