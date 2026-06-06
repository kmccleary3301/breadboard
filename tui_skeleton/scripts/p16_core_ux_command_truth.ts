import { promises as fs } from "node:fs"
import path from "node:path"
import { buildSlashCommandMarkdown, buildSlashCommandReport } from "../tools/reports/slashCommandReport.js"
import { SLASH_COMMAND_REGISTRY, type SlashCommandRegistryEntry } from "../src/repl/slashCommandRegistry.js"

export type P16CommandDisposition = "implemented" | "bounded" | "deferred" | "hidden" | "future"

interface P16CommandTruthRow {
  readonly name: string
  readonly owner: SlashCommandRegistryEntry["category"]
  readonly category: SlashCommandRegistryEntry["category"]
  readonly visibility: SlashCommandRegistryEntry["visibility"]
  readonly availability: SlashCommandRegistryEntry["availability"]
  readonly dispatchKind: SlashCommandRegistryEntry["dispatchKind"]
  readonly disposition: P16CommandDisposition
  readonly state: string
  readonly modelSubmissionPolicy: string
  readonly historyBehavior: SlashCommandRegistryEntry["historyBehavior"]
  readonly usage: string
  readonly aliases: readonly string[]
  readonly disabledReason: string
  readonly evidence: readonly string[]
  readonly notes: string
}

interface P16CommandTruthReport {
  readonly schemaVersion: "bb.p16.command-truth.v1"
  readonly generatedAt: string
  readonly generatedFrom: readonly string[]
  readonly commandCount: number
  readonly visibleCount: number
  readonly experimentalCount: number
  readonly hiddenOrDebugCount: number
  readonly dispositionCounts: Record<P16CommandDisposition, number>
  readonly rows: readonly P16CommandTruthRow[]
  readonly aliasCollisions: readonly string[]
  readonly warnings: readonly string[]
}

const P16_ROOT = "../docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete"

const dispositionFor = (command: SlashCommandRegistryEntry): P16CommandDisposition => {
  if (command.visibility === "hidden" || command.visibility === "debug") return "hidden"
  if (command.availability === "feature-gated" || command.dispatchKind === "deferred") return "deferred"
  if (command.visibility === "experimental") return "bounded"
  return "implemented"
}

const stateFor = (command: SlashCommandRegistryEntry, disposition: P16CommandDisposition): string => {
  if (disposition === "deferred") return "feature-gated fail-closed; addressable but not visible by default"
  if (disposition === "hidden") return command.visibility === "debug" ? "debug-local hidden from visible suggestions" : "hidden from visible suggestions"
  if (disposition === "bounded") return "available bounded MVP surface; intentionally hidden from root suggestions until broader phase evidence"
  return command.dispatchKind === "controller" ? "controller command path" : "local UI command path"
}

const modelSubmissionPolicyFor = (command: SlashCommandRegistryEntry): string => {
  if (command.dispatchKind === "ui-local" || command.dispatchKind === "debug-local") return "handled locally; must not call model on Enter"
  if (command.dispatchKind === "deferred") return "fail-closed locally; must not call model on Enter"
  return "sent as a controller command, not as arbitrary prompt text"
}

const evidenceFor = (command: SlashCommandRegistryEntry, disposition: P16CommandDisposition): string[] => {
  const evidence = ["src/repl/slashCommandRegistry.ts"]
  if (command.dispatchKind === "ui-local" || command.dispatchKind === "debug-local" || command.dispatchKind === "deferred") {
    evidence.push("src/repl/components/replView/controller/useReplCommands.ts")
    evidence.push("src/repl/components/replView/controller/__tests__/useReplCommands.attachmentSemantics.test.tsx")
  }
  if (command.argumentSchema?.length) evidence.push("src/repl/__tests__/slashCommandRegistry.test.ts")
  if (disposition === "implemented") evidence.push("tests/slashCommandReport.test.ts")
  return evidence
}

const notesFor = (command: SlashCommandRegistryEntry, disposition: P16CommandDisposition): string => {
  if (command.name === "goal") return "Deferred until durable goal persistence, stop conditions, and completion audit semantics are productized."
  if (command.name === "fork") return "Deferred until backend session graph, checkpoint, and recovery semantics are first-class."
  if (command.name === "diff") return "Bounded read-only working-tree diff first, transcript-diff fallback otherwise; file/hunk/export/copy maturity accepted bounded; approval workflow semantics remain Phase F."
  if (command.name === "permissions") return "Bounded read-only permission status; policy editing remains Phase F/future until productized."
  if (command.name === "agents") return "Bounded task dashboard entrypoint; production multiagent depth remains Phase G."
  if (disposition === "hidden") return "Callable for diagnostics where applicable, but intentionally omitted from visible user suggestions."
  if (command.dispatchKind === "controller") return "Controller command path is intentionally separate from plain prompt submission."
  return "Local UI command path."
}

