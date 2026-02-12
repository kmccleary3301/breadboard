import { describe, it, expect } from "vitest"
import { computeInlineDiffSpans, shiftSpans } from "../inlineDiff.js"

const spanText = (value: string, spans: { start: number; end: number }[]): string[] =>
  spans.map((span) => value.slice(span.start, span.end))

const normalize = (value: string): string => value.replace(/\s+/g, "")

describe("computeInlineDiffSpans", () => {
  it("highlights changed words inside a line", () => {
    const before = 'printf("hello world\\n");'
    const after = 'printf("goodbye world\\n");'
    const { del, add } = computeInlineDiffSpans(before, after)
    const delText = spanText(before, del).join("")
    const addText = spanText(after, add).join("")
    expect(delText).toContain("hello")
    expect(delText).not.toContain("world")
    expect(addText).toContain("goodbye")
    expect(addText).not.toContain("world")
  })

  it("highlights numeric changes without touching surrounding punctuation", () => {
    const before = "foo = 1;"
    const after = "foo = 2;"
    const { del, add } = computeInlineDiffSpans(before, after)
    const delText = normalize(spanText(before, del).join(""))
    const addText = normalize(spanText(after, add).join(""))
    expect(delText).toContain("1")
    expect(addText).toContain("2")
    expect(delText).not.toContain(";")
    expect(addText).not.toContain(";")
  })

  it("ignores whitespace-only changes", () => {
    const before = "foo  bar"
    const after = "foo bar"
    const { del, add } = computeInlineDiffSpans(before, after)
    expect(del).toHaveLength(0)
    expect(add).toHaveLength(0)
  })

  it("handles insertions while leaving shared tokens untouched", () => {
    const before = "const x = 1;"
    const after = "const x = 1 + 2;"
    const { add } = computeInlineDiffSpans(before, after)
    const addText = normalize(spanText(after, add).join(""))
    expect(addText).toContain("+2")
    expect(addText).not.toContain("const")
  })

  it("handles deletions in the middle of a line", () => {
    const before = "return foo(bar);"
    const after = "return bar;"
    const { del } = computeInlineDiffSpans(before, after)
    const delText = spanText(before, del).join("")
    expect(delText).toContain("foo")
    expect(delText).not.toContain("return")
  })

  it("handles repeated tokens without over-highlighting", () => {
    const before = "foo bar foo"
    const after = "foo baz foo"
    const { del, add } = computeInlineDiffSpans(before, after)
    const delText = normalize(spanText(before, del).join(""))
    const addText = normalize(spanText(after, add).join(""))
    expect(delText).toBe("bar")
    expect(addText).toBe("baz")
  })

  it("highlights filename suffix changes", () => {
    const before = "path/to/file.ts"
    const after = "path/to/file.test.ts"
    const { add } = computeInlineDiffSpans(before, after)
    const addText = spanText(after, add).join("")
    expect(addText).toContain("test")
    expect(addText).not.toContain("path/to")
  })

  it("handles underscores as part of word tokens", () => {
    const before = "foo_bar = 1"
    const after = "foo_baz = 1"
    const { del, add } = computeInlineDiffSpans(before, after)
    const delText = normalize(spanText(before, del).join(""))
    const addText = normalize(spanText(after, add).join(""))
    expect(delText).toContain("foo_bar")
    expect(addText).toContain("foo_baz")
  })

  it("handles whole-line replacement when other side is empty", () => {
    const before = ""
    const after = "new line"
    const { del, add } = computeInlineDiffSpans(before, after)
    expect(del).toHaveLength(0)
    expect(spanText(after, add).join("")).toBe("new line")
  })

  it("supports non-latin text tokens", () => {
    const before = "こんにちは世界"
    const after = "こんにちは友達"
    const { del, add } = computeInlineDiffSpans(before, after)
    const delText = spanText(before, del).join("")
    const addText = spanText(after, add).join("")
    expect(delText).toContain("世界")
    expect(addText).toContain("友達")
  })

  it("returns empty spans for identical strings", () => {
    const value = "no change here"
    const { del, add } = computeInlineDiffSpans(value, value)
    expect(del).toHaveLength(0)
    expect(add).toHaveLength(0)
  })
})

describe("shiftSpans", () => {
  it("shifts spans by prefix length", () => {
    const before = 'printf("hello world\\n");'
    const after = 'printf("goodbye world\\n");'
    const { del } = computeInlineDiffSpans(before, after)
    const prefix = "2 - "
    const shifted = shiftSpans(del, prefix.length)
    const combined = `${prefix}${before}`
    const shiftedText = spanText(combined, shifted).join("")
    expect(shiftedText).toContain("hello")
    expect(shiftedText).not.toContain(prefix.trim())
  })
})
