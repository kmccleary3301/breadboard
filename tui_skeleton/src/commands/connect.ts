import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import type { UserConfigFile } from "../config/userConfig.js"
import { getUserConfigPath, loadUserConfigSync, writeUserConfig } from "../config/userConfig.js"
import { normalizeBaseUrl } from "../config/baseUrl.js"
const tokenOption = Options.text("token").pipe(Options.optional)
const clearTokenOption = Options.boolean("clear-token").pipe(Options.optional)

const isBaseUrlChange = (existing: UserConfigFile, baseUrl: string): boolean => {
  if (!existing.baseUrl) return false
  try {
    return normalizeBaseUrl(existing.baseUrl) !== baseUrl
  } catch {
    return existing.baseUrl !== baseUrl
  }
}

const isUrlBoundKeychainRef = (authTokenRef: string | undefined, baseUrl: string): boolean => {
  const account = authTokenRef?.startsWith("keychain:") ? authTokenRef.slice("keychain:".length).trim() : ""
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(account)) return false
  try {
    return normalizeBaseUrl(account) !== baseUrl
  } catch {
    return false
  }
}

export const buildConnectConfig = (
  existing: UserConfigFile,
  baseUrl: string,
  tokenValue: string | null,
  clearTokenValue: boolean,
): UserConfigFile => {
  const next: UserConfigFile = {
    ...existing,
    baseUrl,
    ...(tokenValue ? { authToken: tokenValue } : {}),
  }
  if (tokenValue || clearTokenValue) {
    delete (next as { authToken?: string }).authToken
    delete (next as { authTokenRef?: string }).authTokenRef
  } else if ((existing.authTokenRef === "keychain" && isBaseUrlChange(existing, baseUrl)) || isUrlBoundKeychainRef(existing.authTokenRef, baseUrl)) {
    delete (next as { authTokenRef?: string }).authTokenRef
  }
  if (tokenValue) {
    return { ...next, authToken: tokenValue }
  }
  return next
}

export const connectCommand = Command.make(
  "connect",
  {
    url: Args.text({ name: "url" }),
    token: tokenOption,
    clearToken: clearTokenOption,
  },
  ({ url, token, clearToken }) =>
    Effect.tryPromise(async () => {
      const normalized = normalizeBaseUrl(url)
      const existing = loadUserConfigSync()
      const tokenValue = Option.getOrNull(token)
      const clearTokenValue = Option.match(clearToken, {
        onNone: () => false,
        onSome: (value) => value,
      })

      const next = buildConnectConfig(existing, normalized, tokenValue, clearTokenValue)
      await writeUserConfig(next)

      const configPath = getUserConfigPath()
      const tokenNote = tokenValue
        ? "Token saved."
        : clearTokenValue
          ? "Token cleared."
          : next.authToken || next.authTokenRef
            ? "Token preserved."
            : "No token configured."
      await Console.log(`Saved engine URL to ${configPath}\nBase URL: ${normalized}\n${tokenNote}`)
    }),
)
