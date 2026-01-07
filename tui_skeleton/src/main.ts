#!/usr/bin/env node
import { Args, Command, Options } from "@effect/cli"
import { NodeContext, NodeRuntime } from "@effect/platform-node"
import { Console, Effect } from "effect"
import { askCommand } from "./commands/ask.js"
import { replCommand } from "./commands/repl.js"
import { resumeCommand } from "./commands/resume.js"
import { sessionsCommand } from "./commands/sessions.js"
import { filesCommand } from "./commands/files.js"
import { artifactsCommand } from "./commands/artifacts.js"
import { renderCommand } from "./commands/render.js"
import { doctorCommand } from "./commands/doctor.js"
import { uiCommand } from "./commands/ui.js"
import { runCommand } from "./commands/run.js"
import { connectCommand } from "./commands/connect.js"
import { engineCommand } from "./commands/engine.js"
import { pluginCommand } from "./commands/plugin.js"
import { authCommand } from "./commands/auth.js"
import { ensureEngine } from "./engine/engineSupervisor.js"
import { loadAppConfig } from "./config/appConfig.js"
import { CLI_VERSION } from "./config/version.js"

const root = Command.make("breadboard", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([
    askCommand,
    replCommand,
    uiCommand,
    resumeCommand,
    sessionsCommand,
    filesCommand,
    artifactsCommand,
    renderCommand,
    runCommand,
    doctorCommand,
    connectCommand,
    engineCommand,
    pluginCommand,
    authCommand,
    Command.make("config", {}, () => Console.log("config command not yet implemented")),
  ]),
)

const cli = Command.run(root, { name: "breadboard", version: CLI_VERSION })

const defaultedToRepl = process.argv.length <= 2
const argv = defaultedToRepl ? [...process.argv.slice(0, 2), "repl"] : process.argv
if (defaultedToRepl) {
  console.log("Defaulting to the REPL workspace (use --help for other commands).")
}

const shouldSkipEngine = (args: string[]): boolean => {
  const command = args[2]
  if (!command) return false
  if (command.startsWith("-")) return true
  if (command === "connect" || command === "config" || command === "engine" || command === "auth") return true
  if (args.includes("--help") || args.includes("-h") || args.includes("--version") || args.includes("-v")) {
    return true
  }
  return false
}

const isLocalBaseUrl = (value: string): boolean => {
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    return host === "localhost" || host === "127.0.0.1" || host === "::1"
  } catch {
    return false
  }
}

const runCli = async () => {
  const command = argv[2]
  if (!shouldSkipEngine(argv)) {
    try {
      const config = loadAppConfig()
      const wantsIsolation =
        command === "run" &&
        process.env.BREADBOARD_ENGINE_ISOLATED !== "0" &&
        isLocalBaseUrl(config.baseUrl)
      await ensureEngine({ isolated: wantsIsolation })
    } catch (error) {
      if (command === "doctor") {
        console.warn((error as Error).message)
      } else {
        console.error((error as Error).message)
        process.exit(1)
      }
    }
  }
  cli(argv).pipe(Effect.provide(NodeContext.layer), NodeRuntime.runMain)
}

runCli().catch((error) => {
  console.error((error as Error).message)
  process.exit(1)
})
