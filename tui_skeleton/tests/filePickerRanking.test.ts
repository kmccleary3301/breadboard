import { describe, it, expect } from "vitest"
import { rankFuzzyFileItems } from "../src/repl/fileRanking.js"
import type { SessionFileInfo } from "../src/api/types.js"

describe("file picker fuzzy ranking", () => {
  it("prioritizes basename prefix matches", () => {
    const items: SessionFileInfo[] = [
      { path: "docs/readme.md", type: "file" },
      { path: "README.md", type: "file" },
      { path: "src/readme_helper.ts", type: "file" },
      { path: "readme", type: "directory" },
    ]

    const ranked = rankFuzzyFileItems(items, "read", 10, (item) => item.path)

    expect(ranked[0]?.path).toBe("README.md")
  })
})
