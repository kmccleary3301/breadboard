import { Args, Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import chalk from "chalk"
import { homedir } from "node:os"
import path from "node:path"
import fs from "node:fs"
import { promises as fsp } from "node:fs"
import { spawn } from "node:child_process"
import { createInterface } from "node:readline/promises"
import { stdin as input, stdout as output } from "node:process"
import { createApiClient, ApiError } from "../api/client.js"
import type { ProviderAuthStatusResponse } from "../api/types.js"
import { DEFAULT_CONFIG_PATH, loadAppConfig } from "../config/appConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import { getUserConfigPath, loadUserConfigSync, writeUserConfig } from "../config/userConfig.js"
import { deleteKeychainAuthToken, getKeychainAuthToken, isKeychainAvailable, setKeychainAuthToken } from "../config/keychain.js"
import { ensureEngine } from "../engine/engineSupervisor.js"

const urlOption = Options.text("url").pipe(Options.optional)
const configOption = Options.text("config").pipe(Options.optional)
const providerOption = Options.text("provider").pipe(Options.optional)
const methodOption = Options.text("method").pipe(Options.optional)
const apiKeyOption = Options.text("api-key").pipe(Options.optional)
const noBrowserOption = Options.boolean("no-browser").pipe(Options.withDefault(false))
const forceOption = Options.boolean("force").pipe(Options.withDefault(false))

type LoginMethod = "chatgpt-browser" | "chatgpt-force-browser" | "api-key"
type ProviderId = "openai" | "anthropic" | "openrouter" | "google" | "github" | "vercel"

interface LoginRequest {
  readonly baseUrl: string
  readonly configPath: string
  readonly provider: ProviderId
  readonly method: LoginMethod
  readonly noBrowser: boolean
  readonly force: boolean
  readonly apiKey?: string
}

interface CodexSubscriptionStoreEntry {
  readonly token: string
  readonly updated_at: string
  readonly source: string
  readonly codex_auth_path?: string
}

interface CodexSubscriptionStore {
  readonly by_base_url: Record<string, CodexSubscriptionStoreEntry>
}

const COLORS = {
  ok: "#22c55e",
  warn: "#f59e0b",
  err: "#ff4d7d",
  info: "#38bdf8",
  muted: "#94a3b8",
  title: "#e94f9a",
}

const CODEX_AUTH_PATH = path.join(homedir(), ".codex", "auth.json")
const AUTH_STORE_DIR = path.join(homedir(), ".breadboard", "auth")
const CODEX_SUBSCRIPTION_STORE_PATH = path.join(AUTH_STORE_DIR, "codex_subscription_tokens.json")

const cOk = (value: string) => chalk.hex(COLORS.ok)(value)
const cWarn = (value: string) => chalk.hex(COLORS.warn)(value)
const cErr = (value: string) => chalk.hex(COLORS.err)(value)
const cInfo = (value: string) => chalk.hex(COLORS.info)(value)
const cMuted = (value: string) => chalk.hex(COLORS.muted)(value)
const cTitle = (value: string) => chalk.hex(COLORS.title)(value)

const normalizeUrl = (value: string): string => {
  const trimmed = value.trim()
  if (!trimmed) {
    throw new Error("URL is empty.")
  }
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
  const parsed = new URL(withScheme)
  if (!parsed.hostname) {
    throw new Error(`Invalid URL: ${value}`)
  }
  return parsed.toString().replace(/\/$/, "")
}

const resolveBaseUrl = (override: Option.Option<string>): string => {
  const raw = Option.getOrNull(override)
  if (raw) return normalizeUrl(raw)
  return loadAppConfig().baseUrl
}

const resolveConfigPath = (override: Option.Option<string>): string => {
  const raw = Option.getOrNull(override)?.trim()
  if (!raw) return DEFAULT_CONFIG_PATH
  return path.isAbsolute(raw) ? raw : path.resolve(raw)
}

const isLocalHost = (host: string): boolean => {
  const value = host.trim().toLowerCase()
  return value === "localhost" || value === "127.0.0.1" || value === "::1"
}

const parseProvider = (value: string | null | undefined): ProviderId | null => {
  if (!value) return null
  const normalized = value.trim().toLowerCase()
  if (!normalized) return null
  if (normalized === "openai") return "openai"
  if (normalized === "anthropic") return "anthropic"
  if (normalized === "openrouter") return "openrouter"
  if (normalized === "google") return "google"
  if (normalized === "github" || normalized === "github-copilot" || normalized === "copilot") return "github"
  if (normalized === "vercel" || normalized === "vercel-ai-gateway") return "vercel"
  return null
}

const parseMethod = (value: string | null | undefined): LoginMethod | null => {
  if (!value) return null
  const normalized = value.trim().toLowerCase()
  if (!normalized) return null
  if (normalized === "chatgpt-browser" || normalized === "browser" || normalized === "chatgpt") {
    return "chatgpt-browser"
  }
  if (normalized === "chatgpt-force-browser" || normalized === "chatgpt-browser-force" || normalized === "force") {
    return "chatgpt-force-browser"
  }
  if (normalized === "api-key" || normalized === "key") return "api-key"
  return null
}

const providerLabel = (provider: ProviderId): string => {
  switch (provider) {
    case "openai":
      return "OpenAI"
    case "anthropic":
      return "Anthropic"
    case "openrouter":
      return "OpenRouter"
    case "google":
      return "Google"
    case "github":
      return "GitHub Copilot"
    case "vercel":
      return "Vercel AI Gateway"
    default:
      return provider
  }
}

const keychainLockedMessage = (error: unknown): boolean => {
  const message = (error as Error)?.message ?? String(error)
  return /locked/i.test(message) && /keychain|collection|secret/i.test(message)
}

const codexSubscriptionAccount = (baseUrl: string): string => `provider-auth:openai:subscription:${baseUrl}`

const readCodexSubscriptionStore = async (): Promise<CodexSubscriptionStore> => {
  try {
    const raw = await fsp.readFile(CODEX_SUBSCRIPTION_STORE_PATH, "utf8")
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { by_base_url: {} }
    const record = parsed as Record<string, unknown>
    const byBase = record.by_base_url
    if (!byBase || typeof byBase !== "object" || Array.isArray(byBase)) return { by_base_url: {} }
    const next: Record<string, CodexSubscriptionStoreEntry> = {}
    for (const [key, value] of Object.entries(byBase as Record<string, unknown>)) {
      if (!value || typeof value !== "object" || Array.isArray(value)) continue
      const row = value as Record<string, unknown>
      const token = typeof row.token === "string" ? row.token.trim() : ""
      if (!token) continue
      next[key] = {
        token,
        updated_at: typeof row.updated_at === "string" ? row.updated_at : new Date().toISOString(),
        source: typeof row.source === "string" ? row.source : "fallback_file",
        codex_auth_path: typeof row.codex_auth_path === "string" ? row.codex_auth_path : undefined,
      }
    }
    return { by_base_url: next }
  } catch {
    return { by_base_url: {} }
  }
}

const writeCodexSubscriptionStore = async (store: CodexSubscriptionStore): Promise<void> => {
  await fsp.mkdir(AUTH_STORE_DIR, { recursive: true })
  await fsp.writeFile(CODEX_SUBSCRIPTION_STORE_PATH, `${JSON.stringify(store, null, 2)}\n`, { encoding: "utf8", mode: 0o600 })
}

const persistCodexSubscriptionToken = async (
  baseUrl: string,
  token: string,
): Promise<"keychain" | "fallback_file" | "none"> => {
  const keychainOk = await isKeychainAvailable()
  if (keychainOk) {
    try {
      await setKeychainAuthToken(codexSubscriptionAccount(baseUrl), token)
      return "keychain"
    } catch (error) {
      if (!keychainLockedMessage(error)) {
        throw error
      }
    }
  }
  const store = await readCodexSubscriptionStore()
  const next: CodexSubscriptionStore = {
    by_base_url: {
      ...store.by_base_url,
      [baseUrl]: {
        token,
        updated_at: new Date().toISOString(),
        source: "codex_access_token",
        codex_auth_path: CODEX_AUTH_PATH,
      },
    },
  }
  await writeCodexSubscriptionStore(next)
  return "fallback_file"
}

const clearCodexSubscriptionToken = async (baseUrl: string): Promise<void> => {
  await deleteKeychainAuthToken(codexSubscriptionAccount(baseUrl))
  const store = await readCodexSubscriptionStore()
  if (store.by_base_url[baseUrl]) {
    const next = { ...store.by_base_url }
    delete next[baseUrl]
    await writeCodexSubscriptionStore({ by_base_url: next })
  }
}

const readCodexTokenFromAuthFile = async (): Promise<string | null> => {
  let raw = ""
  try {
    raw = await fsp.readFile(CODEX_AUTH_PATH, "utf8")
  } catch {
    return null
  }
  const parsed = JSON.parse(raw) as unknown
  if (!parsed || typeof parsed !== "object") return null
  const priorityKeys = [
    "codex_access_token",
    "access_token",
    "id_token",
    "token",
    "auth_token",
  ]
  const queue: unknown[] = [parsed]
  const seen = new Set<unknown>()
  while (queue.length > 0) {
    const node = queue.shift()
    if (!node || typeof node !== "object") continue
    if (seen.has(node)) continue
    seen.add(node)
    const record = node as Record<string, unknown>
    for (const key of priorityKeys) {
      const value = record[key]
      if (typeof value === "string" && value.trim()) {
        return value.trim()
      }
    }
    for (const value of Object.values(record)) {
      if (value && typeof value === "object") queue.push(value)
    }
  }
  return null
}

const runCodexLogin = async (noBrowser: boolean): Promise<void> => {
  const args = ["login"]
  if (noBrowser) args.push("--device-auth")
  await new Promise<void>((resolve, reject) => {
    const child = spawn("codex", args, {
      stdio: "inherit",
      shell: false,
    })
    child.once("error", reject)
    child.once("exit", (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`codex login exited with code ${code ?? "unknown"}`))
      }
    })
  })
}

