import { spawn, type ChildProcess } from "node:child_process"
import fs from "node:fs"
import net from "node:net"
import path from "node:path"

export type BridgeHandle = {
  readonly baseUrl: string
  readonly child: ChildProcess | null
  readonly started: boolean
}

export type SessionCreateRequest = {
  readonly config_path: string
  readonly task: string
  readonly overrides?: Record<string, unknown> | null
  readonly metadata?: Record<string, unknown> | null
  readonly workspace?: string | null
  readonly max_steps?: number | null
  readonly permission_mode?: string | null
  readonly stream?: boolean
}

export type SessionCreateResponse = {
  readonly session_id: string
  readonly status: string
  readonly logging_dir?: string | null
}

export type SessionCommandRequest = {
  readonly command: string
  readonly payload?: Record<string, unknown> | null
}

export type SessionCommandResponse = {
  readonly status?: string
  readonly detail?: Record<string, unknown> | null
}

export type SessionInputRequest = {
  readonly content: string
  readonly attachments?: string[] | null
}

export type BridgeEvent = {
  readonly id?: string
  readonly seq?: number | null
  readonly type: string
  readonly session_id?: string
  readonly turn?: number | null
  readonly timestamp?: number | null
  readonly timestamp_ms?: number | null
  readonly protocol_version?: string
  readonly payload?: Record<string, unknown> | null
}

export type ModelCatalogEntry = {
  readonly id: string
  readonly provider?: string | null
  readonly name?: string | null
  readonly context_length?: number | null
  readonly metadata?: Record<string, unknown> | null
}

export type ModelCatalogResponse = {
  readonly models: ModelCatalogEntry[]
  readonly default_model?: string | null
  readonly config_path?: string | null
}

