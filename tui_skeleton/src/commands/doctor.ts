import { Command, Options } from "@effect/cli"
import { Console, Effect } from "effect"
import { existsSync } from "node:fs"
import type { HealthResponse } from "../api/types.js"
import { DEFAULT_CONFIG_PATH, loadAppConfig } from "../config/appConfig.js"
import { getUserConfigPath } from "../config/userConfig.js"
import { resolveAuthToken } from "../config/authTokenProvider.js"
import { resolveBreadboardRepoPath } from "../utils/paths.js"
import { shutdownEngine } from "../engine/engineSupervisor.js"
import { getCliApi, reportApiFailure } from "./commandRuntime.js"
import { renderOptionalReportLine } from "./commandReport.js"
import { renderDetailPresentation } from "./commandDetailPresentation.js"

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_CONFIG_PATH))

const formatValue = (value: string | number | null | undefined): string =>
  value === null || value === undefined || value === "" ? "unknown" : String(value)

const maskToken = (token?: string): string => {
  if (!token) return "not set"
  if (token.length <= 6) return "***"
  return `${token.slice(0, 3)}…${token.slice(-3)}`
}

export const resolveDoctorAuthLabel = async (baseUrl: string): Promise<string> => maskToken(await resolveAuthToken(baseUrl))

export const doctorCommand = Command.make(
  "doctor",
  {
    config: configOption,
  },
  ({ config }) =>
    Effect.tryPromise(async () => {
      const api = getCliApi()
      const appConfig = loadAppConfig()
      const configPath = resolveBreadboardRepoPath(config)
      const configExists = existsSync(configPath)
      const userConfigPath = getUserConfigPath()

      const headerLines = [
        `Base URL: ${appConfig.baseUrl}`,
        `Auth token: ${await resolveDoctorAuthLabel(appConfig.baseUrl)}`,
        `User config: ${userConfigPath}`,
        `Config path: ${configPath}${configExists ? "" : " (missing)"}`,
      ]

      let health: HealthResponse | null = null
      try {
        health = await api.health()
        await Console.log(
          `Engine health: ${formatValue(health.status)} (protocol ${formatValue(health.protocol_version)})`,
        )
      } catch (error) {
        await reportApiFailure("Engine health check failed", error)
      }

      try {
        const catalog = await api.getModelCatalog(configPath)
        await Console.log(`Models: ${catalog.models.length} (default: ${formatValue(catalog.default_model)})`)
      } catch (error) {
        await reportApiFailure("Model catalog failed", error)
      }

      const reportLines = [
        ...headerLines,
        health?.protocol_version ? `Protocol version: ${health.protocol_version}` : null,
        health?.served_revision?.commit
          ? `Served revision: ${health.served_revision.commit}${health.served_revision.dirty ? " dirty" : ""}`
          : null,
        health?.served_revision?.repo_root ? `Served root: ${health.served_revision.repo_root}` : null,
        health?.started_at ? `Started at: ${health.started_at}` : null,
      ].filter((line): line is string => line !== null)

      const sections = [] as Array<{ title: string; lines: string[] }>
      try {
        const status = await api.engineStatus()
        const branchLine = renderOptionalReportLine("Served branch", status.served_revision?.branch)
        if (branchLine) {
          reportLines.push(branchLine)
        }
        const eventlog = status.eventlog
        if (eventlog) {
          const enabled = eventlog.enabled ? "enabled" : "disabled"
          sections.push({
            title: "Event log",
            lines: [
              `Status: ${enabled}${eventlog.dir ? ` (${eventlog.dir})` : ""}`,
              ...(eventlog.bootstrap ? [`Bootstrap: ${eventlog.bootstrap}`] : []),
              ...(eventlog.replay ? [`Replay: ${eventlog.replay}`] : []),
              ...(eventlog.max_mb ? [`Max MB: ${eventlog.max_mb}`] : []),
              ...(eventlog.sessions !== undefined && eventlog.sessions !== null ? [`Sessions: ${eventlog.sessions}`] : []),
              ...(eventlog.last_activity ? [`Last activity: ${eventlog.last_activity}`] : []),
              ...(eventlog.capped ? ["Capped: true"] : []),
            ],
          })
        }
        const index = status.session_index
        if (index) {
          const idxEnabled = index.enabled ? "enabled" : "disabled"
          const engineLabel = index.engine ? ` engine=${index.engine}` : ""
          sections.push({
            title: "Session index",
            lines: [
              `Status: ${idxEnabled}${engineLabel}${index.dir ? ` (${index.dir})` : ""}`,
              ...(index.sessions !== undefined && index.sessions !== null ? [`Sessions: ${index.sessions}`] : []),
              ...(index.last_activity ? [`Last activity: ${index.last_activity}`] : []),
            ],
          })
        }
      } catch (error) {
        await reportApiFailure("Engine status failed", error)
      }
      await Console.log(renderDetailPresentation("Breadboard doctor", { lines: reportLines, sections }))
      await shutdownEngine().catch(() => undefined)
    }),
)