const buildApi = (baseUrl: string) =>
  createApiClient({
    baseUrl,
    authToken: () => resolveAuthToken(baseUrl),
  })

const ensureLocalEngineForAuth = async (
  baseUrl: string,
  allowSpawn = true,
): Promise<{ baseUrl: string; started: boolean }> => {
  const parsed = new URL(baseUrl)
  if (!isLocalHost(parsed.hostname)) return { baseUrl, started: false }
  const previousUrl = process.env.BREADBOARD_API_URL
  process.env.BREADBOARD_API_URL = baseUrl
  try {
    const ensured = await ensureEngine({ allowSpawn })
    return { baseUrl: ensured.baseUrl, started: ensured.started }
  } finally {
    if (previousUrl == null) {
      delete process.env.BREADBOARD_API_URL
    } else {
      process.env.BREADBOARD_API_URL = previousUrl
    }
  }
}

const attachProviderAuth = async (request: LoginRequest, token: string): Promise<void> => {
  const api = buildApi(request.baseUrl)
  const response = await api.providerAuthAttach({
    material: {
      provider_id: request.provider,
      api_key: token,
      headers: {
        Authorization: `Bearer ${token}`,
      },
      is_subscription_plan: request.provider === "openai" && request.method !== "api-key",
    },
    config_path: request.configPath,
  })
  if (response?.ok === false) {
    throw new Error(`provider-auth attach returned ok=false (${JSON.stringify(response.detail ?? {})})`)
  }
}

