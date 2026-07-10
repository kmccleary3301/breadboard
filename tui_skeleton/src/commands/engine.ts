import { Args, Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { loadAppConfig } from "../config/appConfig.js"
import { loadUserConfigSync } from "../config/userConfig.js"
import { ensureEngine, getEngineLogPath, startEngineDetached, stopEngineFromLock, shutdownEngine } from "../engine/engineSupervisor.js"
import { resolveEngineLifecycleMode } from "../engine/lifecycleMode.js"
import { CLI_PROTOCOL_VERSION } from "../config/version.js"
import { spawn } from "node:child_process"
import fs from "node:fs"
import { formatApiFailure, getCliApi } from "./commandRuntime.js"
import {
  renderEngineLogsLine,
  renderEngineServeLine,
  renderEngineShutdownLine,
  renderEngineStartedLine,
  renderNoManagedEngineStoppedLine,
  renderStoppedEngineLine,
  renderPressCtrlCToStopLine,
} from "./commandLifecycle.js"
import { renderLabeledLine } from "./commandText.js"

const hostOption = Options.text("host").pipe(Options.withDefault("127.0.0.1"))
const portOption = Options.integer("port").pipe(Options.withDefault(9099))
const followOption = Options.boolean("follow").pipe(Options.optional)
const linesOption = Options.integer("lines").pipe(Options.withDefault(200))

const formatBool = (value: boolean | null | undefined): string =>
  value === null || value === undefined ? "unknown" : value ? "yes" : "no"

const formatNumber = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value) ? "unknown" : String(value)

const reportFailure = (label: string, error: unknown): void => {
  for (const line of formatApiFailure(label, error)) {
    console.error(line)
  }
}

const statusCommand = Command.make("status", {}, () =>
  Effect.tryPromise(async () => {
    const api = getCliApi()
    const appConfig = loadAppConfig()
    const userConfig = loadUserConfigSync()
    const lifecycle = resolveEngineLifecycleMode({
      configMode: userConfig.engineMode,
      envMode: process.env.BREADBOARD_ENGINE_MODE,
      baseUrl: appConfig.baseUrl,
      explicitBaseUrlConfigured: Boolean(process.env.BREADBOARD_API_URL?.trim() || userConfig.baseUrl?.trim()),
    })
    console.log(["Engine status", renderLabeledLine("Base URL", appConfig.baseUrl)].join("\n"))
    console.log(renderLabeledLine("Lifecycle mode", `${lifecycle.mode} (${lifecycle.modeSource})`))
    console.log(renderLabeledLine("Owned by TUI", lifecycle.owned ? "yes" : "no"))
    console.log(renderLabeledLine("Restart policy", lifecycle.restartPolicy))
    console.log(renderLabeledLine("Log path", getEngineLogPath()))
    try {
      const health = await api.health()
      console.log(
        `Health: ${health.status} (protocol ${health.protocol_version ?? "unknown"}, version ${health.version ?? "unknown"})`,
      )
      if (health.served_revision?.commit) {
        const dirtySuffix = health.served_revision.dirty ? " dirty" : ""
        console.log(renderLabeledLine("Served revision", `${health.served_revision.commit}${dirtySuffix}`))
      }
      if (health.served_revision?.repo_root) {
        console.log(renderLabeledLine("Served root", health.served_revision.repo_root))
      }
      if (health.started_at) {
        console.log(renderLabeledLine("Started at", health.started_at))
      }
    } catch (error) {
      reportFailure("Health check failed", error)
      return
    }

    try {
      const info = await api.engineStatus()
      console.log(renderLabeledLine("PID", formatNumber(info.pid)))
      console.log(renderLabeledLine("Uptime (s)", formatNumber(info.uptime_s)))
      console.log(renderLabeledLine("Ray available", formatBool(info.ray?.available)))
      console.log(renderLabeledLine("Ray initialized", formatBool(info.ray?.initialized)))
      if (info.served_revision?.branch) {
        console.log(renderLabeledLine("Branch", info.served_revision.branch))
      }
      if (info.protocol_version && info.protocol_version !== CLI_PROTOCOL_VERSION) {
        console.log(renderLabeledLine("Protocol mismatch", `engine ${info.protocol_version} vs cli ${CLI_PROTOCOL_VERSION}`))
      }
    } catch (error) {
      reportFailure("/status failed", error)
    }
  }),
)

const startCommand = Command.make(
  "start",
  {
    host: hostOption,
    port: portOption,
  },
  ({ host, port }) =>
    Effect.tryPromise(async () => {
      process.env.BREADBOARD_ENGINE_KEEPALIVE = "1"
      process.env.RAY_DISABLE_DASHBOARD ??= "1"

      const result = await startEngineDetached({ host, port })
      console.log(renderEngineStartedLine(result.baseUrl, result.pid))
      console.log(renderEngineLogsLine(result.logPath))
    }),
)

const serveCommand = Command.make(
  "serve",
  {
    host: hostOption,
    port: portOption,
  },
  ({ host, port }) =>
    Effect.tryPromise(async () => {
      const normalizedHost = host.trim() || "127.0.0.1"
      const normalizedPort = Number.isFinite(port) && port > 0 ? port : 9099
      process.env.BREADBOARD_API_URL = `http://${normalizedHost}:${normalizedPort}`
      process.env.BREADBOARD_ENGINE_KEEPALIVE = "1"
      process.env.RAY_DISABLE_DASHBOARD ??= "1"

      const { baseUrl, pid, started } = await ensureEngine({ allowSpawn: true, isolated: false })
      console.log(renderEngineServeLine(baseUrl, pid, started))
      console.log(renderPressCtrlCToStopLine())

      await new Promise<void>((resolve) => {
        process.once("SIGINT", () => resolve())
        process.once("SIGTERM", () => resolve())
      })

      process.env.BREADBOARD_ENGINE_KEEPALIVE = "0"
      const stopped = await shutdownEngine({ timeoutMs: 5_000, force: true }).catch(() => false)
      console.log(renderEngineShutdownLine(stopped))
    }),
)

const logsCommand = Command.make(
  "logs",
  {
    follow: followOption,
    lines: linesOption,
  },
  ({ follow, lines }) =>
    Effect.tryPromise(async () => {
      const logPath = getEngineLogPath()
      const followEnabled = Option.match(follow, { onNone: () => false, onSome: (value) => value })
      const lineCount = Number.isFinite(lines) && lines > 0 ? lines : 200

      console.log(renderLabeledLine("Log path", logPath))
      if (!fs.existsSync(logPath)) {
        console.log("No log file found yet.")
        return
      }

      if (followEnabled) {
        const child = spawn("tail", ["-n", String(lineCount), "-f", logPath], { stdio: "inherit" })
        await new Promise<void>((resolve) => child.once("exit", () => resolve()))
        return
      }

      const output = spawn("tail", ["-n", String(lineCount), logPath], { stdio: "inherit" })
      await new Promise<void>((resolve) => output.once("exit", () => resolve()))
    }),
)

const stopCommand = Command.make(
  "stop",
  {},
  () =>
    Effect.tryPromise(async () => {
      const result = await stopEngineFromLock({ timeoutMs: 5_000, force: true })
      if (result.stopped) {
        console.log(renderStoppedEngineLine(result.pid))
        return
      }
      console.log(renderNoManagedEngineStoppedLine(result.pid))
    }),
)

export const engineCommand = Command.make("engine", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([
    statusCommand,
    startCommand,
    serveCommand,
    logsCommand,
    stopCommand,
  ]),
)