const buildRows = (): P16CommandTruthRow[] =>
  [...SLASH_COMMAND_REGISTRY]
    .sort((a, b) => a.order - b.order)
    .map((command) => {
      const disposition = dispositionFor(command)
      return {
        name: `/${command.name}`,
        owner: command.category,
        category: command.category,
        visibility: command.visibility,
        availability: command.availability,
        dispatchKind: command.dispatchKind,
        disposition,
        state: stateFor(command, disposition),
        modelSubmissionPolicy: modelSubmissionPolicyFor(command),
        historyBehavior: command.historyBehavior,
        usage: command.usage ?? "",
        aliases: command.aliases?.map((alias) => `/${alias}`) ?? [],
        disabledReason: command.disabledReason ?? "",
        evidence: evidenceFor(command, disposition),
        notes: notesFor(command, disposition),
      }
    })

const collectAliasCollisions = (): string[] => {
  const names = new Set(SLASH_COMMAND_REGISTRY.map((command) => command.name))
  const collisions: string[] = []
  for (const command of SLASH_COMMAND_REGISTRY) {
    for (const alias of command.aliases ?? []) {
      if (names.has(alias)) collisions.push(`/${command.name} alias /${alias} collides with exact command; exact command wins`)
    }
  }
  return collisions.sort()
}

const validateRegistryTruth = (rows: readonly P16CommandTruthRow[]): string[] => {
  const errors: string[] = []
  const names = new Set<string>()
  for (const command of SLASH_COMMAND_REGISTRY) {
    if (names.has(command.name)) errors.push(`duplicate command name: /${command.name}`)
    names.add(command.name)
    if (!command.summary.trim()) errors.push(`missing summary: /${command.name}`)
    if (command.availability === "feature-gated" && !command.disabledReason?.trim()) {
      errors.push(`feature-gated command lacks disabledReason: /${command.name}`)
    }
    if (command.dispatchKind === "deferred" && command.availability !== "feature-gated") {
      errors.push(`deferred command must be feature-gated: /${command.name}`)
    }
    if (command.usage && !command.argumentSchema?.length) {
      errors.push(`usage exists without argument schema: /${command.name}`)
    }
    if (command.visibility === "visible" && command.dispatchKind === "deferred") {
      errors.push(`visible command cannot be deferred: /${command.name}`)
    }
  }
  const knownRows = new Set(rows.map((row) => row.name.slice(1)))
  for (const command of SLASH_COMMAND_REGISTRY) {
    if (!knownRows.has(command.name)) errors.push(`missing report row: /${command.name}`)
  }
  return errors
}

export const buildP16CommandTruthReport = (generatedAt = new Date().toISOString()): P16CommandTruthReport => {
  const rows = buildRows()
  const dispositionCounts = rows.reduce<Record<P16CommandDisposition, number>>(
    (acc, row) => {
      acc[row.disposition] += 1
      return acc
    },
    { implemented: 0, bounded: 0, deferred: 0, hidden: 0, future: 0 },
  )
  const warnings = collectAliasCollisions()
  return {
    schemaVersion: "bb.p16.command-truth.v1",
    generatedAt,
    generatedFrom: ["src/repl/slashCommandRegistry.ts", "tools/reports/slashCommandReport.ts"],
    commandCount: rows.length,
    visibleCount: rows.filter((row) => row.visibility === "visible").length,
    experimentalCount: rows.filter((row) => row.visibility === "experimental").length,
    hiddenOrDebugCount: rows.filter((row) => row.visibility === "hidden" || row.visibility === "debug").length,
    dispositionCounts,
    rows,
    aliasCollisions: warnings,
    warnings,
  }
}

const table = (headers: readonly string[], rows: readonly (readonly string[])[]): string => {
  const escape = (value: string) => value.replace(/\|/g, "\\|").replace(/\n/g, "<br>")
  return [
    `| ${headers.map(escape).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.map(escape).join(" | ")} |`),
  ].join("\n")
}