const detachProviderAuth = async (baseUrl: string, provider: ProviderId): Promise<void> => {
  const api = buildApi(baseUrl)
  try {
    await api.providerAuthDetach({ provider_id: provider })
  } catch {
    // best-effort detach
  }
}

const providerStatus = async (baseUrl: string): Promise<ProviderAuthStatusResponse | null> => {
  try {
    const api = buildApi(baseUrl)
    return await api.providerAuthStatus()
  } catch {
    return null
  }
}

const labelState = (label: string, set: boolean): string =>
  `${label}: ${set ? cOk("set") : cMuted("unset")}`

const printAuthStatus = async (baseUrl: string): Promise<void> => {
  const user = loadUserConfigSync()
  const envToken = process.env.BREADBOARD_API_TOKEN?.trim()
  const keychainToken = await getKeychainAuthToken(baseUrl)
  const keychainOk = await isKeychainAvailable()
  const codexStore = await readCodexSubscriptionStore()
  const codexFallbackToken = codexStore.by_base_url[baseUrl]?.token?.trim()
  const codexAuthToken = await readCodexTokenFromAuthFile()
  let engineError: string | null = null
  const attached = await providerStatus(baseUrl)
  if (!attached) {
    engineError = `Engine/provider status unavailable at ${baseUrl}`
  }
  const openAiAttached = attached?.attached?.some((item) => item.provider_id === "openai" && item.is_subscription_plan === true) ?? false

  const lines = [
    cTitle("BreadBoard auth status"),
    `${cInfo("Base URL")}: ${baseUrl}`,
    labelState("Env token", Boolean(envToken)),
    labelState("Config token", Boolean(user.authToken)),
    `${cInfo("Config token ref")}: ${user.authTokenRef ? user.authTokenRef : cMuted("unset")}`,
    `${cInfo("Keychain available")}: ${keychainOk ? cOk("yes") : cWarn("no")}`,
    labelState("Keychain token", Boolean(keychainToken)),
    labelState("Local Codex auth", Boolean(codexAuthToken)),
    labelState("Fallback file token", Boolean(codexFallbackToken)),
    `${cInfo("Fallback file path")}: ${CODEX_SUBSCRIPTION_STORE_PATH}`,
  ]
  if (engineError) {
    lines.push(`${cWarn("Provider auth status")}: unavailable (${engineError})`)
  } else {
    const attachedRows = attached?.attached ?? []
    lines.push(`${cInfo("Provider auth attached")}: ${attachedRows.length}`)
    lines.push(`${cInfo("OpenAI subscription attached")}: ${openAiAttached ? cOk("yes") : cMuted("no")}`)
  }
  console.log(lines.join("\n"))
}

