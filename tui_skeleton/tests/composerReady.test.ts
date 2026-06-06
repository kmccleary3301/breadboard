import { describe, expect, it } from "vitest"

import { COMPOSER_READY_MARKERS, findComposerReadyMarker, includesComposerReady } from "../scripts/harness/composerReady.ts"

describe("composerReady markers", () => {
  it("matches the current footer hint", () => {
    expect(findComposerReadyMarker("• [ready] enter send")).toBe("enter send")
    expect(includesComposerReady("• [ready] enter send")).toBe(true)
  })

  it("keeps compatibility with legacy startup copy", () => {
    expect(findComposerReadyMarker("Try edit <file> to...")).toBe("Try edit <file> to...")
  })

  it("tracks the compatibility marker set explicitly", () => {
    expect(COMPOSER_READY_MARKERS).toContain("enter send")
    expect(COMPOSER_READY_MARKERS).toContain("Try edit <file> to...")
  })
})
