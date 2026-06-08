import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperToolSmoke } from "../tools/assertions/liveWrapperToolSmokeCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-live-tool-check-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperToolSmokeCheck", () => {
  it("passes when the live wrapper tool row renders the richer shell contract", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# tool-first-result",
        "● Tool",
        "  │ stdout 1 line",
        "  └ /tmp/project",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "repl_state.ndjson"),
      JSON.stringify({ state: { lastToolEvent: { text: "Tool\n│ stdout 1 line\n└ /tmp/project" } } }) + "\n",
      "utf8",
    )
    await expect(evaluateLiveWrapperToolSmoke(dir)).resolves.toEqual([])
  })
})