const selectMenu = async <T extends string>(
  title: string,
  options: ReadonlyArray<{ readonly label: string; readonly value: T }>,
  fallback: T,
): Promise<T> => {
  if (!input.isTTY || !output.isTTY) return fallback
  const rl = createInterface({ input, output })
  try {
    console.log(cTitle(title))
    for (let index = 0; index < options.length; index += 1) {
      const item = options[index]
      const marker = index === 0 ? cOk("*") : cMuted("-")
      console.log(`  ${marker} ${index + 1}. ${item.label}`)
    }
    const raw = (await rl.question(cInfo("Select option number (Enter for default): "))).trim()
    if (!raw) return fallback
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return fallback
    const selected = options[Math.max(0, Math.min(options.length - 1, Math.floor(parsed) - 1))]
    return selected?.value ?? fallback
  } finally {
    rl.close()
  }
}

const promptText = async (message: string): Promise<string> => {
  const rl = createInterface({ input, output })
  try {
    return (await rl.question(message)).trim()
  } finally {
    rl.close()
  }
}

const resolveLoginInput = async (
  providerRaw: string | null,
  methodRaw: string | null,
): Promise<{ provider: ProviderId; method: LoginMethod }> => {
  const providerParsed = parseProvider(providerRaw)
  const methodParsed = parseMethod(methodRaw)
  if (providerParsed && methodParsed) return { provider: providerParsed, method: methodParsed }
  const provider = providerParsed
    ?? (await selectMenu(
      "Add credential: provider",
      [
        { label: "OpenAI", value: "openai" },
        { label: "Anthropic", value: "anthropic" },
        { label: "OpenRouter", value: "openrouter" },
        { label: "Google", value: "google" },
        { label: "GitHub Copilot", value: "github" },
        { label: "Vercel AI Gateway", value: "vercel" },
      ] as const satisfies ReadonlyArray<{ readonly label: string; readonly value: ProviderId }>,
      "openai",
    ))

  if (methodParsed) return { provider, method: methodParsed }
  if (provider === "openai") {
    const method = await selectMenu(
      "OpenAI login method",
      [
        { label: "ChatGPT Pro/Plus (browser)", value: "chatgpt-browser" },
        { label: "ChatGPT Pro/Plus (browser, force re-login)", value: "chatgpt-force-browser" },
        { label: "Manually enter API key", value: "api-key" },
      ] as const satisfies ReadonlyArray<{ readonly label: string; readonly value: LoginMethod }>,
      "chatgpt-browser",
    )
    return { provider, method }
  }
  return { provider, method: "api-key" }
}