export const buildFeatureDispositionMarkdown = (report: P16CommandTruthReport): string => {
  const groups: P16CommandDisposition[] = ["implemented", "bounded", "deferred", "hidden", "future"]
  const lines = [
    "# P16 Feature Disposition Matrix",
    "",
    `Generated: ${report.generatedAt}`,
    "",
    "Generated from the slash command registry. Do not hand-edit command truth without updating the registry or this generator.",
    "",
    "## Summary",
    "",
    table(["Disposition", "Count", "Meaning"], groups.map((group) => [
      group,
      String(report.dispositionCounts[group]),
      group === "implemented" ? "Visible available command with implemented local/controller path."
        : group === "bounded" ? "Available bounded MVP surface, intentionally experimental/hidden from default suggestions until later phase evidence."
        : group === "deferred" ? "Addressable fail-closed command with explicit disabled copy; no model submission."
        : group === "hidden" ? "Hidden/debug command omitted from visible suggestions."
        : "Future command not present as executable UX.",
    ])),
    "",
    "## Rows",
    "",
    table(
      ["Command", "Owner", "Disposition", "State", "Visibility", "Availability", "Dispatch", "Notes"],
      report.rows.map((row) => [row.name, row.owner, row.disposition, row.state, row.visibility, row.availability, row.dispatchKind, row.notes]),
    ),
    "",
    "## Alias Collisions And Warnings",
    "",
    ...(report.warnings.length ? report.warnings.map((warning) => `- ${warning}`) : ["- none"]),
  ]
  return `${lines.join("\n")}\n`
}

export const buildCommandTruthMarkdown = (report: P16CommandTruthReport): string => {
  const lines = [
    "# P16 Command Truth Matrix",
    "",
    `Generated: ${report.generatedAt}`,
    "",
    "This matrix binds every slash command to owner, dispatch, argument, visibility, history, and model-submission semantics.",
    "",
    "## Contract",
    "",
    "- Local, debug-local, deferred, malformed, unavailable, and unknown slash commands must not fall through as model prompts.",
    "- Controller commands are submitted as controller commands, not arbitrary prompt text.",
    "- Feature-gated commands must be addressable, fail closed, clear the composer, and show truthful disabled copy.",
    "- Experimental bounded commands must remain out of default root suggestions until their broader phase claims are earned.",
    "",
    "## Rows",
    "",
    table(
      ["Command", "Usage", "Aliases", "Category", "Dispatch", "History", "Model Submission Policy", "Disabled Reason", "Evidence"],
      report.rows.map((row) => [
        row.name,
        row.usage || "none",
        row.aliases.join(", ") || "none",
        row.category,
        row.dispatchKind,
        row.historyBehavior,
        row.modelSubmissionPolicy,
        row.disabledReason || "none",
        row.evidence.join("; "),
      ]),
    ),
  ]
  return `${lines.join("\n")}\n`
}

const run = async () => {
  const outArg = process.argv.slice(2).find((arg) => arg !== "--")
  const outDir = path.resolve(outArg ?? path.join(P16_ROOT, "artifacts/core_ux/command_truth_latest"))
  await fs.mkdir(outDir, { recursive: true })
  const report = buildP16CommandTruthReport()
  const errors = validateRegistryTruth(report.rows)
  if (errors.length) {
    await fs.writeFile(path.join(outDir, "command_truth_errors.json"), `${JSON.stringify(errors, null, 2)}\n`, "utf8")
    throw new Error(`P16 command truth validation failed: ${errors.join("; ")}`)
  }
  await fs.writeFile(path.join(outDir, "slash_commands.json"), `${JSON.stringify(buildSlashCommandReport(), null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "slash_commands.md"), `${buildSlashCommandMarkdown()}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "command_truth_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "feature_disposition_matrix.md"), buildFeatureDispositionMarkdown(report), "utf8")
  await fs.writeFile(path.join(outDir, "command_truth_matrix.md"), buildCommandTruthMarkdown(report), "utf8")

  const p16Root = path.resolve(P16_ROOT)
  await fs.writeFile(path.join(p16Root, "feature_disposition_matrix.md"), buildFeatureDispositionMarkdown(report), "utf8")
  await fs.writeFile(path.join(p16Root, "command_truth_matrix.md"), buildCommandTruthMarkdown(report), "utf8")
  process.stdout.write(`wrote P16 command truth artifacts to ${outDir}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
