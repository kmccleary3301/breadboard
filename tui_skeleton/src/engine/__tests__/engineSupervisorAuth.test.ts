import path from "node:path"
import { tmpdir } from "node:os"
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const resolveAuthTokenMock = vi.hoisted(() => vi.fn(() => new Promise<string>(() => undefined)))

vi.mock("../../config/authTokenProvider.js", () => ({
  resolveAuthToken: resolveAuthTokenMock,
}))

const ORIGINAL_ENV = {
  BREADBOARD_USER_CONFIG: process.env.BREADBOARD_USER_CONFIG,
  BREADBOARD_API_URL: process.env.BREADBOARD_API_URL,
  BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS: process.env.BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS,
  BREADBOARD_ENGINE_MODE: process.env.BREADBOARD_ENGINE_MODE,
  HOME: process.env.HOME,
}

const TEST_HOMES: string[] = []



beforeEach(async () => {
  const home = await mkdtemp(path.join(tmpdir(), "breadboard-engine-auth-"))
  TEST_HOMES.push(home)
  process.env.HOME = home
  process.env.BREADBOARD_USER_CONFIG = path.join(home, "config.json")
})
afterEach(async () => {
  for (const [key, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = value
    }
  }
  await Promise.all(TEST_HOMES.splice(0).map((home) => rm(home, { recursive: true, force: true })))
  vi.restoreAllMocks()
  resolveAuthTokenMock.mockReset()
  resolveAuthTokenMock.mockImplementation(() => new Promise<string>(() => undefined))
  vi.resetModules()
})

describe("ensureEngine auth bootstrap", () => {
  it("times out health auth resolution instead of hanging", async () => {
    process.env.BREADBOARD_API_URL = "http://127.0.0.1:65535"
    process.env.BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS = "25"
    process.env.BREADBOARD_ENGINE_MODE = "external"
    vi.resetModules()
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }))

    const { ensureEngine } = await import("../engineSupervisor.js")
    const startedAt = Date.now()

    await expect(ensureEngine({ allowSpawn: false })).rejects.toThrow("Engine not reachable")
    expect(Date.now() - startedAt).toBeLessThan(1_000)
    expect(fetchMock).toHaveBeenCalled()
    expect(resolveAuthTokenMock).toHaveBeenCalledWith("http://127.0.0.1:65535")
  })

  it("probes health below path-prefixed external base URLs", async () => {
    process.env.BREADBOARD_API_URL = "http://127.0.0.1:65535/api"
    process.env.BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS = "25"
    process.env.BREADBOARD_ENGINE_MODE = "external"
    vi.resetModules()
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }))

    const { ensureEngine } = await import("../engineSupervisor.js")

    await expect(ensureEngine({ allowSpawn: false })).resolves.toMatchObject({
      baseUrl: "http://127.0.0.1:65535/api",
      started: false,
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.map(([url]) => url.toString())).toEqual([
      "http://127.0.0.1:65535/api/health",
      "http://127.0.0.1:65535/api/health",
    ])
  })

  it("treats synchronous health auth provider errors as unreachable health", async () => {
    process.env.BREADBOARD_API_URL = "http://127.0.0.1:65535"
    process.env.BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS = "25"
    process.env.BREADBOARD_ENGINE_MODE = "external"
    vi.resetModules()
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }))

    resolveAuthTokenMock.mockImplementationOnce(() => {
      throw new Error("sync auth failure")
    })
    const { ensureEngine } = await import("../engineSupervisor.js")

    await expect(ensureEngine({ allowSpawn: false })).rejects.toThrow("Engine not reachable")
    expect(resolveAuthTokenMock).toHaveBeenCalledWith("http://127.0.0.1:65535")
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it("leaves healthy mismatched managed locks intact for explicit URLs", async () => {
    const home = await mkdtemp(path.join(tmpdir(), "breadboard-engine-lock-"))
    process.env.HOME = home
    process.env.BREADBOARD_API_URL = "http://127.0.0.1:65535/api"
    process.env.BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS = "25"
    process.env.BREADBOARD_ENGINE_MODE = "external"
    const lockPath = path.join(home, ".breadboard", "engine", "engine.lock")
    const lock = {
      pid: process.pid,
      port: 9099,
      baseUrl: "http://127.0.0.1:9099",
      startedAt: new Date().toISOString(),
    }
    await mkdir(path.dirname(lockPath), { recursive: true })
    await writeFile(lockPath, JSON.stringify(lock, null, 2), "utf8")
    vi.resetModules()
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }))

    const { ensureEngine } = await import("../engineSupervisor.js")

    await expect(ensureEngine({ allowSpawn: false })).resolves.toMatchObject({
      baseUrl: "http://127.0.0.1:65535/api",
      started: false,
    })
    expect(fetchMock.mock.calls.map(([url]) => url.toString())).toEqual([
      "http://127.0.0.1:65535/api/health",
      "http://127.0.0.1:65535/api/health",
    ])
    await expect(readFile(lockPath, "utf8")).resolves.toBe(JSON.stringify(lock, null, 2))
    await rm(home, { recursive: true, force: true })
  })
})
