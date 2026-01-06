import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { getUserConfigPath, loadUserConfigSync, writeUserConfig } from "../config/userConfig.js"

const tokenOption = Options.text("token").pipe(Options.optional)
const clearTokenOption = Options.boolean("clear-token").pipe(Options.optional)

const normalizeUrl = (value: string): string => {
  const trimmed = value.trim()
  if (!trimmed) {
    throw new Error("URL is empty.")
  }
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
  let parsed: URL
  try {
    parsed = new URL(withScheme)
  } catch {
    throw new Error(`Invalid URL: ${value}`)
  }
  if (!parsed.hostname) {
    throw new Error(`Invalid URL: ${value}`)
  }
  return parsed.toString().replace(/\/$/, "")
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
      const normalized = normalizeUrl(url)
      const existing = loadUserConfigSync()
      const tokenValue = Option.getOrNull(token)
      const clearTokenValue = Option.match(clearToken, {
        onNone: () => false,
        onSome: (value) => value,
      })

      const next = {
        ...existing,
        baseUrl: normalized,
        ...(tokenValue ? { authToken: tokenValue } : {}),
      }
      if (clearTokenValue) {
        delete (next as { authToken?: string }).authToken
      }
      await writeUserConfig(next)

      const configPath = getUserConfigPath()
      const tokenNote = tokenValue
        ? "Token saved."
        : clearTokenValue
          ? "Token cleared."
          : existing.authToken
            ? "Token preserved."
            : "No token configured."
      await Console.log(`Saved engine URL to ${configPath}\nBase URL: ${normalized}\n${tokenNote}`)
    }),
)
