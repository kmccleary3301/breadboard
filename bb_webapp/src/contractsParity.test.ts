import { describe, expect, it } from "vitest"
import { buildSessionDownloadPath, FORBIDDEN_ROUTE_MARKERS } from "./bridgeContracts"

describe("bridge contract parity guards", () => {
  it("does not reference deprecated or mismatched route shapes", () => {
    for (const marker of FORBIDDEN_ROUTE_MARKERS) {
      expect(buildSessionDownloadPath("session-1").includes(marker)).toBe(false)
    }
  })

  it("uses normalized download path contract", () => {
    expect(buildSessionDownloadPath("session-1")).toBe("/sessions/session-1/download")
  })
})