const executeLogin = async (request: LoginRequest): Promise<void> => {
  const ensured = await ensureLocalEngineForAuth(request.baseUrl)
  const effectiveBaseUrl = ensured.baseUrl
  let tokenSource = ""
  let token = request.apiKey?.trim() || ""
  let codexLoginLaunched = false

  if (!token) {
    if (request.provider === "openai" && request.method !== "api-key") {
      if (request.force || request.method === "chatgpt-force-browser") {
        await runCodexLogin(request.noBrowser)
        codexLoginLaunched = true
      }
      token = (await readCodexTokenFromAuthFile()) ?? ""
      if (!token) {
        await runCodexLogin(request.noBrowser)
        codexLoginLaunched = true
        token = (await readCodexTokenFromAuthFile()) ?? ""
      }
      if (!token) {
        throw new Error(`Could not read a usable token from ${CODEX_AUTH_PATH}.`)
      }
      tokenSource = "codex_access_token"
    } else {
      token = await promptText(cInfo("Paste API key (input visible): "))
      if (!token.trim()) {
        throw new Error("No API key provided.")
      }
      tokenSource = "manual_api_key"
    }
  } else {
    tokenSource = "flag_api_key"
  }

  await attachProviderAuth({ ...request, baseUrl: effectiveBaseUrl }, token)

  let persistence = "none"
  if (request.provider === "openai" && request.method !== "api-key") {
    persistence = await persistCodexSubscriptionToken(effectiveBaseUrl, token)
  }

  const status = await providerStatus(effectiveBaseUrl)
  const openAiAttached = status?.attached?.some((item) => item.provider_id === "openai" && item.is_subscription_plan === true) ?? false
  const persistedMsg =
    persistence === "keychain"
      ? cOk("Credential stored in keychain.")
      : persistence === "fallback_file"
      ? cWarn(`Credential stored locally because your keychain is currently locked (${CODEX_SUBSCRIPTION_STORE_PATH}).`)
      : cMuted("Credential not persisted (session-only attach).")
  const verboseAuth = process.env.BREADBOARD_AUTH_VERBOSE === "1"
  const firstLine =
    request.provider === "openai" && request.method !== "api-key"
      ? cOk("OpenAI ChatGPT subscription connected.")
      : cOk(`${providerLabel(request.provider)} credential connected.`)
  const methodLine =
    request.provider === "openai" && request.method !== "api-key"
      ? cInfo(`Completed ChatGPT subscription login using ${request.noBrowser ? "device" : "browser"} flow.`)
      : cInfo("Completed API-key auth attach flow.")

  console.log(
    [
      firstLine,
      methodLine,
      `${cInfo("Engine attach")}: ${openAiAttached || request.provider !== "openai" ? cOk("ok") : cWarn("not confirmed")}.`,
      persistedMsg,
      cMuted("Run `breadboard auth status` for detailed diagnostics."),
      ...(verboseAuth
        ? [
            cMuted(
              request.provider === "openai" && request.method !== "api-key"
                ? `Token source=${tokenSource}; codex_auth=${CODEX_AUTH_PATH}; codex_login_launched=${codexLoginLaunched ? "yes" : "no"}`
                : `Token source=${tokenSource}`,
            ),
          ]
        : []),
    ].join("\n"),
  )
}

const runInteractiveAuth = async (url: Option.Option<string>, config: Option.Option<string>): Promise<void> => {
  const baseUrl = resolveBaseUrl(url)
  const configPath = resolveConfigPath(config)
  const selection = await resolveLoginInput(null, null)
  const request: LoginRequest = {
    baseUrl,
    configPath,
    provider: selection.provider,
    method: selection.method,
    noBrowser: false,
    force: selection.method === "chatgpt-force-browser",
  }
  await executeLogin(request)
}

const authSetCommand = Command.make(
  "set",
  {
    token: Args.text({ name: "token" }),
    url: urlOption,
  },
  ({ token, url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      if (!(await isKeychainAvailable())) {
        throw new Error(
          "Keychain support is not available (optional dependency `keytar` missing). Install it or use BREADBOARD_API_TOKEN.",
        )
      }
      await setKeychainAuthToken(baseUrl, token)
      const existing = loadUserConfigSync()
      const next = { ...existing, baseUrl, authTokenRef: `keychain:${baseUrl}` }
      delete (next as { authToken?: string }).authToken
      await writeUserConfig(next)
      console.log(cOk("Stored API token in keychain."))
      console.log(`${cInfo("Base URL")}: ${baseUrl}`)
      console.log(`${cInfo("Config")}: ${getUserConfigPath()}`)
    }),
)

const authClearCommand = Command.make(
  "clear",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      const removed = await deleteKeychainAuthToken(baseUrl)
      const existing = loadUserConfigSync()
      const next = { ...existing }
      if (next.authTokenRef?.startsWith("keychain:")) {
        delete (next as { authTokenRef?: string }).authTokenRef
      }
      await writeUserConfig(next)
      console.log(removed ? cOk("Keychain entry removed.") : cMuted("No keychain entry found."))
      console.log(`${cInfo("Base URL")}: ${baseUrl}`)
      console.log(`${cInfo("Config")}: ${getUserConfigPath()}`)
    }),
)

const authStatusCommand = Command.make(
  "status",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      await printAuthStatus(resolveBaseUrl(url))
    }),
)

