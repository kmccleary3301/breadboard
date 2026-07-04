import { afterEach, describe, expect, it, vi } from "vitest"

const ORIGINAL_ENV = {
  BREADBOARD_API_TOKEN: process.env.BREADBOARD_API_TOKEN,
  BREADBOARD_KEYCHAIN: process.env.BREADBOARD_KEYCHAIN,
}

const keychainMock = vi.hoisted(() => vi.fn<(account: string) => Promise<string | undefined>>())
const deleteKeychainMock = vi.hoisted(() => vi.fn<(account: string) => Promise<boolean>>())
const userConfigMock = vi.hoisted(() => vi.fn())
const writeUserConfigMock = vi.hoisted(() => vi.fn<(config: unknown) => Promise<void>>())

vi.mock("../keychain.js", () => ({
  getKeychainAuthToken: keychainMock,
  deleteKeychainAuthToken: deleteKeychainMock,
}))

vi.mock("../userConfig.js", () => ({
  loadUserConfigSync: userConfigMock,
  writeUserConfig: writeUserConfigMock,
}))

afterEach(() => {
  for (const [key, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) delete process.env[key]
    else process.env[key] = value
  }
  vi.clearAllMocks()
  vi.resetModules()
})

describe("resolveAuthToken", () => {
  it("treats explicit keychain accounts as opaque aliases", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    delete process.env.BREADBOARD_KEYCHAIN
    userConfigMock.mockReturnValue({ authTokenRef: "keychain:my-prod-token" })
    keychainMock.mockResolvedValue("stored-token")

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9099/health")).resolves.toBe("stored-token")

    expect(keychainMock).toHaveBeenCalledWith("my-prod-token")
  })

  it("resolves plain keychain refs against the current base URL", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    delete process.env.BREADBOARD_KEYCHAIN
    userConfigMock.mockReturnValue({ authTokenRef: "keychain" })
    keychainMock.mockImplementation(async (account) =>
      account === "http://127.0.0.1:9199" ? "new-server-token" : undefined,
    )

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9199/health")).resolves.toBe("new-server-token")

    expect(keychainMock).toHaveBeenCalledWith("http://127.0.0.1:9199")
    expect(keychainMock).not.toHaveBeenCalledWith("http://127.0.0.1:9099")
  })

  it("ignores URL-bound keychain refs for other base URLs", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    delete process.env.BREADBOARD_KEYCHAIN
    userConfigMock.mockReturnValue({ authTokenRef: "keychain:http://127.0.0.1:9099" })
    keychainMock.mockResolvedValue("old-server-token")

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9199/health")).resolves.toBeUndefined()

    expect(keychainMock).not.toHaveBeenCalled()
  })

  it("falls back from mismatched URL-bound refs to enabled implicit keychain accounts", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    process.env.BREADBOARD_KEYCHAIN = "1"
    userConfigMock.mockReturnValue({ authTokenRef: "keychain:http://127.0.0.1:9099" })
    keychainMock.mockImplementation(async (account) =>
      account === "http://127.0.0.1:9199/health" ? "new-server-token" : undefined,
    )

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9199/health")).resolves.toBe("new-server-token")

    expect(keychainMock).toHaveBeenCalledWith("http://127.0.0.1:9199/health")
    expect(keychainMock).not.toHaveBeenCalledWith("http://127.0.0.1:9099")
  })

  it("normalizes keychain base-url accounts and falls back to the raw URL", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    process.env.BREADBOARD_KEYCHAIN = "1"
    userConfigMock.mockReturnValue({})
    keychainMock.mockResolvedValueOnce(undefined).mockResolvedValueOnce("legacy-token")

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9099/")).resolves.toBe("legacy-token")

    expect(keychainMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:9099")
    expect(keychainMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:9099/")
  })

  it("falls back to the server origin for health-check URLs", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    process.env.BREADBOARD_KEYCHAIN = "1"
    userConfigMock.mockReturnValue({})
    keychainMock.mockResolvedValueOnce(undefined).mockResolvedValueOnce("origin-token")

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9099/health")).resolves.toBe("origin-token")

    expect(keychainMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:9099/health")
    expect(keychainMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:9099")
  })

  it("prefers path-prefixed keychain base-url accounts before origin fallbacks", async () => {
    delete process.env.BREADBOARD_API_TOKEN
    process.env.BREADBOARD_KEYCHAIN = "1"
    userConfigMock.mockReturnValue({})
    keychainMock.mockResolvedValueOnce("api-token")

    const { resolveAuthToken } = await import("../authTokenProvider.js")
    await expect(resolveAuthToken("http://127.0.0.1:9099/api")).resolves.toBe("api-token")

    expect(keychainMock).toHaveBeenCalledTimes(1)
    expect(keychainMock).toHaveBeenCalledWith("http://127.0.0.1:9099/api")
  })
})

