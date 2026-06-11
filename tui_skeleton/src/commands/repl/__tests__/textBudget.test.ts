import { describe, expect, it } from "vitest"
import {
  applyTextBudget,
  clampTextByChars,
  clampTextByLines,
  formatOneLinePreview,
  normalizeLineEndings,
} from "../textBudget.js"

describe("textBudget", () => {
  it("normalizes CRLF and clamps visible lines with an optional omission marker", () => {
    expect(normalizeLineEndings("a\r\nb\rc")).toBe("a\nb\nc")
    const result = clampTextByLines("alpha\r\nbeta\ngamma", 2, { omissionText: "…" })
    expect(result).toEqual({ text: "alpha\nbeta…", truncated: true })
  })

  it("clamps by characters without exceeding the requested budget", () => {
    expect(clampTextByChars("abcdef", 5)).toEqual({ text: "abcd…", truncated: true })
    expect(clampTextByChars("abcdef", 5, { ellipsis: "..." })).toEqual({ text: "ab...", truncated: true })
    expect(clampTextByChars("abcdef", 1, { ellipsis: "..." })).toEqual({ text: ".", truncated: true })
  })

  it("combines line and character budgets", () => {
    const result = applyTextBudget("one\ntwo\nthree", { maxLines: 2, maxChars: 7, omissionText: "…" })
    expect(result.text.length).toBeLessThanOrEqual(7)
    expect(result.truncated).toBe(true)
  })

  it("formats one-line previews by stripping ansi/control characters and collapsing whitespace", () => {
    const result = formatOneLinePreview("\u001b[31mLOUD\u001b[0m\u0007\nvalue   next", { maxChars: 12, ellipsis: "..." })
    expect(result).toEqual({ text: "LOUD valu...", truncated: true })
    expect(result.text).not.toContain("\u001b")
    expect(result.text).not.toContain("\u0007")
  })

  it("uses a fallback for empty previews", () => {
    expect(formatOneLinePreview("\u0007\n\t", { fallback: "task", maxChars: 20 }).text).toBe("task")
  })
})
