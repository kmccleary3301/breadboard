import { spawn, type ChildProcess } from "node:child_process"
import { homedir } from "node:os"
import path from "node:path"
import fs from "node:fs"
import { promises as fsp } from "node:fs"
import net from "node:net"
import { fileURLToPath } from "node:url"
import { loadAppConfig } from "../config/appConfig.js"
import { loadUserConfigSync } from "../config/userConfig.js"
import { downloadBundle, resolveCachedBundle } from "./engineBundles.js"
import { CLI_PROTOCOL_VERSION, CLI_VERSION } from "../config/version.js"

interface EngineLock {
  readonly pid: number
  readonly port: number
  readonly baseUrl: string
  readonly startedAt: string
  readonly root?: string
  readonly command?: string
}

export interface EngineSupervisorResult {
  readonly baseUrl: string
  readonly started: boolean
  readonly pid?: number
}

const ENGINE_DIR = path.join(homedir(), ".breadboard", "engine")
const ENGINE_LOCK_PATH = path.join(ENGINE_DIR, "engine.lock")
const DEFAULT_PORT = 9099
const DEFAULT_HOST = "127.0.0.1"
const STARTUP_TIMEOUT_MS = 20_000
const HEALTH_TIMEOUT_MS = 2_500

let activeChild: ChildProcess | null = null
let activeBaseUrl: string | null = null

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readLockSync = (): EngineLock | null => {
  try {
    if (!fs.existsSync(ENGINE_LOCK_PATH)) return null
    const raw = fs.readFileSync(ENGINE_LOCK_PATH, "utf8")
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return null
    if (typeof parsed.pid !== "number" || typeof parsed.port !== "number" || typeof parsed.baseUrl !== "string") {
      return null
    }
    return {
      pid: parsed.pid,
      port: parsed.port,
      baseUrl: parsed.baseUrl,
      startedAt: typeof parsed.startedAt === "string" ? parsed.startedAt : new Date().toISOString(),
      root: typeof parsed.root === "string" ? parsed.root : undefined,
      command: typeof parsed.command === "string" ? parsed.command : undefined,
    }
  } catch {
    return null
  }
}

const writeLock = async (lock: EngineLock): Promise<void> => {
  await fsp.mkdir(path.dirname(ENGINE_LOCK_PATH), { recursive: true })
  await fsp.writeFile(ENGINE_LOCK_PATH, JSON.stringify(lock, null, 2), "utf8")
}

const clearLock = async (): Promise<void> => {
  try {
    await fsp.unlink(ENGINE_LOCK_PATH)
  } catch {
    // ignore
  }
}

const isProcessAlive = (pid: number): boolean => {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

const isLocalHost = (hostname: string): boolean => {
  const lower = hostname.toLowerCase()
  return lower === "localhost" || lower === "127.0.0.1" || lower === "::1"
}

const resolveBaseUrl = (value: string | undefined): URL | null => {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed) return null
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
  try {
    return new URL(candidate)
  } catch {
    return null
  }
}

const healthCheck = async (baseUrl: string, timeoutMs = HEALTH_TIMEOUT_MS): Promise<boolean> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(new URL("/health", baseUrl), { signal: controller.signal })
    return response.ok
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

const fetchHealth = async (
  baseUrl: string,
  timeoutMs = HEALTH_TIMEOUT_MS,
): Promise<{ protocol_version?: string; version?: string } | null> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(new URL("/health", baseUrl), { signal: controller.signal })
    if (!response.ok) return null
    const payload = (await response.json().catch(() => null)) as Record<string, any> | null
    if (!payload || typeof payload !== "object") return null
    return {
      protocol_version: typeof payload.protocol_version === "string" ? payload.protocol_version : undefined,
      version: typeof payload.version === "string" ? payload.version : undefined,
    }
  } catch {
    return null
  } finally {
    clearTimeout(timeout)
  }
}

const verifyProtocol = async (baseUrl: string) => {
  const health = await fetchHealth(baseUrl)
  if (!health?.protocol_version) return
  if (health.protocol_version !== CLI_PROTOCOL_VERSION) {
    const strict = process.env.BREADBOARD_PROTOCOL_STRICT === "1"
    const message = `Engine protocol ${health.protocol_version} does not match CLI protocol ${CLI_PROTOCOL_VERSION}`
    if (strict) {
      throw new Error(message)
    }
    console.warn(`[engine] ${message}`)
  }
  if (health.version && process.env.BREADBOARD_ENGINE_VERSION) {
    if (health.version !== process.env.BREADBOARD_ENGINE_VERSION) {
      console.warn(
        `[engine] Engine version ${health.version} does not match expected ${process.env.BREADBOARD_ENGINE_VERSION}`,
      )
    }
  }
}

