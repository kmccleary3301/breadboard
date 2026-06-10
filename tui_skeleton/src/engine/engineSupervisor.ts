import { spawn, type ChildProcess, type StdioOptions } from "node:child_process"
import { homedir } from "node:os"
import path from "node:path"
import fs from "node:fs"
import { promises as fsp } from "node:fs"
import net from "node:net"
import { fileURLToPath } from "node:url"
import { loadAppConfig } from "../config/appConfig.js"
import { loadUserConfigSync } from "../config/userConfig.js"
import { CLI_PROTOCOL_VERSION, CLI_VERSION } from "../config/version.js"
import { assertRuntimeIntegrity, resolveEngineRoot as resolveRuntimeEngineRoot } from "../config/runtimePaths.js"
import { resolveEngineLifecycleMode, type ResolvedEngineLifecycleMode } from "./lifecycleMode.js"

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

export interface ActiveEngineLifecycleSnapshot extends ResolvedEngineLifecycleMode {
  readonly baseUrl?: string
  readonly pid?: number
  readonly lockPath: string
  readonly logPath: string
}

const ENGINE_DIR = path.join(homedir(), ".breadboard", "engine")
const ENGINE_LOCK_PATH = path.join(ENGINE_DIR, "engine.lock")
const ENGINE_LOG_PATH = path.join(ENGINE_DIR, "engine.log")
const DEFAULT_PORT = 9099
const DEFAULT_HOST = "127.0.0.1"
const envInt = (key: string, fallback: number): number => {
  const raw = process.env[key]
  if (!raw) return fallback
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

const STARTUP_TIMEOUT_MS = envInt("BREADBOARD_ENGINE_STARTUP_TIMEOUT_MS", 20_000)
const HEALTH_TIMEOUT_MS = envInt("BREADBOARD_ENGINE_HEALTH_TIMEOUT_MS", 2_500)

let activeChild: ChildProcess | null = null
let activeBaseUrl: string | null = null
let activeLifecycleMode: ResolvedEngineLifecycleMode | null = null
const cleanupByChild = new WeakMap<ChildProcess, () => void>()

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

export const shouldReuseLockedEngineBaseUrl = ({
  isolated,
  explicitBaseUrlConfigured,
  configuredBaseUrl,
  lockedBaseUrl,
}: {
  isolated: boolean
  explicitBaseUrlConfigured: boolean
  configuredBaseUrl: string
  lockedBaseUrl: string
}): boolean => {
  if (isolated) return false
  if (!explicitBaseUrlConfigured) return true
  return configuredBaseUrl === lockedBaseUrl
}

const healthCheck = async (baseUrl: string, timeoutMs = HEALTH_TIMEOUT_MS): Promise<boolean> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const token = loadAppConfig().authToken
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(new URL("/health", baseUrl), { signal: controller.signal, headers })
    return response.ok
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

const readyCheck = async (baseUrl: string, timeoutMs = HEALTH_TIMEOUT_MS): Promise<boolean> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const token = loadAppConfig().authToken
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(new URL("/ready", baseUrl), { signal: controller.signal, headers })
    if (!response.ok) return false
    const payload = (await response.json().catch(() => null)) as Record<string, any> | null
    if (!payload || typeof payload !== "object") return false
    return payload.ready === true
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

const fetchHealth = async (
  baseUrl: string,
  timeoutMs = HEALTH_TIMEOUT_MS,
): Promise<{
  protocol_version?: string
  version?: string
  started_at?: string
  started_at_unix?: number
  pid?: number
  served_revision?: {
    repo_root?: string
    commit?: string | null
    branch?: string | null
    dirty?: boolean | null
  }
} | null> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const token = loadAppConfig().authToken
    const headers: Record<string, string> = {}
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(new URL("/health", baseUrl), { signal: controller.signal, headers })
    if (!response.ok) return null
    const payload = (await response.json().catch(() => null)) as Record<string, any> | null
    if (!payload || typeof payload !== "object") return null
    return {
      protocol_version: typeof payload.protocol_version === "string" ? payload.protocol_version : undefined,
      version: typeof payload.version === "string" ? payload.version : undefined,
      started_at: typeof payload.started_at === "string" ? payload.started_at : undefined,
      started_at_unix: typeof payload.started_at_unix === "number" ? payload.started_at_unix : undefined,
      pid: typeof payload.pid === "number" ? payload.pid : undefined,
      served_revision:
        payload.served_revision && typeof payload.served_revision === "object"
          ? {
              repo_root:
                typeof (payload.served_revision as Record<string, unknown>).repo_root === "string"
                  ? ((payload.served_revision as Record<string, unknown>).repo_root as string)
                  : undefined,
              commit:
                typeof (payload.served_revision as Record<string, unknown>).commit === "string"
                  ? ((payload.served_revision as Record<string, unknown>).commit as string)
                  : null,
              branch:
                typeof (payload.served_revision as Record<string, unknown>).branch === "string"
                  ? ((payload.served_revision as Record<string, unknown>).branch as string)
                  : null,
              dirty:
                typeof (payload.served_revision as Record<string, unknown>).dirty === "boolean"
                  ? ((payload.served_revision as Record<string, unknown>).dirty as boolean)
                  : null,
            }
          : undefined,
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
    const strict =
      process.env.BREADBOARD_PROTOCOL_STRICT !== "0" &&
      process.env.BREADBOARD_ALLOW_PROTOCOL_MISMATCH !== "1"
    const message = `Engine protocol ${health.protocol_version} does not match CLI protocol ${CLI_PROTOCOL_VERSION}`
    if (strict) {
      throw new Error(message)
    }
    console.warn(`[engine] ${message}`)
  }
  if (health.version && process.env.BREADBOARD_ENGINE_VERSION) {
    if (health.version !== process.env.BREADBOARD_ENGINE_VERSION) {
      const strict = process.env.BREADBOARD_ENGINE_ALLOW_VERSION_MISMATCH !== "1"
      const message = `[engine] Engine version ${health.version} does not match expected ${process.env.BREADBOARD_ENGINE_VERSION}`
      if (strict) {
        throw new Error(message)
      }
      console.warn(message)
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

const waitForReady = async (baseUrl: string, timeoutMs = STARTUP_TIMEOUT_MS): Promise<boolean> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (await readyCheck(baseUrl)) return true
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

const resolveEngineRoot = (): string | null => {
  const explicit = process.env.BREADBOARD_ENGINE_ROOT?.trim()
  if (explicit) {
    return path.resolve(explicit)
  }
  try {
    return resolveRuntimeEngineRoot()
  } catch {
    const moduleDir = path.dirname(fileURLToPath(import.meta.url))
    let current = path.resolve(moduleDir)
    for (;;) {
      const candidate = path.join(current, "agentic_coder_prototype")
      if (fs.existsSync(candidate)) return current
      const parent = path.dirname(current)
      if (parent === current) break
      current = parent
    }
    return null
  }
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

  const engineRoot = resolveEngineRoot()
  if (engineRoot) {
    const python = process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"
    return {
      command: python,
      args: ["-m", "agentic_coder_prototype.api.cli_bridge.server"],
      cwd: engineRoot,
    }
  }
  const python = process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"
  const cwd = engineRoot ?? process.cwd()
  return {
    command: python,
    args: ["-m", "agentic_coder_prototype.api.cli_bridge.server"],
    cwd,
  }
}

const registerCleanup = (child: ChildProcess, options: { keepAlive?: boolean } = {}) => {
  const shutdownSignal = process.platform === "win32" ? "SIGTERM" : "SIGINT"
  const keepAlive = options.keepAlive === true
  if (keepAlive) return () => undefined
  const cleanup = () => {
    if (!child.killed) {
      child.kill(shutdownSignal)
    }
  }
  const onExit = () => cleanup()
  const onSigint = () => {
    cleanup()
    process.exit(130)
  }
  const onSigterm = () => {
    cleanup()
    process.exit(143)
  }
  process.once("exit", onExit)
  process.once("SIGINT", onSigint)
  process.once("SIGTERM", onSigterm)
  const unregister = () => {
    process.off("exit", onExit)
    process.off("SIGINT", onSigint)
    process.off("SIGTERM", onSigterm)
  }
  cleanupByChild.set(child, unregister)
  child.once("exit", unregister)
  return unregister
}

const unregisterChildCleanup = (child: ChildProcess | null | undefined): void => {
  if (!child) return
  const unregister = cleanupByChild.get(child)
  if (!unregister) return
  cleanupByChild.delete(child)
  unregister()
}

const shouldKeepAlive = (isolated = false) => {
  if (isolated) return false
  const raw = (process.env.BREADBOARD_ENGINE_KEEPALIVE ?? "").trim().toLowerCase()
  if (!raw) return true
  if (["1", "true", "yes", "on"].includes(raw)) return true
  if (["0", "false", "no", "off"].includes(raw)) return false
  return true
}

export const shutdownEngine = async (
  options: { timeoutMs?: number; force?: boolean } = {},
): Promise<boolean> => {
  if (!activeChild) return false
  if (shouldKeepAlive()) return false
  if (process.env.BREADBOARD_ENGINE_MANAGED !== "1") return false

  const child = activeChild
  activeChild = null
  activeBaseUrl = null
  activeLifecycleMode = null

  if (child.exitCode !== null || child.killed) {
    await clearLock()
    return true
  }

  const timeoutMs = options.timeoutMs ?? 2_500
  const signal = options.force ? "SIGKILL" : process.platform === "win32" ? "SIGTERM" : "SIGINT"
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

const waitForProcessExit = async (pid: number, timeoutMs: number): Promise<boolean> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (!isProcessAlive(pid)) return true
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  return !isProcessAlive(pid)
}

export const restartOwnedEngine = async (
  options: { timeoutMs?: number; force?: boolean } = {},
): Promise<EngineSupervisorResult> => {
  const timeoutMs = options.timeoutMs ?? 1_500
  const signal = options.force === false ? "SIGTERM" : "SIGKILL"
  const lock = readLockSync()
  const child = activeChild
  const pid = child?.pid ?? lock?.pid

  unregisterChildCleanup(child)
  activeChild = null
  activeBaseUrl = null
  await clearLock()

  if (typeof pid === "number" && Number.isFinite(pid) && isProcessAlive(pid)) {
    try {
      process.kill(pid, signal)
    } catch {
      // The process may have exited between the liveness check and kill.
    }
    await waitForProcessExit(pid, timeoutMs)
  }

  return ensureEngine({ allowSpawn: true, isolated: false })
}

export const getEngineLogPath = (): string => ENGINE_LOG_PATH

export const getActiveEngineLifecycleMode = (): ResolvedEngineLifecycleMode | null => activeLifecycleMode

export const getActiveEngineLifecycleSnapshot = (): ActiveEngineLifecycleSnapshot | null => {
  if (!activeLifecycleMode) return null
  const lock = readLockSync()
  return {
    ...activeLifecycleMode,
    baseUrl: activeBaseUrl ?? lock?.baseUrl,
    pid: activeChild?.pid ?? lock?.pid,
    lockPath: ENGINE_LOCK_PATH,
    logPath: ENGINE_LOG_PATH,
  }
}

export const stopEngineFromLock = async (
  options: { timeoutMs?: number; force?: boolean } = {},
): Promise<{ stopped: boolean; pid?: number; baseUrl?: string }> => {
  const lock = readLockSync()
  if (!lock) return { stopped: false }
  if (!isProcessAlive(lock.pid)) {
    await clearLock()
    return { stopped: false, pid: lock.pid, baseUrl: lock.baseUrl }
  }

  const timeoutMs = options.timeoutMs ?? 5_000
  const signal = options.force ? "SIGKILL" : process.platform === "win32" ? "SIGTERM" : "SIGINT"
  try {
    process.kill(lock.pid, signal)
  } catch {
    await clearLock()
    return { stopped: false, pid: lock.pid, baseUrl: lock.baseUrl }
  }

  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (!isProcessAlive(lock.pid)) {
      await clearLock()
      return { stopped: true, pid: lock.pid, baseUrl: lock.baseUrl }
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }

  if (!options.force) {
    try {
      process.kill(lock.pid, "SIGKILL")
    } catch {
      // ignore
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }

  const stopped = !isProcessAlive(lock.pid)
  if (stopped) {
    await clearLock()
  }
  return { stopped, pid: lock.pid, baseUrl: lock.baseUrl }
}

export const startEngineDetached = async ({
  host = DEFAULT_HOST,
  port = DEFAULT_PORT,
}: {
  host?: string
  port?: number
} = {}): Promise<{ baseUrl: string; pid: number; logPath: string }> => {
  const baseHost = host.trim() || DEFAULT_HOST
  const requestedPort = Number.isFinite(port) && (port ?? 0) > 0 ? (port as number) : DEFAULT_PORT

  // Prefer existing engine via lock if healthy.
  const lock = readLockSync()
  if (lock && isProcessAlive(lock.pid)) {
    const ok = await healthCheck(lock.baseUrl)
    if (ok) {
      return { baseUrl: lock.baseUrl, pid: lock.pid, logPath: ENGINE_LOG_PATH }
    }
  }

  // Avoid surprising port changes for a "daemon-style" command; require explicit port if in use.
  const available = await isPortAvailable(baseHost, requestedPort)
  if (!available) {
    throw new Error(
      `Port ${requestedPort} is not available on ${baseHost}. Stop the existing engine or choose a different --port.`,
    )
  }

  await fsp.mkdir(path.dirname(ENGINE_LOG_PATH), { recursive: true })
  const outFd = fs.openSync(ENGINE_LOG_PATH, "a")
  const { command, args, cwd, shell } = await resolveEngineCommand()
  const child = spawn(command, args, {
    cwd,
    env: {
      ...process.env,
      BREADBOARD_CLI_HOST: baseHost,
      BREADBOARD_CLI_PORT: String(requestedPort),
    },
    stdio: ["ignore", outFd, outFd],
    detached: true,
    shell,
  })
  child.unref()

  if (!child.pid) {
    throw new Error("Failed to start engine (missing pid).")
  }

  const resolvedBaseUrl = `http://${baseHost}:${requestedPort}`
  await writeLock({
    pid: child.pid,
    port: requestedPort,
    baseUrl: resolvedBaseUrl,
    startedAt: new Date().toISOString(),
    root: cwd,
    command: [command, ...args].join(" "),
  })

  const healthy = await waitForHealth(resolvedBaseUrl, STARTUP_TIMEOUT_MS)
  if (!healthy) {
    throw new Error(`Engine failed to become healthy at ${resolvedBaseUrl}. See logs: ${ENGINE_LOG_PATH}`)
  }
  const ready = await waitForReady(resolvedBaseUrl, STARTUP_TIMEOUT_MS)
  if (!ready) {
    throw new Error(`Engine failed to become ready at ${resolvedBaseUrl}. See logs: ${ENGINE_LOG_PATH}`)
  }

  await verifyProtocol(resolvedBaseUrl)
  return { baseUrl: resolvedBaseUrl, pid: child.pid, logPath: ENGINE_LOG_PATH }
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
  allowSpawn,
  isolated = false,
  cliMode,
}: {
  allowSpawn?: boolean
  isolated?: boolean
  cliMode?: string | null
} = {}): Promise<EngineSupervisorResult> => {
  assertRuntimeIntegrity({ includeSource: true })
  if (activeBaseUrl) {
    const cachedBaseUrl = activeBaseUrl
    const cachedPid = activeChild?.pid
    const processAlive = cachedPid == null || isProcessAlive(cachedPid)
    const healthy = processAlive ? await healthCheck(cachedBaseUrl) : false
    if (healthy) {
      return { baseUrl: cachedBaseUrl, started: Boolean(cachedPid), pid: cachedPid }
    }
    unregisterChildCleanup(activeChild)
    activeChild = null
    activeBaseUrl = null
    if (cachedPid != null && !processAlive) {
      await clearLock()
    }
  }

  const config = loadAppConfig()
  const userConfig = loadUserConfigSync()
  const configUrl = resolveBaseUrl(config.baseUrl)
  const explicitBaseUrlConfigured = Boolean(
    process.env.BREADBOARD_API_URL?.trim() || userConfig.baseUrl?.trim(),
  )
  const baseHost = isolated ? DEFAULT_HOST : configUrl?.hostname ?? DEFAULT_HOST
  const baseUrl = isolated
    ? `http://${baseHost}:${DEFAULT_PORT}`
    : configUrl?.toString().replace(/\/$/, "") ?? `http://${DEFAULT_HOST}:${DEFAULT_PORT}`
  const lifecycleMode = resolveEngineLifecycleMode({
    cliMode,
    configMode: userConfig.engineMode,
    envMode: process.env.BREADBOARD_ENGINE_MODE,
    baseUrl,
    explicitBaseUrlConfigured,
  })
  activeLifecycleMode = lifecycleMode
  if (allowSpawn === undefined) {
    allowSpawn = lifecycleMode.allowSpawn
  } else if (lifecycleMode.mode !== "local-owned" && lifecycleMode.modeSource !== "explicit-base-url") {
    allowSpawn = false
  }

  if (!isolated) {
    const lock = readLockSync()
    if (lock && isProcessAlive(lock.pid)) {
      const shouldUseLock = shouldReuseLockedEngineBaseUrl({
        isolated,
        explicitBaseUrlConfigured,
        configuredBaseUrl: baseUrl,
        lockedBaseUrl: lock.baseUrl,
      })
      const ok = shouldUseLock ? await healthCheck(lock.baseUrl) : false
      if (ok) {
        activeBaseUrl = lock.baseUrl
        process.env.BREADBOARD_API_URL = lock.baseUrl
        activeLifecycleMode = lifecycleMode
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
    activeLifecycleMode = lifecycleMode
    await verifyProtocol(baseUrl)
    return { baseUrl, started: false }
  }

  if (!shouldSpawn) {
    throw new Error(
      `Engine not reachable at ${baseUrl}. Start it with "breadboard engine start" (background) or "breadboard engine serve" (foreground), or use "breadboard connect" to point at an existing engine.`,
    )
  }

  const preferredPortRaw = configUrl?.port
    ? Number(configUrl.port)
    : Number(process.env.BREADBOARD_CLI_PORT ?? DEFAULT_PORT)
  const preferredPort = Number.isFinite(preferredPortRaw) ? preferredPortRaw : DEFAULT_PORT
  const port = isolated ? await pickEphemeralPort(baseHost) : await findAvailablePort(baseHost, preferredPort)
  const resolvedBaseUrl = `http://${baseHost}:${port}`
  const { command, args, cwd, shell } = await resolveEngineCommand()
  await fsp.mkdir(path.dirname(ENGINE_LOG_PATH), { recursive: true })
  const inheritEngineStdio = process.env.BREADBOARD_ENGINE_INHERIT_STDIO === "1"
  const keepAliveManagedChild = shouldKeepAlive(isolated)
  const canUnrefManagedChild = !inheritEngineStdio
  let logFd: number | null = null
  const stdio: StdioOptions = inheritEngineStdio
    ? "inherit"
    : (() => {
        logFd = fs.openSync(ENGINE_LOG_PATH, "a")
        return ["ignore", logFd, logFd]
      })()
  const child = spawn(command, args, {
    cwd,
    env: {
      ...process.env,
      BREADBOARD_CLI_HOST: baseHost,
      BREADBOARD_CLI_PORT: String(port),
    },
    stdio,
    detached: keepAliveManagedChild && canUnrefManagedChild,
    shell,
  })
  if (logFd != null) {
    try {
      fs.closeSync(logFd)
    } catch {
      // ignore best-effort fd close in parent
    }
  }
  activeChild = child
  activeBaseUrl = resolvedBaseUrl
  activeLifecycleMode = lifecycleMode
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

  registerCleanup(child, { keepAlive: keepAliveManagedChild })
  child.once("exit", () => {
    unregisterChildCleanup(child)
    activeChild = null
    activeBaseUrl = null
    activeLifecycleMode = null
    clearLock().catch(() => undefined)
  })

  const healthy = await waitForHealth(resolvedBaseUrl)
  if (!healthy) {
    if (child.pid) {
      try {
        child.kill()
      } catch {
        // ignore
      }
    }
    throw new Error(`Engine failed to become healthy at ${resolvedBaseUrl}.`)
  }
  const ready = await waitForReady(resolvedBaseUrl)
  if (!ready) {
    if (child.pid) {
      try {
        child.kill()
      } catch {
        // ignore
      }
    }
    throw new Error(`Engine failed to become ready at ${resolvedBaseUrl}.`)
  }

  await verifyProtocol(resolvedBaseUrl)
  // Once the managed engine is healthy and ready, it should behave like the detached
  // background daemon path and stop pinning short-lived CLI commands such as `bb ask`.
  if (keepAliveManagedChild && canUnrefManagedChild) {
    child.unref()
  }
  return { baseUrl: resolvedBaseUrl, started: true, pid: child.pid }
}
