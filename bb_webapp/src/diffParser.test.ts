import { describe, expect, it } from "vitest"
import { extractDiffPayload, parseUnifiedDiff } from "./diffParser"

describe("diffParser", () => {
  it("extracts diff payload from known keys", () => {
    expect(extractDiffPayload({ diff: "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b" })?.diffText).toContain("@@")
    expect(extractDiffPayload({ text: "no-diff" })).toBeNull()
  })

  it("parses unified diff with file/hunk stats", () => {
    const parsed = parseUnifiedDiff("--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n-a\n+b\n c")
    expect(parsed.files).toHaveLength(1)
    expect(parsed.files[0].displayPath).toBe("a.txt")
    expect(parsed.files[0].additions).toBe(1)
    expect(parsed.files[0].removals).toBe(1)
    expect(parsed.malformed).toBe(false)
  })

  it("falls back safely on malformed input", () => {
    const parsed = parseUnifiedDiff("not-a-diff")
    expect(parsed.files[0].displayPath).toBe("raw")
    expect(parsed.malformed).toBe(true)
  })

  it("truncates very large diffs deterministically", () => {
    const lines = ["--- a/a.txt", "+++ b/a.txt", "@@ -1,1 +1,1 @@"]
    for (let index = 0; index < 5000; index += 1) {
      lines.push(index % 2 === 0 ? `+line-${index}` : ` line-${index}`)
    }
    const parsed = parseUnifiedDiff(lines.join("\n"), 1000)
    expect(parsed.truncated).toBe(true)
    expect(parsed.files.length).toBeGreaterThan(0)
  })
})
