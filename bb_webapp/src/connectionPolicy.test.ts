import { describe, expect, it } from "vitest"
import { resolveClientAuthToken, sanitizeTokenForStorage, shouldSendAuthorizationHeader } from "./connectionPolicy"

describe("connectionPolicy", () => {
  it("attaches auth header only for remote mode", () => {
    expect(shouldSendAuthorizationHeader("local")).toBe(false)
    expect(shouldSendAuthorizationHeader("sandbox")).toBe(false)
    expect(shouldSendAuthorizationHeader("remote")).toBe(true)
  })

  it("resolves auth token by mode", () => {
    expect(resolveClientAuthToken("local", "abc")).toBeUndefined()
    expect(resolveClientAuthToken("remote", "abc")).toBe("abc")
  })

  it("respects token storage policy", () => {
    expect(sanitizeTokenForStorage("session", "abc")).toBe("")
    expect(sanitizeTokenForStorage("persisted", "abc")).toBe("abc")
  })
})
