import { Command, Args, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import path from "node:path"
import type { SessionFileInfo } from "../api/types.js"
import { getCliApi, reportApiCommandError } from "./commandRuntime.js"
import { normalizeTableJsonOutputMode } from "./commandOutput.js"
import { renderSimpleTable } from "./commandTable.js"
import { printCommandPresentation } from "./commandPresentation.js"
import { reportValidationError } from "./commandValidation.js"
import { renderActionItemsLine, renderSavedToLine } from "./commandListDetail.js"

const sessionArg = Args.text({ name: "session-id" })
const pathArg = Args.text({ name: "path" }).pipe(Args.optional)
const catPathArg = Args.text({ name: "path" })
const outputOption = Options.text("output").pipe(Options.withDefault("table"))
const catOutOption = Options.text("out").pipe(Options.optional)

export const formatFileList = (files: SessionFileInfo[]): string => {
  if (files.length === 0) {
    return "(empty)"
  }
  return renderSimpleTable(
    ["Type", "Path", "Size"],
    files.map((file) => [file.type, file.path, file.size != null ? String(file.size) : "-"]),
  )
}

const readStdin = async (): Promise<string> =>
  new Promise<string>((resolve, reject) => {
    const { stdin } = process
    if (stdin.isTTY) {
      reject(new Error("No stdin available; provide --diff or --diff-file."))
      return
    }
    stdin.setEncoding("utf8")
    let result = ""
    stdin.on("data", (chunk) => {
      result += chunk
    })
    stdin.on("end", () => resolve(result))
    stdin.on("error", (error) => reject(error))
  })

export const summarizeDiffFiles = (diffText: string): string[] => {
  const files = new Set<string>()
  const lines = diffText.split(/\r?\n/)
  for (const line of lines) {
    if (line.startsWith("+++ ") || line.startsWith("--- ")) {
      const candidate = line.slice(4).trim()
      if (!candidate || candidate === "/dev/null") continue
      const normalized = candidate.replace(/^a\//, "").replace(/^b\//, "")
      if (normalized.length > 0) {
        files.add(normalized)
      }
    }
  }
  return Array.from(files).sort()
}

const filesLsCommand = Command.make("ls", { session: sessionArg, path: pathArg, output: outputOption }, ({ session, path, output }) =>
  Effect.tryPromise(async () => {
    try {
      const pathValue = Option.getOrNull(path)
      const files = await getCliApi().listSessionFiles(session, pathValue ?? undefined)
      const mode = normalizeTableJsonOutputMode(output)
      await printCommandPresentation({ mode, jsonValue: files, text: formatFileList(files) })
    } catch (error) {
      await reportApiCommandError("list files", error)
      throw error
    }
  }),
)

const filesCatCommand = Command.make("cat", { session: sessionArg, file: catPathArg, out: catOutOption }, ({ session, file, out }) =>
  Effect.tryPromise(async () => {
    try {
      const result = await getCliApi().readSessionFile(session, file)
      const outValue = Option.getOrNull(out)
      if (outValue && outValue.trim().length > 0) {
        const target = path.resolve(outValue)
        await fs.mkdir(path.dirname(target), { recursive: true })
        await fs.writeFile(target, result.content, "utf8")
        await Console.log(renderSavedToLine("File", target))
      } else {
        await Console.log(result.content)
      }
    } catch (error) {
      await reportApiCommandError("read file", error)
      throw error
    }
  }),
)

const diffOption = Options.text("diff").pipe(Options.optional)
const diffFileOption = Options.text("diff-file").pipe(Options.optional)

export const filesApplyCommand = Command.make(
  "apply",
  { session: sessionArg, diff: diffOption, diffFile: diffFileOption },
  ({ session, diff, diffFile }) =>
    Effect.tryPromise(async () => {
      try {
        const diffValue = Option.getOrNull(diff)
        const diffFileValue = Option.getOrNull(diffFile)
        let payloadDiff = diffValue ?? null
        if (!payloadDiff && diffFileValue) {
          payloadDiff = diffFileValue === "-" ? await readStdin() : await fs.readFile(diffFileValue, "utf8")
        }
        if (!payloadDiff) {
          await reportValidationError("Provide --diff text or --diff-file path")
          return
        }
        if (payloadDiff.trim().length === 0) {
          await reportValidationError("Diff content is empty; nothing to apply.")
          return
        }
        const affected = summarizeDiffFiles(payloadDiff)
        await Console.log(
          renderActionItemsLine("Applying diff affecting", affected, "Applying diff (no file headers detected)."),
        )
        await getCliApi().postCommand(session, {
          command: "apply_diff",
          payload: { diff: payloadDiff },
        })
        await Console.log("Diff applied")
      } catch (error) {
        await reportApiCommandError("apply diff", error)
        throw error
      }
    }),
)

export const filesCommand = Command.make("files", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([filesLsCommand, filesCatCommand, filesApplyCommand]),
)
