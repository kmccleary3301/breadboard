import { execFileSync } from "node:child_process"
import { existsSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { copyWorkingTreePatch, exportWorkingTreePatch, readWorkingTreeDiff } from "../workingTreeDiff.js"

const git = (cwd: string, args: readonly string[]) => {
  execFileSync("git", [...args], { cwd, stdio: "pipe" })
}

const makeRepo = () => {
  const dir = mkdtempSync(path.join(tmpdir(), "bb-working-tree-diff-"))
  git(dir, ["init"])
  git(dir, ["config", "user.email", "breadboard@example.test"])
  git(dir, ["config", "user.name", "BreadBoard Test"])
  writeFileSync(path.join(dir, "main.ts"), "export const value = 1\n")
  git(dir, ["add", "main.ts"])
  git(dir, ["commit", "-m", "initial"])
  return dir
}

const canonicalPath = (value: string): string => path.normalize(realpathSync.native(value))

describe("readWorkingTreeDiff", () => {
  it("returns clean status for a clean git workspace", async () => {
    const dir = makeRepo()
    const result = await readWorkingTreeDiff(dir)
    expect(result.kind).toBe("clean")
    expect(result.changedFiles).toEqual([])
    expect(result.patch).toBe("")
  })

  it("summarizes tracked and untracked changes without mutating the workspace", async () => {
    const dir = makeRepo()
    writeFileSync(path.join(dir, "main.ts"), "export const value = 2\nexport const label = 'changed'\n")
    writeFileSync(path.join(dir, "notes.txt"), "untracked\n")

    const result = await readWorkingTreeDiff(dir)

    expect(result.kind).toBe("dirty")
    expect(result.repoRoot).toBeDefined()
    expect(canonicalPath(result.repoRoot!)).toBe(canonicalPath(dir))
    expect(result.changedFiles.map((file) => file.path)).toContain("main.ts")
    expect(result.changedFiles.map((file) => file.path)).toContain("notes.txt")
    expect(result.untrackedCount).toBe(1)
    expect(result.additions).toBeGreaterThan(0)
    expect(result.deletions).toBeGreaterThan(0)
    expect(result.patch).toContain("diff --git a/main.ts b/main.ts")
    expect(result.patch).not.toContain("notes.txt")
    expect(result.warnings.join("\n")).toContain("untracked")
  })

  it("fails closed outside git repositories", async () => {
    const dir = mkdtempSync(path.join(tmpdir(), "bb-working-tree-non-git-"))
    const result = await readWorkingTreeDiff(dir)
    expect(result.kind).toBe("not-git")
    expect(result.warnings.join("\n")).toContain("not inside a git repository")
  })

  it("exports tracked patch inside the repository and rejects path traversal", async () => {
    const dir = makeRepo()
    writeFileSync(path.join(dir, "main.ts"), "export const value = 3\n")

    const exported = await exportWorkingTreePatch(dir, ".breadboard/review.patch")
    expect(exported.ok).toBe(true)
    expect(existsSync(exported.path!)).toBe(true)
    expect(canonicalPath(exported.path!)).toBe(canonicalPath(path.join(dir, ".breadboard", "review.patch")))
    expect(readFileSync(exported.path!, "utf8")).toContain("diff --git a/main.ts b/main.ts")

    const outside = await exportWorkingTreePatch(dir, "../outside.patch")
    expect(outside.ok).toBe(false)
    expect(outside.error).toContain("outside")
  })

  it("copies tracked patch through the fake clipboard write sink", async () => {
    const dir = makeRepo()
    writeFileSync(path.join(dir, "main.ts"), "export const value = 4\n")
    const sink = path.join(dir, ".breadboard", "clipboard.txt")
    process.env.BREADBOARD_FAKE_CLIPBOARD_WRITE_PATH = sink
    try {
      const copied = await copyWorkingTreePatch(dir)
      expect(copied.ok).toBe(true)
      expect(copied.method).toBe("fake-file")
      expect(readFileSync(sink, "utf8")).toContain("diff --git a/main.ts b/main.ts")
    } finally {
      delete process.env.BREADBOARD_FAKE_CLIPBOARD_WRITE_PATH
    }
  })
})
