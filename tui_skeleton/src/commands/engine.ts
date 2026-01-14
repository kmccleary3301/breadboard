import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { ApiClient, ApiError } from "../api/client.js"
import { loadAppConfig } from "../config/appConfig.js"
import { ensureEngine, getEngineLogPath, startEngineDetached, stopEngineFromLock, shutdownEngine } from "../engine/engineSupervisor.js"
import { CLI_PROTOCOL_VERSION } from "../config/version.js"
import { spawn } from "node:child_process"
import fs from "node:fs"

const hostOption = Options.text("host").pipe(Options.withDefault("127.0.0.1"))
const portOption = Options.integer("port").pipe(Options.withDefault(9099))
const followOption = Options.boolean("follow").pipe(Options.optional)
const linesOption = Options.integer("lines").pipe(Options.withDefault(200))

const formatBool = (value: boolean | null | undefined): string =>
  value === null || value === undefined ? "unknown" : value ? "yes" : "no"

const formatNumber = (value: number | null | undefined): string =>
  value === null || value === undefined || !Number.isFinite(value) ? "unknown" : String(value)

const statusCommand = Command.make("status", {}, () =>
  Effect.tryPromise(async () => {
    const appConfig = loadAppConfig()
    await Console.log("Engine status")
    await Console.log(`Base URL: ${appConfig.baseUrl}`)
    try {
      const health = await ApiClient.health()
      await Console.log(
        `Health: ${health.status} (protocol ${health.protocol_version ?? "unknown"}, version ${health.version ?? "unknown"})`,
      )
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`Health check failed (${error.status}).`)
      } else {
        await Console.error(`Health check failed: ${(error as Error).message}`)
      }
      return
    }

    try {
      const info = await ApiClient.engineStatus()
      await Console.log(`PID: ${formatNumber(info.pid)}`)
      await Console.log(`Uptime (s): ${formatNumber(info.uptime_s)}`)
      await Console.log(`Ray available: ${formatBool(info.ray?.available)}`)
      await Console.log(`Ray initialized: ${formatBool(info.ray?.initialized)}`)
      if (info.protocol_version && info.protocol_version !== CLI_PROTOCOL_VERSION) {
        await Console.log(`Protocol mismatch: engine ${info.protocol_version} vs cli ${CLI_PROTOCOL_VERSION}`)
      }
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`/status failed (${error.status}).`)
      } else {
        await Console.error(`/status failed: ${(error as Error).message}`)
      }
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
      await Console.log(`Engine started: ${result.baseUrl} (pid ${result.pid})`)
      await Console.log(`Logs: ${result.logPath}`)
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
      await Console.log(`Engine: ${baseUrl}${pid ? ` (pid ${pid})` : ""}${started ? " [started]" : ""}`)
      await Console.log("Press Ctrl-C to stop.")

      await new Promise<void>((resolve) => {
        process.once("SIGINT", () => resolve())
        process.once("SIGTERM", () => resolve())
      })

      process.env.BREADBOARD_ENGINE_KEEPALIVE = "0"
      const stopped = await shutdownEngine({ timeoutMs: 5_000, force: true }).catch(() => false)
      await Console.log(stopped ? "Engine stopped." : "Engine stop requested.")
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

      await Console.log(`Log path: ${logPath}`)
      if (!fs.existsSync(logPath)) {
        await Console.log("No log file found yet.")
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
        await Console.log(`Stopped engine${result.pid ? ` (pid ${result.pid})` : ""}.`)
        return
      }
      await Console.log(`No managed engine stopped${result.pid ? ` (pid ${result.pid})` : ""}.`)
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
