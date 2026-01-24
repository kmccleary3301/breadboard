import {
  createCliRenderer,
  TextRenderable,
  TextareaRenderable,
  type KeyEvent,
  type PasteEvent,
} from "@opentui/core"

import { spawn, type ChildProcess } from "node:child_process"
import fs from "node:fs"
import net from "node:net"
import path from "node:path"

type CliBridgeSessionCreateRequest = {
  readonly config_path: string
  readonly task: string
  readonly overrides?: Record<string, unknown> | null
  readonly metadata?: Record<string, unknown> | null
  readonly workspace?: string | null
  readonly max_steps?: number | null
  readonly permission_mode?: string | null
  readonly stream?: boolean
}

type CliBridgeSessionCreateResponse = {
  readonly session_id: string
  readonly status: string
}

type CliBridgeSessionInputRequest = {
  readonly content: string
  readonly attachments?: string[] | null
}

type CliBridgeSessionEvent = {
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

type ParsedArgs = {
  readonly baseUrl?: string
  readonly configPath?: string
  readonly workspace?: string
  readonly python?: string
  readonly noStartEngine?: boolean
  readonly localSpam?: boolean
  readonly localSpamHz?: number
}

const parseArgs = (argv: string[]): ParsedArgs => {
  const args: Record<string, string | boolean> = {}
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i] ?? ""
    if (!token.startsWith("--")) continue
    const key = token.slice(2)
    const next = argv[i + 1]
    if (!next || next.startsWith("--")) {
      args[key] = true
      continue
    }
    args[key] = next
    i += 1
  }
  return {
    baseUrl: typeof args["base-url"] === "string" ? (args["base-url"] as string) : undefined,
    configPath: typeof args["config"] === "string" ? (args["config"] as string) : undefined,
    workspace: typeof args["workspace"] === "string" ? (args["workspace"] as string) : undefined,
    python: typeof args["python"] === "string" ? (args["python"] as string) : undefined,
    noStartEngine: Boolean(args["no-start-engine"]),
    localSpam: Boolean(args["local-spam"]),
    localSpamHz: typeof args["local-spam-hz"] === "string" ? Number(args["local-spam-hz"]) : undefined,
  }
}