const buildUrl = (baseUrl: string, pathPart: string): string => {
  const normalized = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`
  return new URL(pathPart.replace(/^\//, ""), normalized).toString()
}

export const resolveEngineRoot = (startDir: string): string | null => {
  let current = path.resolve(startDir)
  for (;;) {
    if (fs.existsSync(path.join(current, "agentic_coder_prototype"))) return current
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  return null
}

const pickEphemeralPort = (host = "127.0.0.1"): Promise<number> =>
  new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, host, () => {
      const addr = server.address()
      if (!addr || typeof addr === "string") {
        server.close(() => reject(new Error("Unable to resolve ephemeral port.")))
        return
      }
      const port = addr.port
      server.close(() => resolve(port))
    })
  })

export const healthCheck = async (baseUrl: string, timeoutMs = 1500): Promise<boolean> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(buildUrl(baseUrl, "/health"), { signal: controller.signal })
    return response.ok
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

export const waitForHealth = async (baseUrl: string, timeoutMs = 40_000): Promise<boolean> => {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (await healthCheck(baseUrl, 1000)) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

export const ensureBridge = async (options: {
  readonly baseUrlHint?: string
  readonly python?: string
  readonly engineRoot?: string
  readonly logPath?: string
}): Promise<BridgeHandle> => {
  const hinted = options.baseUrlHint?.trim() || process.env.BREADBOARD_API_URL?.trim() || ""
  if (hinted && (await healthCheck(hinted))) {
    return { baseUrl: hinted.replace(/\/$/, ""), child: null, started: false }
  }

  const engineRoot = options.engineRoot?.trim() || resolveEngineRoot(process.cwd())
  if (!engineRoot) {
    throw new Error("Unable to locate engine root (expected agentic_coder_prototype/ upward from cwd).")
  }

  const host = "127.0.0.1"
  const python = options.python?.trim() || process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"
  const maxAttempts = 5

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const port = await pickEphemeralPort(host)
    const baseUrl = `http://${host}:${port}`
    const child = spawn(python, ["-m", "agentic_coder_prototype.api.cli_bridge.server"], {
      cwd: engineRoot,
      env: {
        ...process.env,
        BREADBOARD_CLI_HOST: host,
        BREADBOARD_CLI_PORT: String(port),
        BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    })

    const logStream = options.logPath ? fs.createWriteStream(options.logPath, { flags: "a" }) : null
    if (logStream) {
      logStream.write(`\n[bridge] attempt ${attempt}/${maxAttempts} baseUrl=${baseUrl}\n`)
      child.stdout?.on("data", (chunk) => logStream.write(chunk))
      child.stderr?.on("data", (chunk) => logStream.write(chunk))
      child.once("exit", () => logStream.end())
    }

    const exitedEarly = new Promise<boolean>((resolve) => {
      child.once("exit", () => resolve(true))
    })
    const ready = await Promise.race([waitForHealth(baseUrl, 40_000), exitedEarly.then(() => false)])
    if (ready) {
      process.env.BREADBOARD_API_URL = baseUrl
      return { baseUrl, child, started: true }
    }

    try {
      child.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
    } catch {
      // ignore
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }

  throw new Error(`CLI bridge failed to become healthy after ${maxAttempts} attempts.`)
}

export const createSession = async (baseUrl: string, payload: SessionCreateRequest): Promise<SessionCreateResponse> => {
  const response = await fetch(buildUrl(baseUrl, "/sessions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`Create session failed: HTTP ${response.status} ${text}`)
  }
  return (await response.json()) as SessionCreateResponse
}

export const postInput = async (baseUrl: string, sessionId: string, payload: SessionInputRequest): Promise<void> => {
  const response = await fetch(buildUrl(baseUrl, `/sessions/${sessionId}/input`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`Post input failed: HTTP ${response.status} ${text}`)
  }
}

export const postCommand = async (
  baseUrl: string,
  sessionId: string,
  payload: SessionCommandRequest,
): Promise<SessionCommandResponse> => {
  const response = await fetch(buildUrl(baseUrl, `/sessions/${sessionId}/command`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`Post command failed: HTTP ${response.status} ${text}`)
  }
  const text = await response.text().catch(() => "")
  if (!text.trim()) return { status: "accepted", detail: null }
  try {
    const parsed = JSON.parse(text) as SessionCommandResponse
    if (parsed && typeof parsed === "object") {
      return parsed
    }
  } catch {
    // ignore parse failure; return opaque success marker
  }
  return { status: "accepted", detail: null }
}

export const getModelCatalog = async (baseUrl: string, configPath: string): Promise<ModelCatalogResponse> => {
  const url = new URL(buildUrl(baseUrl, "/models"))
  url.searchParams.set("config_path", configPath)
  const response = await fetch(url.toString(), { method: "GET" })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`Get model catalog failed: HTTP ${response.status} ${text}`)
  }
  return (await response.json()) as ModelCatalogResponse
}

export const iterSseEvents = async function* (
  url: string,
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<{ id: string | null; data: string }, void, void> {
  const response = await fetch(url, { method: "GET", signal: options.signal })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`SSE request failed: HTTP ${response.status} ${text}`)
  }
  if (!response.body) {
    throw new Error("SSE response missing body")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let currentId: string | null = null
  let currentData: string[] = []

  const flush = async function* () {
    if (currentData.length === 0) return
    const data = currentData.join("\n")
    const id = currentId
    currentId = null
    currentData = []
    yield { id, data }
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, "\n")

      while (true) {
        const sep = buffer.indexOf("\n\n")
        if (sep === -1) break
        const raw = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)

        const lines = raw.split("\n")
        for (const line of lines) {
          if (!line || line.startsWith(":")) continue
          if (line.startsWith("id:")) {
            currentId = line.slice(3).trim() || null
            continue
          }
          if (line.startsWith("data:")) {
            currentData.push(line.slice(5).replace(/^ /, ""))
          }
        }
        yield* flush()
      }
    }
  } finally {
    reader.releaseLock()
    await response.body.cancel().catch(() => undefined)
  }
}

export const streamSessionEvents = async function* (
  baseUrl: string,
  sessionId: string,
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<BridgeEvent, void, void> {
  const url = buildUrl(baseUrl, `/sessions/${sessionId}/events`)
  for await (const evt of iterSseEvents(url, options)) {
    if (!evt.data) continue
    try {
      const parsed = JSON.parse(evt.data) as BridgeEvent
      if (parsed && typeof parsed.type === "string") {
        yield parsed
      }
    } catch {
      // ignore malformed events
    }
  }
}
