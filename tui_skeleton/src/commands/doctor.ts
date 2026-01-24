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
      try {
        const status = await ApiClient.engineStatus()
        const eventlog = status.eventlog
        if (eventlog) {
          const enabled = eventlog.enabled ? "enabled" : "disabled"
          await Console.log(`Event log: ${enabled}${eventlog.dir ? ` (${eventlog.dir})` : ""}`)
          if (eventlog.bootstrap) {
            await Console.log(`Event log bootstrap: ${eventlog.bootstrap}`)
          }
          if (eventlog.replay) {
            await Console.log(`Event log replay: ${eventlog.replay}`)
          }
          if (eventlog.max_mb) {
            await Console.log(`Event log max MB: ${eventlog.max_mb}`)
          }
          if (eventlog.sessions !== undefined && eventlog.sessions !== null) {
            await Console.log(`Event log sessions: ${eventlog.sessions}`)
          }
          if (eventlog.last_activity) {
            await Console.log(`Event log last activity: ${eventlog.last_activity}`)
          }
          if (eventlog.capped) {
            await Console.log("Event log capped: true")
          }
        }
        const index = status.session_index
        if (index) {
          const idxEnabled = index.enabled ? "enabled" : "disabled"
          const engineLabel = index.engine ? ` engine=${index.engine}` : ""
          await Console.log(`Session index: ${idxEnabled}${engineLabel}${index.dir ? ` (${index.dir})` : ""}`)
          if (index.sessions !== undefined && index.sessions !== null) {
            await Console.log(`Session index sessions: ${index.sessions}`)
          }
          if (index.last_activity) {
            await Console.log(`Session index last activity: ${index.last_activity}`)
          }
        }
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Engine status failed (${error.status}).`)
        } else {
          await Console.error(`Engine status failed: ${(error as Error).message}`)
        }
      }
      await shutdownEngine().catch(() => undefined)
    }),
)
