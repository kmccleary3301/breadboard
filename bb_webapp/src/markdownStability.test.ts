import { describe, expect, it } from "vitest"
import { splitStableMarkdownForStreaming } from "./markdownStability"

describe("splitStableMarkdownForStreaming", () => {
  it("returns fully stable content for complete plain text", () => {
    const split = splitStableMarkdownForStreaming("hello world.\n")
    expect(split.stablePrefix).toBe("hello world.\n")
    expect(split.unstableTail).toBe("")
  })

  it("keeps an unterminated fenced block in unstable tail", () => {
    const input = "Before block.\n```ts\nconst x = 1;\n"
    const split = splitStableMarkdownForStreaming(input)
    expect(split.stablePrefix).toBe("Before block.\n")
    expect(split.unstableTail).toBe("```ts\nconst x = 1;\n")
  })

  it("keeps incomplete markdown links in unstable tail", () => {
    const input = "Summary line.\n[Open"
    const split = splitStableMarkdownForStreaming(input)
    expect(split.stablePrefix).toBe("Summary line.\n")
    expect(split.unstableTail).toBe("[Open")
  })

  it("keeps unmatched inline code in unstable tail", () => {
    const input = "Done sentence.\n`part"
    const split = splitStableMarkdownForStreaming(input)
    expect(split.stablePrefix).toBe("Done sentence.\n")
    expect(split.unstableTail).toBe("`part")
  })
})
