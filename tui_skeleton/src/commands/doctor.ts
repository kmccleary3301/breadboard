import { Command, Options } from "@effect/cli"
import { Console, Effect } from "effect"
import { existsSync } from "node:fs"
import { ApiClient, ApiError } from "../api/client.js"
import type { HealthResponse } from "../api/types.js"
import { DEFAULT_CONFIG_PATH, loadAppConfig } from "../config/appConfig.js"
import { getUserConfigPath } from "../config/userConfig.js"
import { resolveBreadboardPath } from "../utils/paths.js"
import { shutdownEngine } from "../engine/engineSupervisor.js"

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_CONFIG_PATH))

const formatValue = (value: string | number | null | undefined): string =>
  value === null || value === undefined || value === "" ? "unknown" : String(value)

const maskToken = (token?: string): string => {
  if (!token) return "not set"
  if (token.length <= 6) return "***"
  return `${token.slice(0, 3)}…${token.slice(-3)}`
}

export const doctorCommand = Command.make(
  "doctor",
  {
    config: configOption,
  },
  ({ config }) =>
    Effect.tryPromise(async () => {
      const appConfig = loadAppConfig()
      const configPath = resolveBreadboardPath(config)
      const configExists = existsSync(configPath)
      const userConfigPath = getUserConfigPath()

      await Console.log("Breadboard doctor")
      await Console.log(`Base URL: ${appConfig.baseUrl}`)
      await Console.log(`Auth token: ${maskToken(appConfig.authToken)}`)
      await Console.log(`User config: ${userConfigPath}`)
      await Console.log(`Config path: ${configPath}${configExists ? "" : " (missing)"}`)

      let health: HealthResponse | null = null
      try {
        health = await ApiClient.health()
        await Console.log(
          `Engine health: ${formatValue(health.status)} (protocol ${formatValue(health.protocol_version)})`,
        )
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Engine health check failed (${error.status}).`)
        } else {
          await Console.error(`Engine health check failed: ${(error as Error).message}`)
        }
      }

      try {
        const catalog = await ApiClient.getModelCatalog(configPath)
        await Console.log(
          `Models: ${catalog.models.length} (default: ${formatValue(catalog.default_model)})`,
        )
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Model catalog failed (${error.status}).`)
        } else {
          await Console.error(`Model catalog failed: ${(error as Error).message}`)
        }
      }

      if (health?.protocol_version) {
        await Console.log(`Protocol version: ${health.protocol_version}`)
      }
      await shutdownEngine().catch(() => undefined)
    }),
)
