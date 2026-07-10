import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperEmulatorSmoke } from "../tools/assertions/liveWrapperEmulatorSmokeCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-emulator-smoke-check-"))
  tempDirs.push(dir)
  await mkdir(path.join(dir, "observer_text"), { recursive: true })
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperEmulatorSmokeCheck", () => {
  it("passes when the emulator observer captures the expected settled answer", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "observer_text", "after-answer.txt"), "two\n", "utf8")
    await writeFile(path.join(dir, "emulator_snapshots.txt"), "# after-submit\nfoo\n\n# after-answer\ntwo\n", "utf8")
    await writeFile(path.join(dir, "emulator_metadata.json"), JSON.stringify({ display: ":99", paneId: "1" }) + "\n", "utf8")
    await expect(evaluateLiveWrapperEmulatorSmoke(dir)).resolves.toEqual([])
  })
})
