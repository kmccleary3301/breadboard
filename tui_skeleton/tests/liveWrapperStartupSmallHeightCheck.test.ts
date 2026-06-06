import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperStartupSmallHeight } from "../tools/assertions/liveWrapperStartupSmallHeightCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-live-startup-smallheight-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperStartupSmallHeightCheck", () => {
  it("passes when constrained-height startup renders the compact orientation surface cleanly", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# startup-smallheight",
        "BreadBoard · Claude Code",
        "gpt-5.4-mini   ·   /repo",
        "❯ Try \"refactor <filepath>\"",
        "[ready]",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      JSON.stringify({ pendingResponse: false, landingVariant: "compact", landingRetired: true, warmLandingVisible: false }) + "\n",
      "utf8",
    )

    await expect(evaluateLiveWrapperStartupSmallHeight(dir)).resolves.toEqual([])
  })
})
