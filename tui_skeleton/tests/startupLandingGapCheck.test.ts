import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { evaluateStartupLandingGap } from "../tools/assertions/startupLandingGapCheck.ts"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })))
})

const makeCase = async (body: string, name = "startup.txt") => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-startup-gap-"))
  tempDirs.push(dir)
  await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
  await fs.writeFile(path.join(dir, "observer_text", name), body, "utf8")
  return dir
}

describe("startupLandingGapCheck", () => {
  it("accepts a real landing pane with visible composer", async () => {
    const dir = await makeCase("╭── BreadBoard v0.2.0 ──╮\n│ Tips for getting started │\n╰──────────────────────────╯\n❯ Try \"refactor <filepath>\"\n• [ready]\n")
    await expect(evaluateStartupLandingGap(dir)).resolves.toEqual([])
  })

  it("allows compact startup only for constrained-height captures", async () => {
    const dir = await makeCase("BreadBoard · repo · Ready\n❯ Try \"refactor <filepath>\"\n• [ready]\n", "startup-smallheight.txt")
    await expect(evaluateStartupLandingGap(dir)).resolves.toEqual([])
  })

  it("flags compact-only startup when height is not marked constrained", async () => {
    const dir = await makeCase("BreadBoard · repo · Ready\n❯ Try \"refactor <filepath>\"\n• [ready]\n")
    const anomalies = await evaluateStartupLandingGap(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("startup-rich-landing-missing")
  })

  it("flags missing orientation", async () => {
    const dir = await makeCase("❯ Try \"refactor <filepath>\"\n• [ready]\n")
    const anomalies = await evaluateStartupLandingGap(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("startup-orientation-missing")
  })

  it("flags missing composer", async () => {
    const dir = await makeCase("╭── BreadBoard v0.2.0 ──╮\n│ Tips for getting started │\n╰──────────────────────────╯\n")
    const anomalies = await evaluateStartupLandingGap(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("startup-composer-missing")
  })

  it("flags large blank gulf between landing and composer", async () => {
    const dir = await makeCase([
      "╭── BreadBoard v0.2.0 ──╮",
      "│ Tips for getting started │",
      "╰──────────────────────────╯",
      "",
      "",
      "",
      "",
      "❯ Try \"refactor <filepath>\"",
      "• [ready]",
    ].join("\n"))
    const anomalies = await evaluateStartupLandingGap(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("startup-gap-budget-exceeded")
  })
})
