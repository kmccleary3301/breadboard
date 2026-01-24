import dotenv from "dotenv"
import { homedir } from "node:os"
import path from "node:path"
import { promises as fs } from "node:fs"
import { Effect, Layer, Context } from "effect"
import { loadUserConfigSync } from "./userConfig.js"

dotenv.config()

export interface AppConfig {
  readonly baseUrl: string
  readonly authToken: string | undefined
  readonly sessionCachePath: string
  readonly remoteStreamDefault: boolean
  readonly requestTimeoutMs: number
  readonly streamSchema: number | null
  readonly streamIncludeLegacy: boolean | null
}

const DEFAULT_BASE_URL = "http://127.0.0.1:9099"
const DEFAULT_TIMEOUT_MS = 30_000
export const DEFAULT_CONFIG_PATH =
  process.env.BREADBOARD_DEFAULT_CONFIG ?? "agent_configs/opencode_openrouter_grok4fast_cli_default.yaml"
const FALLBACK_MODEL_ID = "openrouter/x-ai/grok-4-fast"

export const DEFAULT_MODEL_ID = process.env.BREADBOARD_DEFAULT_MODEL?.trim() || FALLBACK_MODEL_ID

const resolveCachePath = (): string => {
  const explicit = process.env.BREADBOARD_SESSION_CACHE
  if (explicit && explicit.trim().length > 0) {
    return path.resolve(explicit)
  }
  return path.join(homedir(), ".breadboard", "sessions.json")
}

const computeConfig = (): AppConfig => {
  const userConfig = loadUserConfigSync()
  const baseUrl = process.env.BREADBOARD_API_URL?.trim() || userConfig.baseUrl || DEFAULT_BASE_URL
  const authToken = process.env.BREADBOARD_API_TOKEN?.trim() || userConfig.authToken
  const remoteEnv = process.env.BREADBOARD_ENABLE_REMOTE_STREAM
  const remoteStreamDefault = remoteEnv === undefined ? true : remoteEnv === "1"
  const timeout = Number(process.env.BREADBOARD_API_TIMEOUT_MS ?? DEFAULT_TIMEOUT_MS)
  const schemaEnv =
    process.env.BREADBOARD_STREAM_SCHEMA ??
    process.env.BREADBOARD_TUI_STREAM_SCHEMA ??
    process.env.BREADBOARD_OPENTUI_STREAM_SCHEMA
  const parsedSchema = schemaEnv ? Number(schemaEnv) : NaN
  const streamSchema = Number.isFinite(parsedSchema) ? parsedSchema : null
  const includeLegacyEnv =
    process.env.BREADBOARD_STREAM_INCLUDE_LEGACY ??
    process.env.BREADBOARD_TUI_STREAM_INCLUDE_LEGACY ??
    process.env.BREADBOARD_OPENTUI_STREAM_INCLUDE_LEGACY
  const streamIncludeLegacy =
    includeLegacyEnv === undefined
      ? null
      : includeLegacyEnv === "1" || includeLegacyEnv.toLowerCase() === "true"
  return {
    baseUrl,
    authToken,
    sessionCachePath: resolveCachePath(),
    remoteStreamDefault,
    requestTimeoutMs: Number.isFinite(timeout) && timeout > 0 ? timeout : DEFAULT_TIMEOUT_MS,
    streamSchema,
    streamIncludeLegacy,
  }
}

export const AppConfigTag = Context.GenericTag<AppConfig>("AppConfig")

export const AppConfigLayer = Layer.effect(
  AppConfigTag,
  Effect.sync(computeConfig).pipe(
    Effect.tap(({ sessionCachePath }) =>
      Effect.tryPromise({
        try: async () => {
          const dir = path.dirname(sessionCachePath)
          await fs.mkdir(dir, { recursive: true })
        },
        catch: (error) => error as Error,
      }),
    ),
  ),
)

export const loadAppConfig = (): AppConfig => computeConfig()