const authListCommand = Command.make(
  "list",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      const status = await providerStatus(baseUrl)
      if (!status) {
        console.log(cWarn(`Provider-auth status unavailable at ${baseUrl}.`))
        return
      }
      const attached = status?.attached ?? []
      if (attached.length === 0) {
        console.log(cMuted("No provider credentials are currently attached in the engine."))
        return
      }
      console.log(cTitle("Attached provider credentials"))
      for (const row of attached) {
        const title = `${providerLabel(parseProvider(row.provider_id) ?? "openrouter")} (${row.provider_id})`
        const flags = [
          row.is_subscription_plan ? "subscription" : "api-key",
          row.has_api_key ? "key=yes" : "key=no",
          row.alias ? `alias=${row.alias}` : null,
        ].filter((value): value is string => Boolean(value))
        console.log(`${cOk("•")} ${title} ${cMuted(flags.join(" · "))}`)
      }
    }),
)

const authLoginCommand = Command.make(
  "login",
  {
    url: urlOption,
    config: configOption,
    provider: providerOption,
    method: methodOption,
    apiKey: apiKeyOption,
    noBrowser: noBrowserOption,
    force: forceOption,
  },
  ({ url, config, provider, method, apiKey, noBrowser, force }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      const configPath = resolveConfigPath(config)
      const providerRaw = Option.getOrNull(provider)
      const methodRaw = Option.getOrNull(method)
      const parsed = await resolveLoginInput(providerRaw, methodRaw)
      const request: LoginRequest = {
        baseUrl,
        configPath,
        provider: parsed.provider,
        method: parsed.method,
        noBrowser,
        force: force || parsed.method === "chatgpt-force-browser",
        apiKey: Option.getOrNull(apiKey) ?? undefined,
      }
      await executeLogin(request)
    }),
)

const codexSubscriptionStatusCommand = Command.make(
  "status",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      const keychainToken = await getKeychainAuthToken(codexSubscriptionAccount(baseUrl))
      const store = await readCodexSubscriptionStore()
      const fallbackToken = store.by_base_url[baseUrl]?.token
      const codexToken = await readCodexTokenFromAuthFile()
      const provider = await providerStatus(baseUrl)
      const statusRows = provider?.attached?.filter((row) => row.provider_id === "openai" && row.is_subscription_plan).length ?? 0
      console.log(cTitle("Codex subscription auth status"))
      console.log(`${cInfo("Base URL")}: ${baseUrl}`)
      console.log(labelState("Codex local token", Boolean(codexToken)))
      console.log(labelState("Keychain token", Boolean(keychainToken)))
      console.log(labelState("Fallback file token", Boolean(fallbackToken)))
      console.log(`${cInfo("Fallback file path")}: ${CODEX_SUBSCRIPTION_STORE_PATH}`)
      console.log(`${cInfo("Engine attached")}: ${statusRows > 0 ? cOk("yes") : cMuted("no")}`)
    }),
)

const codexSubscriptionLoginCommand = Command.make(
  "login",
  {
    url: urlOption,
    config: configOption,
    noBrowser: noBrowserOption,
    force: forceOption,
  },
  ({ url, config, noBrowser, force }) =>
    Effect.tryPromise(async () => {
      const request: LoginRequest = {
        baseUrl: resolveBaseUrl(url),
        configPath: resolveConfigPath(config),
        provider: "openai",
        method: force ? "chatgpt-force-browser" : "chatgpt-browser",
        noBrowser,
        force,
      }
      await executeLogin(request)
    }),
)

const codexSubscriptionClearCommand = Command.make(
  "clear",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      await clearCodexSubscriptionToken(baseUrl)
      await detachProviderAuth(baseUrl, "openai")
      console.log(cOk("Cleared local Codex subscription token cache and detached OpenAI provider auth."))
      console.log(cMuted("Run `breadboard auth codex-subscription status` to confirm state."))
    }),
)

const codexSubscriptionCommand = Command.make("codex-subscription", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([codexSubscriptionLoginCommand, codexSubscriptionStatusCommand, codexSubscriptionClearCommand]),
)

export const authCommand = Command.make(
  "auth",
  {
    url: urlOption,
    config: configOption,
  },
  ({ url, config }) =>
    Effect.tryPromise(async () => {
      await runInteractiveAuth(url, config)
    }),
).pipe(
  Command.withSubcommands([
    authLoginCommand,
    authStatusCommand,
    authListCommand,
    authSetCommand,
    authClearCommand,
    codexSubscriptionCommand,
  ]),
)
