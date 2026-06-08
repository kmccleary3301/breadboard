import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { evaluateUiChromeBanlist } from "../tools/assertions/uiChromeBanlistCheck.ts"

const tempDirs: string[] = []

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })))
})

const makeCase = async (body: string) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-ui-banlist-"))
  tempDirs.push(dir)
  await fs.mkdir(path.join(dir, "terminal_text"), { recursive: true })
  await fs.writeFile(path.join(dir, "terminal_text", "visible_final.txt"), body, "utf8")
  return dir
}

describe("uiChromeBanlistCheck", () => {
  it("accepts product-facing Live Shell copy", async () => {
    const dir = await makeCase("Live Shell · Ready · Ctrl+O Transcript\n")
    await expect(evaluateUiChromeBanlist(dir)).resolves.toEqual([])
  })

  it("flags diagnostic runtime vocabulary in user-facing captures", async () => {
    const dir = await makeCase("scene owned runtime host · S1 prototype · owned scene settled\n")
    const anomalies = await evaluateUiChromeBanlist(dir)
    expect(anomalies.map((entry) => entry.id)).toContain("ui-chrome-banned-scene-owned")
    expect(anomalies.map((entry) => entry.id)).toContain("ui-chrome-banned-owned-scene")
    expect(anomalies.map((entry) => entry.id)).toContain("ui-chrome-banned-s1-prototype")
    expect(anomalies.map((entry) => entry.id)).toContain("ui-chrome-banned-runtime-host")
  })
})
