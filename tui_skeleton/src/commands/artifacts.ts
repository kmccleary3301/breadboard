import { Command, Args, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import path from "node:path"
import type { PublicResult } from "@breadboard/sdk"
import { getCliApi, reportApiCommandError } from "./commandRuntime.js"
import { normalizeTableJsonOutputMode } from "./commandOutput.js"
import { renderSimpleTable } from "./commandTable.js"
import { renderSavedToLine } from "./commandListDetail.js"
import { printCommandPresentation } from "./commandPresentation.js"

const sessionArg = Args.text({ name: "session-id" })
const artifactOption = Options.text("artifact").pipe(Options.withDefault("conversation"))
const outOption = Options.text("out").pipe(Options.optional)

const outputOption = Options.text("output").pipe(Options.withDefault("table"))

const listCommand = Command.make("list", { session: sessionArg, output: outputOption }, ({ session, output }) =>
  Effect.tryPromise(async () => {
    try {
      const result = (await getCliApi().invokePublicAction("public.session.artifacts", { session_id: session })) as PublicResult
      const artifacts = result.data.artifacts
      if (!Array.isArray(artifacts)) {
        throw new Error("Session artifact response is missing data.artifacts")
      }
      const mode = normalizeTableJsonOutputMode(output)
      const text = artifacts.length === 0
        ? "(empty)"
        : renderSimpleTable(
            ["Name", "Digest", "Size", "Media Type"],
            artifacts.map((artifact) => {
              const item = artifact as Record<string, unknown>
              return [String(item.name ?? "-"), String(item.digest ?? "-"), String(item.size ?? "-"), String(item.media_type ?? "-")]
            }),
          )
      await printCommandPresentation({ mode, jsonValue: artifacts, text })
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
        console.log(renderSavedToLine("Artifact", target))
      } else {
        console.log(content)
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
