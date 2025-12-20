import { describe, expect, it } from "vitest"
import { computeModelColumns } from "../../src/repl/modelMenu/layout.js"

describe("computeModelColumns", () => {
  it("shows all columns on wide terminals", () => {
    const result = computeModelColumns(120)
    expect(result).toEqual({
      showContext: true,
      showPriceIn: true,
      showPriceOut: true,
      providerWidth: expect.any(Number),
    })
    expect(result.providerWidth).toBeGreaterThanOrEqual(24)
  })

  it("hides context first on medium widths", () => {
    const result = computeModelColumns(70)
    expect(result.showContext).toBe(false)
    expect(result.showPriceIn).toBe(true)
    expect(result.showPriceOut).toBe(true)
  })

  it("falls back to provider + out price on very narrow terminals", () => {
    const result = computeModelColumns(50)
    expect(result.showContext).toBe(false)
    expect(result.showPriceIn).toBe(false)
    expect(result.showPriceOut).toBe(true)
  })
})
