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
import { configCommand } from "./commands/config.js"
import { ensureEngine } from "./engine/engineSupervisor.js"
import { bootstrapProviderEnvironment } from "./config/providerBootstrapEnv.js"
import { CLI_VERSION } from "./config/version.js"
import { computeCliBootPlan, loadCliEnginePlan } from "./cli_bootPlan.js"

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
    configCommand,
  ]),
)

const cli = Command.run(root, { name: "breadboard", version: CLI_VERSION })

const bootPlan = computeCliBootPlan(process.argv)
const argv = bootPlan.argv
if (bootPlan.defaultedToRepl) {
  console.log("Defaulting to the REPL workspace (use --help for other commands).")
}

const runCli = async () => {
  bootstrapProviderEnvironment()
  if (!bootPlan.shouldSkipEngine) {
    try {
      const enginePlan = loadCliEnginePlan(argv)
      await ensureEngine({ isolated: enginePlan.isolated, cliMode: enginePlan.engineMode })
    } catch (error) {
      if (bootPlan.command === "doctor") {
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
