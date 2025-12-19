import { Command, Args, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { promises as fs } from "node:fs"
import path from "node:path"
import { ApiError } from "../api/client.js"
import type { SessionFileInfo } from "../api/types.js"
import { CliProviders } from "../providers/cliProviders.js"

const sessionArg = Args.text({ name: "session-id" })
const pathArg = Args.text({ name: "path" }).pipe(Args.optional)
const catPathArg = Args.text({ name: "path" })
const outputOption = Options.text("output").pipe(Options.withDefault("table"))
const catOutOption = Options.text("out").pipe(Options.optional)

export const formatFileList = (files: SessionFileInfo[]): string => {
  if (files.length === 0) {
    return "(empty)"
  }
  const headers = ["Type", "Path", "Size"]
  const data = files.map((file) => [file.type, file.path, file.size != null ? String(file.size) : "-"])
  const widths = headers.map((header, index) => Math.max(header.length, ...data.map((row) => row[index].length)))
  const formatRow = (row: string[]) => row.map((cell, index) => cell.padEnd(widths[index], " ")).join("  ")
  return [formatRow(headers), formatRow(widths.map((w) => "".padEnd(w, "-"))), ...data.map(formatRow)].join("\n")
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
      const files = await CliProviders.sdk.api().listSessionFiles(session, pathValue ?? undefined)
      if (output === "json") {
        await Console.log(JSON.stringify(files, null, 2))
      } else {
        await Console.log(formatFileList(files))
      }
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`Failed to list files (status ${error.status})`)
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

const filesCatCommand = Command.make("cat", { session: sessionArg, file: catPathArg, out: catOutOption }, ({ session, file, out }) =>
  Effect.tryPromise(async () => {
    try {
      const result = await CliProviders.sdk.api().readSessionFile(session, file)
      const outValue = Option.getOrNull(out)
      if (outValue && outValue.trim().length > 0) {
        const target = path.resolve(outValue)
        await fs.mkdir(path.dirname(target), { recursive: true })
        await fs.writeFile(target, result.content, "utf8")
        await Console.log(`File saved to ${target}`)
      } else {
        await Console.log(result.content)
      }
    } catch (error) {
      if (error instanceof ApiError) {
        await Console.error(`Failed to read file (status ${error.status})`)
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
          await Console.error("Provide --diff text or --diff-file path")
          return
        }
        if (payloadDiff.trim().length === 0) {
          await Console.error("Diff content is empty; nothing to apply.")
          return
        }
        const affected = summarizeDiffFiles(payloadDiff)
        if (affected.length > 0) {
          await Console.log(`Applying diff affecting ${affected.join(", ")}`)
        } else {
          await Console.log("Applying diff (no file headers detected).")
        }
        await CliProviders.sdk.api().postCommand(session, {
          command: "apply_diff",
          payload: { diff: payloadDiff },
        })
        await Console.log("Diff applied")
      } catch (error) {
        if (error instanceof ApiError) {
          await Console.error(`Failed to apply diff (status ${error.status})`)
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

export const filesCommand = Command.make("files", {}, () => Effect.succeed(undefined)).pipe(
  Command.withSubcommands([filesLsCommand, filesCatCommand, filesApplyCommand]),
)
