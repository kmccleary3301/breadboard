import { Command, Args, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import path from "node:path"
import { ApiError } from "../api/client.js"
import { formatFileList } from "./files.js"
import { CliProviders } from "../providers/cliProviders.js"

const sessionArg = Args.text({ name: "session-id" })
const artifactOption = Options.text("artifact").pipe(Options.withDefault("conversation"))
const outOption = Options.text("out").pipe(Options.optional)

const listOption = Options.text("path").pipe(Options.optional)
const outputOption = Options.text("output").pipe(Options.withDefault("table"))

const listCommand = Command.make("list", { session: sessionArg, path: listOption, output: outputOption }, ({ session, path, output }) =>
  Effect.tryPromise(async () => {
    try {
      const scope = Option.getOrNull(path) ?? "logging"
      const files = await CliProviders.sdk.api().listSessionFiles(session, scope)
      if (output === "json") {
        await Console.log(JSON.stringify(files, null, 2))
      } else {
        await Console.log(formatFileList(files))
      }
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`Failed to list artifacts (status ${error.status})`)
        if (error.body) {
          await Console.error(JSON.stringify(error.body))
        }
      } else {
        await Console.error((error as Error).message)
      }
      throw error
    }
  }),
)

const downloadCommand = Command.make("download", { session: sessionArg, artifact: artifactOption, out: outOption }, ({ session, artifact, out }) =>
  Effect.tryPromise(async () => {
    try {
      const content = await CliProviders.sdk.api().downloadArtifact(session, artifact)
      const outValue = Option.getOrNull(out)
      if (outValue && outValue.trim().length > 0) {
        const target = path.resolve(outValue)
        await fs.mkdir(path.dirname(target), { recursive: true })
        await fs.writeFile(target, content, "utf8")
        await Console.log(`Artifact saved to ${target}`)
      } else {
        await Console.log(content)
      }
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`Failed to download artifact (status ${error.status})`)
        if (error.body) {
          await Console.error(JSON.stringify(error.body))
        }
      } else {
        await Console.error((error as Error).message)
      }
      throw error
    }
  }),
)

export const artifactsCommand = Command.make("artifacts", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([listCommand, downloadCommand]),
)
