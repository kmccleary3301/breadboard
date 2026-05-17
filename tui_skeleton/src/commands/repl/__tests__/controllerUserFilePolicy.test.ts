import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { listFiles, readFile } from "../controllerUserMethods.js"

const makeContext = (workspace: string) => ({
  sessionId: null,
  config: { workspace },
})

describe("controller local file policy", () => {
  it("keeps local file listing and reads inside the configured workspace", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "bb-file-policy-"))
    try {
      const context = makeContext(dir)
      await expect(listFiles.call(context, "../")).rejects.toThrow(/outside workspace/)
      await expect(readFile.call(context, "../secret.txt")).rejects.toThrow(/outside workspace/)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it("returns bounded snippets for large local text reads", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "bb-file-policy-"))
    try {
      await writeFile(path.join(dir, "large.txt"), ["head", "middle-a", "middle-b", "tail"].join("\n"))
      const result = await readFile.call(makeContext(dir), "large.txt", {
        mode: "snippet",
        headLines: 1,
        tailLines: 1,
      })
      expect(result.path).toBe("large.txt")
      expect(result.truncated).toBe(true)
      expect(result.content).toContain("head")
      expect(result.content).toContain("tail")
      expect(result.content).not.toContain("middle-a")
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it("fails closed for local binary files instead of returning replacement-character text", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "bb-file-policy-"))
    try {
      await writeFile(path.join(dir, "asset.bin"), Buffer.from([0x00, 0x01, 0x02, 0x03, 0x41]))
      await expect(readFile.call(makeContext(dir), "asset.bin", { mode: "cat" })).rejects.toThrow(
        /Binary file cannot be attached as text context/,
      )
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it("lists hidden directories only as explicit tree/listing entries, leaving fuzzy indexing policy to the picker", async () => {
    const dir = await mkdtemp(path.join(os.tmpdir(), "bb-file-policy-"))
    try {
      await mkdir(path.join(dir, ".git"))
      await writeFile(path.join(dir, ".git", "config"), "ignored by fuzzy picker\n")
      const entries = await listFiles.call(makeContext(dir), ".")
      expect(entries.some((entry) => entry.path === ".git" && entry.type === "directory")).toBe(true)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
