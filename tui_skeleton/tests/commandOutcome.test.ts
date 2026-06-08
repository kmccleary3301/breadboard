import { describe, expect, it, vi } from "vitest"
import { runCommandWithReportedError } from "../src/commands/commandOutcome.js"

describe("commandOutcome", () => {
  it("returns successful results unchanged", async () => {
    await expect(
      runCommandWithReportedError({
        run: async () => 42,
        report: async () => undefined,
      }),
    ).resolves.toBe(42)
  })

  it("reports and rethrows failures", async () => {
    const report = vi.fn(async () => undefined)
    const error = new Error("boom")
    await expect(
      runCommandWithReportedError({
        run: async () => {
          throw error
        },
        report,
      }),
    ).rejects.toBe(error)
    expect(report).toHaveBeenCalledWith(error)
  })
})
