import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateMermaidFallback } from "../tools/assertions/mermaidFallbackCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-mermaid-check-"))
  tempDirs.push(dir)
  return dir
}
afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("mermaidFallbackCheck", () => {
  it("passes when fallback fence and body remain visible without crash signature", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "pty_snapshots.txt"), "# mdx-render\n```mermaid\ngraph TD\nA-->B\n```\n- done\n", "utf8")
    await expect(evaluateMermaidFallback(dir)).resolves.toEqual([])
  })

  it("flags missing fence/body and crash signatures", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "pty_snapshots.txt"), "# mdx-render\nboom\n", "utf8")
    await writeFile(path.join(dir, "pty_plain.txt"), "Language `mermaid` not found\n", "utf8")
    await expect(evaluateMermaidFallback(dir)).resolves.toEqual([
      { id: "mermaid-body-missing", message: "Expected mermaid body lines to remain visible in fallback rendering." },
      { id: "mermaid-crash-signature", message: "Detected mermaid crash signature: Language `mermaid` not found" },
    ])
  })
})
