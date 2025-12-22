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

const root = Command.make("kyle", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([
    askCommand,
    replCommand,
    resumeCommand,
    sessionsCommand,
    filesCommand,
    artifactsCommand,
    renderCommand,
    Command.make("config", {}, () => Console.log("config command not yet implemented")),
  ]),
)

const cli = Command.run(root, { name: "breadboard CLI", version: "0.2.0" })

const defaultedToRepl = process.argv.length <= 2
const argv = defaultedToRepl ? [...process.argv.slice(0, 2), "repl"] : process.argv
if (defaultedToRepl) {
  console.log("Defaulting to the REPL workspace (use --help for other commands).")
}

cli(argv)
  .pipe(Effect.provide(NodeContext.layer), NodeRuntime.runMain)
