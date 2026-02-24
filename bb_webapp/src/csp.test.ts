import { describe, expect, it } from "vitest"
import indexHtml from "../index.html?raw"

describe("index CSP", () => {
  it("defines a content security policy meta tag", () => {
    expect(indexHtml).toContain('http-equiv="Content-Security-Policy"')
  })

  it("includes baseline hardening directives", () => {
    expect(indexHtml).toContain("default-src 'self'")
    expect(indexHtml).toContain("script-src 'self'")
    expect(indexHtml).toContain("connect-src 'self' %VITE_CSP_CONNECT_SRC%")
    expect(indexHtml).toContain("object-src 'none'")
    expect(indexHtml).toContain("frame-ancestors 'none'")
  })
})
