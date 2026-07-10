import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateHiddenEntryAffordance } from "../tools/assertions/hiddenEntryAffordanceCheck.ts"

const makeCase = async (body: string): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-hidden-entry-"))
  await mkdir(path.join(dir, "terminal_text"), { recursive: true })
  await writeFile(path.join(dir, "terminal_text", "visible_final.txt"), body)
  return dir
}

describe("hiddenEntryAffordanceCheck", () => {
  it("rejects the old ambiguous hidden-entry cue", async () => {
    const dir = await makeCase("... 3 earlier entries hidden ...\n")
    try {
      const anomalies = await evaluateHiddenEntryAffordance(dir)
      expect(anomalies.map((item) => item.id)).toContain("hidden-entry-ambiguous-cue")
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it("accepts explicit Live Shell and Scrollback recovery cues", async () => {
    const dir = await makeCase("↑ 3 earlier messages · Ctrl+O Transcript\nScroll up for 2 earlier outputs · Ctrl+O Transcript for search\n")
    try {
      expect(await evaluateHiddenEntryAffordance(dir)).toEqual([])
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
