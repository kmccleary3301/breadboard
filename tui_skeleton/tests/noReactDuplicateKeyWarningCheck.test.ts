import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateNoReactDuplicateKeyWarning } from "../tools/assertions/noReactDuplicateKeyWarningCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-dupkey-check-"))
  tempDirs.push(dir)
  return dir
}
afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("noReactDuplicateKeyWarningCheck", () => {
  it("passes when warning text is absent", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "pty_plain.txt"), "ok\n", "utf8")
    await expect(evaluateNoReactDuplicateKeyWarning(dir)).resolves.toEqual([])
  })

  it("flags duplicate key warning text", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "pty_plain.txt"), "Warning: Encountered two children with the same key\n", "utf8")
    await expect(evaluateNoReactDuplicateKeyWarning(dir)).resolves.toEqual([
      { id: "react-duplicate-key-warning", message: "Detected duplicate React child key warning in pty_plain.txt." },
    ])
  })
})
