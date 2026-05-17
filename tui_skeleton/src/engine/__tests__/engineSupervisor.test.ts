import { describe, expect, it } from "vitest"
import { shouldReuseLockedEngineBaseUrl } from "../engineSupervisor.js"

describe("shouldReuseLockedEngineBaseUrl", () => {
  it("reuses the lock when no explicit base URL is configured", () => {
    expect(
      shouldReuseLockedEngineBaseUrl({
        isolated: false,
        explicitBaseUrlConfigured: false,
        configuredBaseUrl: "http://127.0.0.1:9099",
        lockedBaseUrl: "http://127.0.0.1:9099",
      }),
    ).toBe(true)
  })

  it("reuses the lock when the explicit base URL matches the lock", () => {
    expect(
      shouldReuseLockedEngineBaseUrl({
        isolated: false,
        explicitBaseUrlConfigured: true,
        configuredBaseUrl: "http://127.0.0.1:9500",
        lockedBaseUrl: "http://127.0.0.1:9500",
      }),
    ).toBe(true)
  })

  it("does not reuse the lock when an explicit base URL points elsewhere", () => {
    expect(
      shouldReuseLockedEngineBaseUrl({
        isolated: false,
        explicitBaseUrlConfigured: true,
        configuredBaseUrl: "http://127.0.0.1:9500",
        lockedBaseUrl: "http://127.0.0.1:9099",
      }),
    ).toBe(false)
  })

  it("never reuses the lock in isolated mode", () => {
    expect(
      shouldReuseLockedEngineBaseUrl({
        isolated: true,
        explicitBaseUrlConfigured: false,
        configuredBaseUrl: "http://127.0.0.1:9099",
        lockedBaseUrl: "http://127.0.0.1:9099",
      }),
    ).toBe(false)
  })
})