describe("resolveKeychainDeleteAccounts", () => {
  it("clears normalized, origin, and raw implicit keychain accounts", async () => {
    const { resolveKeychainDeleteAccounts } = await import("../authTokenProvider.js")

    expect(resolveKeychainDeleteAccounts("http://127.0.0.1:9099/api", "keychain")).toEqual([
      "http://127.0.0.1:9099/api",
      "http://127.0.0.1:9099",
    ])
  })

  it("treats explicit keychain accounts as opaque aliases", async () => {
    const { resolveKeychainDeleteAccounts } = await import("../authTokenProvider.js")

    expect(resolveKeychainDeleteAccounts("http://127.0.0.1:9099/api", "keychain:prod")).toEqual(["prod"])
  })

  it("clears URL-shaped keychain refs with normalized and raw accounts", async () => {
    const { resolveKeychainDeleteAccounts } = await import("../authTokenProvider.js")

    expect(resolveKeychainDeleteAccounts("http://127.0.0.1:9099/", "keychain:http://127.0.0.1:9099/")).toEqual([
      "http://127.0.0.1:9099",
      "http://127.0.0.1:9099/",
    ])
  })
})


describe("clearKeychainAuthForBaseUrl", () => {
  it("clears the authTokenRef only for the matching configured base URL", async () => {
    userConfigMock.mockReturnValue({
      baseUrl: "http://127.0.0.1:9099/api",
      authTokenRef: "keychain",
      authToken: "file-token",
    })
    deleteKeychainMock.mockResolvedValueOnce(false).mockResolvedValueOnce(true)

    const { clearKeychainAuthForBaseUrl } = await import("../authTokenProvider.js")
    await expect(clearKeychainAuthForBaseUrl("http://127.0.0.1:9099/api/")).resolves.toBe(true)

    expect(deleteKeychainMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:9099/api")
    expect(deleteKeychainMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:9099")
    expect(writeUserConfigMock).toHaveBeenCalledWith({
      baseUrl: "http://127.0.0.1:9099/api",
      authToken: "file-token",
    })
  })

  it("preserves URL-bound authTokenRef values for other configured bases", async () => {
    userConfigMock.mockReturnValue({
      baseUrl: "http://127.0.0.1:9099",
      authTokenRef: "keychain",
    })
    deleteKeychainMock.mockResolvedValue(false)

    const { clearKeychainAuthForBaseUrl } = await import("../authTokenProvider.js")
    await expect(clearKeychainAuthForBaseUrl("http://127.0.0.1:9099/api")).resolves.toBe(false)

    expect(writeUserConfigMock).toHaveBeenCalledWith({
      baseUrl: "http://127.0.0.1:9099",
      authTokenRef: "keychain",
    })
  })

  it("does not delete explicit keychain aliases for other configured bases", async () => {
    userConfigMock.mockReturnValue({
      baseUrl: "http://127.0.0.1:9099",
      authTokenRef: "keychain:prod",
    })
    deleteKeychainMock.mockResolvedValue(false)

    const { clearKeychainAuthForBaseUrl } = await import("../authTokenProvider.js")
    await expect(clearKeychainAuthForBaseUrl("http://127.0.0.1:9099/api")).resolves.toBe(false)

    expect(deleteKeychainMock).not.toHaveBeenCalledWith("prod")
    expect(deleteKeychainMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:9099/api")
    expect(deleteKeychainMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:9099")
    expect(writeUserConfigMock).toHaveBeenCalledWith({
      baseUrl: "http://127.0.0.1:9099",
      authTokenRef: "keychain:prod",
    })
  })
})
