import { describe, expect, it } from "vitest"

import { buildConnectConfig } from "../connect.js"

describe("buildConnectConfig", () => {
  it("preserves explicit keychain aliases across base URL changes", () => {
    expect(
      buildConnectConfig(
        { baseUrl: "http://127.0.0.1:9099", authTokenRef: "keychain:prod" },
        "http://127.0.0.1:9199",
        null,
        false,
      ),
    ).toEqual({ baseUrl: "http://127.0.0.1:9199", authTokenRef: "keychain:prod" })
  })

  it("drops URL-bound keychain refs when changing base URLs", () => {
    expect(
      buildConnectConfig(
        { baseUrl: "http://127.0.0.1:9099", authTokenRef: "keychain:http://127.0.0.1:9099" },
        "http://127.0.0.1:9199",
        null,
        false,
      ),
    ).toEqual({ baseUrl: "http://127.0.0.1:9199" })
  })

  it("drops plain keychain refs when changing base URLs", () => {
    expect(
      buildConnectConfig(
        { baseUrl: "http://127.0.0.1:9099", authTokenRef: "keychain" },
        "http://127.0.0.1:9199",
        null,
        false,
      ),
    ).toEqual({ baseUrl: "http://127.0.0.1:9199" })
  })

  it("preserves plain keychain refs when the base URL is unchanged", () => {
    expect(
      buildConnectConfig(
        { baseUrl: "http://127.0.0.1:9099/", authTokenRef: "keychain" },
        "http://127.0.0.1:9099",
        null,
        false,
      ),
    ).toEqual({ baseUrl: "http://127.0.0.1:9099", authTokenRef: "keychain" })
  })

  it("clears token refs when a new token is supplied", () => {
    expect(
      buildConnectConfig(
        { baseUrl: "http://127.0.0.1:9099", authTokenRef: "keychain:prod" },
        "http://127.0.0.1:9199",
        "new-token",
        false,
      ),
    ).toEqual({ baseUrl: "http://127.0.0.1:9199", authToken: "new-token" })
  })
})