const waitForHealth = async (baseUrl: string, timeoutMs = STARTUP_TIMEOUT_MS): Promise<boolean> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (await healthCheck(baseUrl)) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

const isPortAvailable = (host: string, port: number): Promise<boolean> =>
  new Promise((resolve) => {
    const server = net.createServer()
    server.once("error", () => {
      resolve(false)
    })
    server.once("listening", () => {
      server.close(() => resolve(true))
    })
    server.listen(port, host)
  })

const findAvailablePort = async (host: string, preferredPort: number): Promise<number> => {
  const maxAttempts = 20
  for (let offset = 0; offset < maxAttempts; offset += 1) {
    const candidate = preferredPort + offset
    if (await isPortAvailable(host, candidate)) {
      return candidate
    }
  }
  throw new Error(`No available port found starting at ${preferredPort}.`)
}

const findUpward = (startDir: string, relativePath: string): string | null => {
  let current = path.resolve(startDir)
  for (;;) {
    const candidate = path.join(current, relativePath)
    if (fs.existsSync(candidate)) return current
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  return null
}

const resolveEngineRoot = (): string | null => {
  const explicit = process.env.BREADBOARD_ENGINE_ROOT?.trim()
  if (explicit) {
    return path.resolve(explicit)
  }
  const cwdHit = findUpward(process.cwd(), "agentic_coder_prototype")
  if (cwdHit) return cwdHit
  const moduleDir = path.dirname(fileURLToPath(import.meta.url))
  const moduleHit = findUpward(moduleDir, "agentic_coder_prototype")
  return moduleHit
}

const resolveEngineCommand = async (): Promise<{ command: string; args: string[]; cwd?: string; shell?: boolean }> => {
  const cmd = process.env.BREADBOARD_ENGINE_CMD?.trim()
  if (cmd) {
    return { command: cmd, args: [], shell: true }
  }
  const bin = process.env.BREADBOARD_ENGINE_BIN?.trim()
  if (bin) {
    const extraArgs = process.env.BREADBOARD_ENGINE_ARGS?.trim().split(/\s+/).filter(Boolean) ?? []
    return { command: bin, args: extraArgs }
  }
  const userConfig = loadUserConfigSync()
  const explicitPath = process.env.BREADBOARD_ENGINE_PATH?.trim() || userConfig.enginePath
  if (explicitPath) {
    return { command: explicitPath, args: [] }
  }
  const version = process.env.BREADBOARD_ENGINE_VERSION?.trim() || userConfig.engineVersion
  const manifestUrl = process.env.BREADBOARD_ENGINE_MANIFEST_URL?.trim()
  if (version) {
    const cached = await resolveCachedBundle(version)
    if (cached) {
      return { command: cached.entry, args: [], cwd: cached.root }
    }
    if (manifestUrl && process.env.BREADBOARD_ENGINE_AUTO_DOWNLOAD === "1") {
      const downloaded = await downloadBundle(manifestUrl, {
        version,
        cliVersion: CLI_VERSION,
        protocolVersion: CLI_PROTOCOL_VERSION,
        retries: 2,
      })
      return { command: path.join(downloaded.root, downloaded.entry), args: [], cwd: downloaded.root }
    }
  }
  if (manifestUrl && process.env.BREADBOARD_ENGINE_AUTO_DOWNLOAD === "1") {
    const downloaded = await downloadBundle(manifestUrl, {
      cliVersion: CLI_VERSION,
      protocolVersion: CLI_PROTOCOL_VERSION,
      retries: 2,
    })
    return { command: path.join(downloaded.root, downloaded.entry), args: [], cwd: downloaded.root }
  }
  const python = process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"
  const cwd = resolveEngineRoot() ?? process.cwd()
  return {
    command: python,
    args: ["-m", "agentic_coder_prototype.api.cli_bridge.server"],
    cwd,
  }
}

const registerCleanup = (child: ChildProcess) => {
  const cleanup = () => {
    if (!child.killed) {
      child.kill()
    }
  }
  process.once("exit", cleanup)
  process.once("SIGINT", () => {
    cleanup()
    process.exit(130)
  })
  process.once("SIGTERM", () => {
    cleanup()
    process.exit(143)
  })
}

const shouldKeepAlive = () =>
  process.env.BREADBOARD_ENGINE_KEEPALIVE === "1" || process.env.BREADBOARD_ENGINE_KEEPALIVE === "true"

export const shutdownEngine = async (
  options: { timeoutMs?: number; force?: boolean } = {},
): Promise<boolean> => {
  if (!activeChild) return false
  if (shouldKeepAlive()) return false
  if (process.env.BREADBOARD_ENGINE_MANAGED !== "1") return false

  const child = activeChild
  activeChild = null
  activeBaseUrl = null

  if (child.exitCode !== null || child.killed) {
    await clearLock()
    return true
  }

  const timeoutMs = options.timeoutMs ?? 2_500
  const signal = options.force ? "SIGKILL" : "SIGTERM"
  try {
    child.kill(signal)
  } catch {
    // ignore
  }

  const exited = await new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs)
    child.once("exit", () => {
      clearTimeout(timer)
      resolve(true)
    })
  })

  if (!exited && !options.force) {
    try {
      child.kill("SIGKILL")
    } catch {
      // ignore
    }
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 1_000)
      child.once("exit", () => {
        clearTimeout(timer)
        resolve()
      })
    })
  }
  await clearLock()
  return true
}

