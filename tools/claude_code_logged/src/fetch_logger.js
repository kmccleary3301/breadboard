import fs from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import crypto from "node:crypto"

const DEFAULT_DIR = path.join(os.homedir(), ".local", "share", "claude_code", "log", "provider_dumps")
const MAX_BYTES = 5 * 1024 * 1024 * 1024 // 5 GiB
const REDACT_HEADERS = new Set(["x-api-key", "authorization", "x-client-api-key"])

let warnedAboutDisk = false
let loggerDisabled = false
let runtimeId = crypto.randomUUID()

const shouldDisable = () => {
  if (loggerDisabled) return true
  const env = process.env
  if (env.CLAUDE_CODE_DISABLE_LOGGING === "1" || env.CLAUDE_CODE_DISABLE_PROVIDER_LOGGING === "1") return true
  if (env.CLAUDE_CODE_ENABLE_PROVIDER_LOGGING === "0") return true
  const argv = process.argv ?? []
  if (argv.some((arg) => arg === "--disable-provider-logging" || arg === "--no-provider-logs")) return true
  return false
}

const ulid = () => {
  const now = Date.now()
  const time = now.toString(32).padStart(8, "0")
  const rand = crypto.randomBytes(10).toString("hex").slice(0, 16)
  return `${time}${rand}`.toUpperCase()
}

const toHeaderArray = (collection) => {
  const result = []
  for (const [name, value] of collection.entries()) {
    const lowered = name.toLowerCase()
    if (REDACT_HEADERS.has(lowered)) {
      result.push({ name: lowered, value: "[redacted]" })
    } else {
      result.push({ name, value })
    }
  }
  return result
}

const detectProvider = (url) => {
  try {
    const parsed = new URL(url)
    if (parsed.hostname.includes("anthropic")) return "anthropic"
    if (parsed.hostname.includes("openrouter")) return "openrouter"
    return parsed.hostname
  } catch {
    return "unknown"
  }
}

const readBody = async (body, contentType) => {
  if (!body) {
    return { encoding: "none", size: 0 }
  }
  try {
    if (contentType && /json|text|javascript/.test(contentType)) {
      const text = await body.text()
      if (contentType.includes("json")) {
        try {
          return { encoding: "json", size: text.length, json: JSON.parse(text) }
        } catch {
          return { encoding: "utf8", size: text.length, text }
        }
      }
      return { encoding: "utf8", size: text.length, text }
    }
    const buffer = Buffer.from(await body.arrayBuffer())
    return { encoding: "base64", size: buffer.byteLength, base64: buffer.toString("base64") }
  } catch {
    return { encoding: "error", size: 0 }
  }
}

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true })
  if (warnedAboutDisk) return
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    let total = 0
    for (const entry of entries) {
      if (!entry.isFile()) continue
      const stat = await fs.stat(path.join(dir, entry.name))
      total += stat.size
      if (total > MAX_BYTES) break
    }
    if (total > MAX_BYTES) {
      warnedAboutDisk = true
      console.warn(`[claude-code-logged] Log directory ${dir} exceeded 5GiB. Consider rotating files.`)
    }
  } catch {
    // ignore
  }
}

async function writeStructuredLog(dir, filename, data) {
  try {
    await ensureDir(dir)
    const target = path.join(dir, filename)
    await fs.writeFile(target, JSON.stringify(data, null, 2), "utf8")
  } catch (error) {
    console.warn("[claude-code-logged] Failed to write provider log:", error?.message ?? error)
  }
}

export function installFetchLogger(options = {}) {
  if (shouldDisable()) {
    loggerDisabled = true
    return
  }
  if (globalThis.fetch == null || globalThis.fetch.__CLAUDE_CODE_LOGGED__) {
    return
  }
  const logDir =
    process.env.CLAUDE_CODE_LOG_DIR && process.env.CLAUDE_CODE_LOG_DIR.trim()
      ? process.env.CLAUDE_CODE_LOG_DIR.trim()
      : DEFAULT_DIR
  const workspacePath =
    process.env.CLAUDE_CODE_WORKSPACE && process.env.CLAUDE_CODE_WORKSPACE.trim()
      ? process.env.CLAUDE_CODE_WORKSPACE.trim()
      : process.cwd()
  const sessionId =
    process.env.CLAUDE_CODE_SESSION_ID ||
    process.env.CLAUDE_SESSION_ID ||
    null
  const cliVersion = options.cliVersion ?? "logged"

  const originalFetch = globalThis.fetch.bind(globalThis)

  const logPhase = async (phase, meta) => {
    if (loggerDisabled) return
    const payload = {
      logVersion: 1,
      phase,
      requestId: meta.requestId,
      retryAttempt: meta.retryAttempt ?? 0,
      timestamp: new Date().toISOString(),
      workspacePath,
      sessionId,
      cliVersion,
      runtimeId,
      targetUrl: meta.url,
      provider: detectProvider(meta.url),
      headers: meta.headers,
      body: meta.body,
    }
    if (phase === "response") {
      payload.status = meta.status
    }
    await writeStructuredLog(logDir, meta.filename, payload)
  }

  globalThis.fetch = async function patchedFetch(input, init) {
    const request = new Request(input, init)
    const requestId = ulid()
    const retryAttempt =
      Number(request.headers.get("x-stainless-retry-count")) ||
      Number(request.headers.get("x-retry-attempt")) ||
      0
    const reqHeaders = toHeaderArray(request.headers)
    const reqBody = await readBody(request.clone(), request.headers.get("content-type") || "")
    const requestFile = `${requestId}_request${retryAttempt ? `_retry_${String(retryAttempt).padStart(2, "0")}` : ""}.json`

    logPhase("request", {
      requestId,
      retryAttempt,
      url: request.url,
      headers: reqHeaders,
      body: reqBody,
      filename: requestFile,
    }).catch(() => {})

    const response = await originalFetch(request, init)
    const respClone = response.clone()
    const resHeaders = toHeaderArray(respClone.headers)
    const resBody = await readBody(respClone, respClone.headers.get("content-type") || "")
    const responseFile = `${requestId}_response${retryAttempt ? `_retry_${String(retryAttempt).padStart(2, "0")}` : ""}.json`

    logPhase("response", {
      requestId,
      retryAttempt,
      url: request.url,
      headers: resHeaders,
      body: resBody,
      filename: responseFile,
      status: {
        code: response.status,
        ok: response.ok,
        statusText: response.statusText,
      },
    }).catch(() => {})

    return response
  }

  globalThis.fetch.__CLAUDE_CODE_LOGGED__ = true
}

export async function logStructuredEvent(data) {
  if (loggerDisabled || shouldDisable()) return
  const dir =
    process.env.CLAUDE_CODE_LOG_DIR && process.env.CLAUDE_CODE_LOG_DIR.trim()
      ? process.env.CLAUDE_CODE_LOG_DIR.trim()
      : DEFAULT_DIR
  await writeStructuredLog(dir, `${data.requestId}_${data.phase || "event"}.json`, data)
}
