#!/usr/bin/env node
import { Command } from "@effect/cli"
import { NodeContext, NodeRuntime } from "@effect/platform-node"
import { Effect } from "effect"
import { LEGACY_ASCII_HEADER } from "./asciiHeader.js"
import { replClassicCommand, runLegacyShell } from "./replClassicCommand.js"

const runCli = () => {
  const root = Command.make("old-repl", {}, () => Effect.succeed(undefined)).pipe(Command.withSubcommands([replClassicCommand]))
  const cli = Command.run(root, { name: "breadboard classic repl", version: "0.1.0" })
  cli(process.argv)
    .pipe(Effect.provide(NodeContext.layer), NodeRuntime.runMain)
}

const args = process.argv.slice(2)

if (args.length === 0) {
  console.log(LEGACY_ASCII_HEADER)
  runLegacyShell().catch((error) => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
} else {
  runCli()
}