const resolveEngineRoot = (): string | null => {
  const startDir = process.cwd()
  let current = path.resolve(startDir)
  for (;;) {
    const candidate = path.join(current, "agentic_coder_prototype")
    if (fs.existsSync(candidate)) return current
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  return null
}

const buildUrl = (baseUrl: string, pathPart: string): string => {
  const normalized = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`
  const url = new URL(pathPart.replace(/^\//, ""), normalized)
  return url.toString()
}

const healthCheck = async (baseUrl: string, timeoutMs = 1500): Promise<boolean> => {
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

const waitForHealth = async (baseUrl: string, timeoutMs = 20_000): Promise<boolean> => {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (await healthCheck(baseUrl, 1000)) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

const ensureCliBridge = async (options: {
  readonly baseUrlHint?: string
  readonly python?: string
  readonly noStartEngine?: boolean
}): Promise<{ baseUrl: string; child: ChildProcess | null; started: boolean }> => {
  const hinted = options.baseUrlHint?.trim() || process.env.BREADBOARD_API_URL?.trim() || ""
  if (hinted && (await healthCheck(hinted))) {
    return { baseUrl: hinted.replace(/\/$/, ""), child: null, started: false }
  }
  if (options.noStartEngine) {
    throw new Error(`CLI bridge not reachable at ${hinted || "<unset>"} and --no-start-engine was set.`)
  }

  const engineRoot = resolveEngineRoot()
  if (!engineRoot) {
    throw new Error("Unable to locate engine root (expected agentic_coder_prototype/ upward from cwd).")
  }

  const host = "127.0.0.1"
  const python = options.python?.trim() || process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"

  const maxAttempts = 5
  const attemptTimeoutMs = 25_000

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
      stdio: ["ignore", "inherit", "inherit"],
    })

    const exitedEarly = new Promise<boolean>((resolve) => {
      child.once("exit", () => resolve(true))
    })

    const ready = await Promise.race([waitForHealth(baseUrl, attemptTimeoutMs), exitedEarly.then(() => false)])
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

    if (attempt === maxAttempts) {
      throw new Error(`CLI bridge failed to become healthy after ${maxAttempts} attempts.`)
    }
  }

  throw new Error("CLI bridge spawn retry loop ended unexpectedly.")
}

const createSession = async (
  baseUrl: string,
  payload: CliBridgeSessionCreateRequest,
): Promise<CliBridgeSessionCreateResponse> => {
  const response = await fetch(buildUrl(baseUrl, "/sessions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`Create session failed: HTTP ${response.status} ${text}`)
  }
  return (await response.json()) as CliBridgeSessionCreateResponse
}

const postInput = async (baseUrl: string, sessionId: string, payload: CliBridgeSessionInputRequest): Promise<void> => {
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

const iterSseEvents = async function* (
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

const streamSessionEvents = async function* (
  baseUrl: string,
  sessionId: string,
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<CliBridgeSessionEvent, void, void> {
  const url = buildUrl(baseUrl, `/sessions/${sessionId}/events`)
  for await (const evt of iterSseEvents(url, options)) {
    if (!evt.data) continue
    try {
      const parsed = JSON.parse(evt.data) as CliBridgeSessionEvent
      if (parsed && typeof parsed.type === "string") {
        yield parsed
      }
    } catch {
      // ignore malformed events
    }
  }
}

const formatEventForStdout = (event: CliBridgeSessionEvent): string | null => {
  const payload = event.payload ?? {}
  const readText = () => {
    const raw = (payload as any).text
    return typeof raw === "string" ? raw : ""
  }

  switch (event.type) {
    case "user_message": {
      const text = readText()
      return text ? `\n[user]\n${text}\n` : null
    }
    case "assistant_message": {
      const text = readText()
      return text ? `\n[assistant]\n${text}\n` : null
    }
    case "tool_call": {
      const call = (payload as any).call
      const name = call && typeof call.name === "string" ? call.name : "tool_call"
      return `\n[tool] ${name}\n`
    }
    case "tool_result": {
      const result = (payload as any).result
      const name = result && typeof result.name === "string" ? result.name : "tool_result"
      return `\n[tool] ${name} (result)\n`
    }
    case "permission_request":
      return `\n[permission] request\n`
    case "permission_response":
      return `\n[permission] response\n`
    case "error": {
      const message = (payload as any).message
      return `\n[error] ${typeof message === "string" ? message : "unknown"}\n`
    }
    case "log_link": {
      const url = (payload as any).url
      return typeof url === "string" ? `\n[log] ${url}\n` : null
    }
    case "run_finished": {
      const reason = (payload as any).reason
      return `\n[run_finished]${typeof reason === "string" ? ` ${reason}` : ""}\n`
    }
    default:
      return null
  }
}

const args = parseArgs(process.argv)
const configPath = args.configPath?.trim() || process.env.BREADBOARD_CONFIG_PATH?.trim() || "agent_configs/test_enhanced_agent_v2.yaml"
const workspace = args.workspace?.trim() || process.env.BREADBOARD_WORKSPACE?.trim() || ""

const bridge = await ensureCliBridge({
  baseUrlHint: args.baseUrl,
  python: args.python,
  noStartEngine: args.noStartEngine,
})

let engineChild = bridge.child
const engineStartedHere = bridge.started
const baseUrl = bridge.baseUrl

const renderer = await createCliRenderer({
  useAlternateScreen: false,
  useConsole: false,
  experimental_splitHeight: 12,
  targetFps: 20,
  maxFps: 30,
})

// OpenTUI splitHeight uses internal `_terminalHeight/_terminalWidth` derived from `stdout.rows/columns`.
// In some environments (including PTY wrappers), Bun may not populate these fields even when output is a TTY.
// If they are missing, `flushStdoutCache()` can emit invalid cursor moves (NaN).
const inferredTerminalWidth = renderer.width || 80
const inferredTerminalHeight = ((renderer as any).renderOffset ?? 0) + renderer.height || 24
const terminalWidth = process.stdout.columns && process.stdout.columns > 0 ? process.stdout.columns : inferredTerminalWidth
const terminalHeight = process.stdout.rows && process.stdout.rows > 0 ? process.stdout.rows : inferredTerminalHeight
const currentTerminalWidth = (renderer as any)._terminalWidth as number | undefined
const currentTerminalHeight = (renderer as any)._terminalHeight as number | undefined
if (!currentTerminalWidth || currentTerminalWidth <= 0) {
  ;(renderer as any)._terminalWidth = terminalWidth
}
if (!currentTerminalHeight || currentTerminalHeight <= 0) {
  ;(renderer as any)._terminalHeight = terminalHeight
}

const splitHeight = 12

const input = new TextareaRenderable(renderer, {
  id: "composer",
  position: "absolute",
  left: 0,
  top: 0,
  width: "100%",
  height: splitHeight - 1,
  placeholder: "Enter to submit · Shift+Enter newline · Ctrl+D exits",
  wrapMode: "word",
  cursorStyle: { style: "block", blinking: false },
})

const footer = new TextRenderable(renderer, {
  id: "footer",
  position: "absolute",
  left: 0,
  top: splitHeight - 1,
  width: "100%",
  height: 1,
  content: `BreadBoard OpenTUI slab · bridge=${baseUrl} · config=${configPath}`,
  fg: "#999999",
})

renderer.root.add(input)
renderer.root.add(footer)
input.focus()

let sessionId: string | null = null
let streamAbort: AbortController | null = null
let streamTask: Promise<void> | null = null
let pendingUserEcho: { text: string; at: number } | null = null

const shutdown = async (): Promise<void> => {
  if (streamAbort) {
    streamAbort.abort()
    streamAbort = null
  }
  await streamTask?.catch(() => undefined)
  streamTask = null
  if (localSpamInterval) {
    clearInterval(localSpamInterval)
    localSpamInterval = null
  }
  renderer.destroy()
  if (engineStartedHere && engineChild) {
    try {
      const shutdownSignal = process.platform === "win32" ? "SIGTERM" : "SIGINT"
      engineChild.kill(shutdownSignal)
    } catch {
      // ignore
    }
    engineChild = null
  }
}

renderer.keyInput.on("keypress", (key: KeyEvent) => {
  if (key.ctrl && key.name === "d") {
    void shutdown()
    return
  }
  if ((key.name === "return" || key.name === "enter") && !key.shift && !key.meta && !key.ctrl) {
    key.preventDefault()
    input.submit()
    return
  }
  if ((key.name === "return" || key.name === "enter") && key.shift && !key.meta && !key.ctrl) {
    key.preventDefault()
    input.newLine()
  }
})

renderer.keyInput.on("paste", (event: PasteEvent) => {
  process.stdout.write(`[paste] ${event.text.length} chars\n`)
})

let localSpamInterval: ReturnType<typeof setInterval> | null = null
if (args.localSpam) {
  const hz = Number.isFinite(args.localSpamHz) && (args.localSpamHz as number) > 0 ? (args.localSpamHz as number) : 50
  const intervalMs = Math.max(1, Math.floor(1000 / hz))
  let counter = 0
  localSpamInterval = setInterval(() => {
    counter += 1
    process.stdout.write(`[local] spam ${counter}\n`)
  }, intervalMs)
}

input.onSubmit = () => {
  const content = input.plainText.trimEnd()
  if (!content.trim()) return
  input.clear()
  input.focus()

  pendingUserEcho = { text: content.trim(), at: Date.now() }
  process.stdout.write(`\n[user]\n${content}\n`)

  void (async () => {
    try {
      if (!sessionId) {
        const created = await createSession(baseUrl, {
          config_path: configPath,
          task: content,
          workspace: workspace || null,
          stream: true,
        })
        sessionId = created.session_id
        process.stdout.write(`\n[session] ${sessionId}\n`)

        streamAbort = new AbortController()
        streamTask = (async () => {
          for await (const event of streamSessionEvents(baseUrl, sessionId!, { signal: streamAbort!.signal })) {
            if (event.type === "user_message" && pendingUserEcho) {
              const text = typeof (event.payload as any)?.text === "string" ? ((event.payload as any).text as string).trim() : ""
              if (text && text === pendingUserEcho.text && Date.now() - pendingUserEcho.at < 2500) {
                pendingUserEcho = null
                continue
              }
            }
            const rendered = formatEventForStdout(event)
            if (rendered) {
              process.stdout.write(rendered)
            }
          }
        })()
        return
      }

      await postInput(baseUrl, sessionId, { content })
    } catch (error) {
      process.stdout.write(`\n[client_error] ${(error as Error).message}\n`)
    }
  })()
}

renderer.start()

process.on("SIGINT", () => void shutdown())
process.on("SIGTERM", () => void shutdown())