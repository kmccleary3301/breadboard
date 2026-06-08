import { promises as fs } from "node:fs"
import path from "node:path"
import { SLASH_COMMAND_REGISTRY, type SlashCommandRegistryEntry } from "../../src/repl/slashCommandRegistry.js"

interface CommandReport {
  readonly schemaVersion: "bb.slash-command-report.v1"
  readonly generatedFrom: "src/repl/slashCommandRegistry.ts"
  readonly commandCount: number
  readonly commands: readonly SlashCommandRegistryEntry[]
}

const formatCommand = (command: SlashCommandRegistryEntry): string[] => {
  const lines = [
    `### /${command.name}${command.usage ? ` ${command.usage}` : ""}`,
    "",
    `- category: ${command.category}`,
    `- visibility: ${command.visibility}`,
    `- availability: ${command.availability}`,
    `- dispatch: ${command.dispatchKind}`,
    `- history: ${command.historyBehavior}`,
    `- summary: ${command.summary}`,
  ]
  if (command.aliases?.length) lines.push(`- aliases: ${command.aliases.map((alias) => `/${alias}`).join(", ")}`)
  if (command.shortcut) lines.push(`- shortcut: ${command.shortcut}`)
  if (command.disabledReason) lines.push(`- disabled reason: ${command.disabledReason}`)
  if (command.argumentSchema?.length) {
    lines.push("- arguments:")
    for (const arg of command.argumentSchema) {
      const flags = [arg.required ? "required" : "optional", arg.variadic ? "variadic" : null, arg.kind]
        .filter(Boolean)
        .join(", ")
      const values = arg.values?.length ? `; values: ${arg.values.join("|")}` : ""
      const description = arg.description ? `; ${arg.description}` : ""
      lines.push(`  - ${arg.name}: ${flags}${values}${description}`)
    }
  } else {
    lines.push("- arguments: none")
  }
  return lines
}

export const buildSlashCommandReport = (): CommandReport => ({
  schemaVersion: "bb.slash-command-report.v1",
  generatedFrom: "src/repl/slashCommandRegistry.ts",
  commandCount: SLASH_COMMAND_REGISTRY.length,
  commands: SLASH_COMMAND_REGISTRY,
})

export const buildSlashCommandMarkdown = (): string => {
  const commands = [...SLASH_COMMAND_REGISTRY].sort((a, b) => a.order - b.order)
  return [
    "# BreadBoard Slash Command Registry",
    "",
    "Generated from `src/repl/slashCommandRegistry.ts`. Do not hand-edit generated command data.",
    "",
    `Command count: ${commands.length}`,
    "",
    ...commands.flatMap((command) => [...formatCommand(command), ""]),
  ].join("\n")
}

const run = async () => {
  const outDir = path.resolve(process.argv[2] ?? "docs/generated")
  await fs.mkdir(outDir, { recursive: true })
  const report = buildSlashCommandReport()
  await fs.writeFile(path.join(outDir, "slash_commands.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "slash_commands.md"), `${buildSlashCommandMarkdown()}\n`, "utf8")
  process.stdout.write(`wrote ${path.join(outDir, "slash_commands.json")} and ${path.join(outDir, "slash_commands.md")}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
