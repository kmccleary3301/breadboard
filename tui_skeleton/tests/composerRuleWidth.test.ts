import { describe, expect, it } from "vitest"

import { resolveComposerRuleWidth } from "../src/repl/components/replView/composer/ComposerPanel.tsx"

describe("resolveComposerRuleWidth", () => {
  it("keeps separator rules one column below terminal width to avoid autowrap collisions", () => {
    expect(resolveComposerRuleWidth(93, "─".repeat(132))).toBe(92)
    expect(resolveComposerRuleWidth(1, "─".repeat(132))).toBe(1)
  })

  it("falls back to prompt-rule length without using the full width", () => {
    expect(resolveComposerRuleWidth(Number.NaN, "────")).toBe(3)
  })
})