const pickEphemeralPort = (host: string): Promise<number> =>
  new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, host, () => {
      const address = server.address()
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("Unable to resolve ephemeral port.")))
        return
      }
      const port = address.port
      server.close(() => resolve(port))
    })
  })

export const ensureEngine = async ({
  allowSpawn = true,
  isolated = false,
}: {
  allowSpawn?: boolean
  isolated?: boolean
} = {}): Promise<EngineSupervisorResult> => {
  if (activeBaseUrl) {
    return { baseUrl: activeBaseUrl, started: Boolean(activeChild?.pid), pid: activeChild?.pid }
  }

  const mode = process.env.BREADBOARD_ENGINE_MODE?.trim().toLowerCase()
  if (mode === "external" || mode === "remote" || mode === "off") {
    allowSpawn = false
  }

  const config = loadAppConfig()
  const configUrl = resolveBaseUrl(config.baseUrl)
  const baseHost = isolated ? DEFAULT_HOST : configUrl?.hostname ?? DEFAULT_HOST
  const baseUrl = isolated
    ? `http://${baseHost}:${DEFAULT_PORT}`
    : configUrl?.toString().replace(/\/$/, "") ?? `http://${DEFAULT_HOST}:${DEFAULT_PORT}`

  if (!isolated) {
  const lock = readLockSync()
  if (lock && isProcessAlive(lock.pid)) {
    const ok = await healthCheck(lock.baseUrl)
    if (ok) {
      activeBaseUrl = lock.baseUrl
      process.env.BREADBOARD_API_URL = lock.baseUrl
      await verifyProtocol(lock.baseUrl)
      return { baseUrl: lock.baseUrl, started: false, pid: lock.pid }
    }
  } else if (lock) {
    await clearLock()
  }
  }

  const shouldSpawn = allowSpawn && isLocalHost(baseHost)
  const hasHealthyEngine = isolated ? false : await healthCheck(baseUrl)
  if (hasHealthyEngine) {
    activeBaseUrl = baseUrl
    process.env.BREADBOARD_API_URL = baseUrl
    await verifyProtocol(baseUrl)
    return { baseUrl, started: false }
  }

  if (!shouldSpawn) {
    throw new Error(`Engine not reachable at ${baseUrl}. Use "breadboard connect" or start the engine.`)
  }

  const preferredPortRaw = configUrl?.port
    ? Number(configUrl.port)
    : Number(process.env.BREADBOARD_CLI_PORT ?? DEFAULT_PORT)
  const preferredPort = Number.isFinite(preferredPortRaw) ? preferredPortRaw : DEFAULT_PORT
  const port = isolated ? await pickEphemeralPort(baseHost) : await findAvailablePort(baseHost, preferredPort)
  const resolvedBaseUrl = `http://${baseHost}:${port}`
  const { command, args, cwd, shell } = await resolveEngineCommand()
  const child = spawn(command, args, {
    cwd,
    env: {
      ...process.env,
      BREADBOARD_CLI_HOST: baseHost,
      BREADBOARD_CLI_PORT: String(port),
    },
    stdio: "inherit",
    shell,
  })
  activeChild = child
  activeBaseUrl = resolvedBaseUrl
  process.env.BREADBOARD_API_URL = resolvedBaseUrl
  process.env.BREADBOARD_ENGINE_MANAGED = "1"

  if (child.pid) {
    await writeLock({
      pid: child.pid,
      port,
      baseUrl: resolvedBaseUrl,
      startedAt: new Date().toISOString(),
      root: cwd,
      command: [command, ...args].join(" "),
    })
  }

  registerCleanup(child)
  child.once("exit", () => {
    activeChild = null
    activeBaseUrl = null
    clearLock().catch(() => undefined)
  })

  const ready = await waitForHealth(resolvedBaseUrl)
  if (!ready) {
    if (child.pid) {
      try {
        child.kill()
      } catch {
        // ignore
      }
    }
    throw new Error(`Engine failed to become healthy at ${resolvedBaseUrl}.`)
  }

  await verifyProtocol(resolvedBaseUrl)
  return { baseUrl: resolvedBaseUrl, started: true, pid: child.pid }
}
