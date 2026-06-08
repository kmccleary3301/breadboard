import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync } from "node:fs"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateLiveWrapperEmulatorStartupSmallHeight } from "../tools/assertions/liveWrapperEmulatorStartupSmallHeightCheck.ts"

describe("liveWrapperEmulatorStartupSmallHeightCheck", () => {
  it("accepts compact constrained-height emulator startup", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-startup-smallheight-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.writeFile(
        path.join(dir, "observer_text", "startup-smallheight.txt"),
        [
          "BreadBoard · Claude Code",
          "❯ Try \"refactor <filepath>\"",
          "[ready] enter send",
        ].join("\n"),
        "utf8",
      )
      await fs.writeFile(
        path.join(dir, "surface_model.ndjson"),
        JSON.stringify({ pendingResponse: false, landingVariant: "compact", landingRetired: true, warmLandingVisible: false }) + "\n",
        "utf8",
      )
      await expect(evaluateLiveWrapperEmulatorStartupSmallHeight(dir)).resolves.toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("accepts Codex classic placeholder for constrained-height emulator startup", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-startup-smallheight-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.writeFile(
        path.join(dir, "observer_text", "startup-smallheight.txt"),
        [
          "BreadBoard v0.2.0",
          "Using Config `Codex`",
          "❯   Type your request…",
          "[ready] enter send",
        ].join("\n"),
        "utf8",
      )
      await fs.writeFile(
        path.join(dir, "surface_model.ndjson"),
        JSON.stringify({ pendingResponse: false, landingVariant: "split", landingRetired: true, warmLandingVisible: false }) + "\n",
        "utf8",
      )
      await expect(evaluateLiveWrapperEmulatorStartupSmallHeight(dir)).resolves.toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
