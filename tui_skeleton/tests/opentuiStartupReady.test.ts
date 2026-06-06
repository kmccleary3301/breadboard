import { describe, expect, it, vi } from "vitest"

import { waitForPlainComposerReady } from "../scripts/opentuiStartupReady.ts"

describe("waitForPlainComposerReady", () => {
  it("accepts the current footer marker", async () => {
    await expect(waitForPlainComposerReady(() => "• [ready] enter send", { timeoutMs: 20, pollMs: 1 })).resolves.toBeUndefined()
  })

  it("accepts legacy OpenTUI startup copy", async () => {
    await expect(waitForPlainComposerReady(() => "Enter submit", { timeoutMs: 20, pollMs: 1 })).resolves.toBeUndefined()
    await expect(waitForPlainComposerReady(() => "Enter to submit", { timeoutMs: 20, pollMs: 1 })).resolves.toBeUndefined()
  })

  it("times out when no readiness marker appears", async () => {
    vi.useFakeTimers()
    try {
      const promise = waitForPlainComposerReady(() => "no marker", { timeoutMs: 10, pollMs: 1, label: "test" })
      const assertion = expect(promise).rejects.toThrow("[test] timed out waiting for startup readiness")
      await vi.advanceTimersByTimeAsync(20)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })
})
