import { describe, expect, it } from "vitest"
import { hasUnsafeMarkdownContent } from "./markdownSecurity"

describe("hasUnsafeMarkdownContent", () => {
  it("allows normal markdown content", () => {
    expect(hasUnsafeMarkdownContent("Hello **world**.\n[docs](https://example.com)\n")).toBe(false)
  })

  it("flags inline script tags", () => {
    expect(hasUnsafeMarkdownContent("Safe text <script>alert(1)</script>")).toBe(true)
  })

  it("flags javascript links", () => {
    expect(hasUnsafeMarkdownContent("[click me](javascript:alert(1))")).toBe(true)
  })

  it("flags iframe tags", () => {
    expect(hasUnsafeMarkdownContent("<iframe src=\"https://example.com\"></iframe>")).toBe(true)
  })

  it("flags html event handler attributes", () => {
    expect(hasUnsafeMarkdownContent('<img src="x" onerror="alert(1)" />')).toBe(true)
  })
})
