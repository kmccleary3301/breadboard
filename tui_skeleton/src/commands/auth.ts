import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { loadAppConfig } from "../config/appConfig.js"
import { getUserConfigPath, loadUserConfigSync, writeUserConfig } from "../config/userConfig.js"
import { deleteKeychainAuthToken, getKeychainAuthToken, isKeychainAvailable, setKeychainAuthToken } from "../config/keychain.js"

const urlOption = Options.text("url").pipe(Options.optional)

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

      await Console.log(`Stored token in keychain.\nBase URL: ${baseUrl}\nConfig: ${getUserConfigPath()}`)
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

      const note = removed ? "Keychain entry removed." : "No keychain entry found (or keychain unavailable)."
      await Console.log(`${note}\nBase URL: ${baseUrl}\nConfig: ${getUserConfigPath()}`)
    }),
)

const authStatusCommand = Command.make(
  "status",
  {
    url: urlOption,
  },
  ({ url }) =>
    Effect.tryPromise(async () => {
      const baseUrl = resolveBaseUrl(url)
      const user = loadUserConfigSync()
      const envToken = process.env.BREADBOARD_API_TOKEN?.trim()
      const keychainToken = await getKeychainAuthToken(baseUrl)
      const keychainOk = await isKeychainAvailable()

      const lines = [
        `Base URL: ${baseUrl}`,
        `Env token: ${envToken ? "set" : "unset"}`,
        `Config token: ${user.authToken ? "set" : "unset"}`,
        `Config token ref: ${user.authTokenRef ? user.authTokenRef : "unset"}`,
        `Keychain available: ${keychainOk ? "yes" : "no"}`,
        `Keychain token: ${keychainToken ? "set" : "unset"}`,
      ]
      await Console.log(lines.join("\n"))
    }),
)

export const authCommand = Command.make("auth", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([authSetCommand, authClearCommand, authStatusCommand]),
)

