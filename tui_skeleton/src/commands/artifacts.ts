import { Command, Args, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import path from "node:path"
import { formatFileList } from "./files.js"
import { getCliApi, reportApiCommandError } from "./commandRuntime.js"
import { normalizeTableJsonOutputMode } from "./commandOutput.js"
import { renderSavedToLine } from "./commandListDetail.js"
import { printCommandPresentation } from "./commandPresentation.js"

const sessionArg = Args.text({ name: "session-id" })
const artifactOption = Options.text("artifact").pipe(Options.withDefault("conversation"))
const outOption = Options.text("out").pipe(Options.optional)

const listOption = Options.text("path").pipe(Options.optional)
const outputOption = Options.text("output").pipe(Options.withDefault("table"))

const listCommand = Command.make("list", { session: sessionArg, path: listOption, output: outputOption }, ({ session, path, output }) =>
  Effect.tryPromise(async () => {
    try {
      const scope = Option.getOrNull(path) ?? "logging"
      const files = await getCliApi().listSessionFiles(session, scope)
      const mode = normalizeTableJsonOutputMode(output)
      await printCommandPresentation({ mode, jsonValue: files, text: formatFileList(files) })
    } catch (error) {
      await reportApiCommandError("list artifacts", error)
      throw error
    }
  }),
)

const downloadCommand = Command.make("download", { session: sessionArg, artifact: artifactOption, out: outOption }, ({ session, artifact, out }) =>
  Effect.tryPromise(async () => {
    try {
      const content = await getCliApi().downloadArtifact(session, artifact)
      const outValue = Option.getOrNull(out)
      if (outValue && outValue.trim().length > 0) {
        const target = path.resolve(outValue)
        await fs.mkdir(path.dirname(target), { recursive: true })
        await fs.writeFile(target, content, "utf8")
        await Console.log(renderSavedToLine("Artifact", target))
      } else {
        await Console.log(content)
      }
    } catch (error) {
      await reportApiCommandError("download artifact", error)
      throw error
    }
  }),
)

export const artifactsCommand = Command.make("artifacts", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([listCommand, downloadCommand]),
)
