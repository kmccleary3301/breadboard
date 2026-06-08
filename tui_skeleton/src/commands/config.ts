import { Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { stringify } from "yaml"
import { resolveTuiConfig } from "../tui_config/load.js"
import { printReportCommandResult } from "./commandApiPresenter.js"

const workspaceOption = Options.text("workspace").pipe(Options.optional)
const tuiPresetOption = Options.text("tui-preset").pipe(Options.optional)
const tuiConfigOption = Options.text("tui-config").pipe(Options.optional)
const tuiConfigStrictOption = Options.boolean("tui-config-strict").pipe(Options.optional)
const outputOption = Options.choice("output", ["json", "yaml", "summary"] as const).pipe(Options.withDefault("json"))

export const configCommand = Command.make(
  "config",
  {
    workspace: workspaceOption,
    tuiPreset: tuiPresetOption,
    tuiConfig: tuiConfigOption,
    tuiConfigStrict: tuiConfigStrictOption,
    output: outputOption,
  },
  ({ workspace, tuiPreset, tuiConfig, tuiConfigStrict, output }) =>
    Effect.tryPromise(async () => {
      const workspaceValue = Option.getOrNull(workspace)
      const tuiPresetValue = Option.getOrNull(tuiPreset)
      const tuiConfigValue = Option.getOrNull(tuiConfig)
      const strictValue = Option.getOrNull(tuiConfigStrict)
      const resolved = await resolveTuiConfig({
        workspace: workspaceValue ?? process.cwd(),
        cliPreset: tuiPresetValue,
        cliConfigPath: tuiConfigValue,
        cliStrict: strictValue,
      })

      const summaryLines = [
        `preset: ${resolved.preset}`,
        `display.asciiOnly: ${resolved.display.asciiOnly}`,
        `display.colorMode: ${resolved.display.colorMode}`,
        `landing.variant: ${resolved.landing.variant}`,
        `composer.promptPrefix: ${resolved.composer.promptPrefix}`,
        `composer.placeholderClaude: ${resolved.composer.placeholderClaude}`,
        `status.position: ${resolved.statusLine.position}`,
        `status.align: ${resolved.statusLine.align}`,
        `markdown.shikiTheme: ${resolved.markdown.shikiTheme}`,
        `diff.previewMaxLines: ${resolved.diff.previewMaxLines}`,
        `diff.maxTokenizedLines: ${resolved.diff.maxTokenizedLines}`,
        `subagents.enabled: ${resolved.subagents.enabled}`,
        `subagents.coalesceMs: ${resolved.subagents.coalesceMs}`,
        `subagents.maxWorkItems: ${resolved.subagents.maxWorkItems}`,
        `subagents.maxStepsPerTask: ${resolved.subagents.maxStepsPerTask}`,
        `meta.strict: ${resolved.meta.strict}`,
        `meta.sources: ${resolved.meta.sources.join(" -> ")}`,
      ]
      await printReportCommandResult({
        mode: output,
        title: "Effective TUI config",
        jsonValue: resolved,
        yamlText: stringify(resolved),
        lines: summaryLines,
        sections:
          resolved.meta.warnings.length > 0
            ? [{ title: "meta.warnings", lines: resolved.meta.warnings.map((warning) => `- ${warning}`) }]
            : [],
      })
    }),
)
