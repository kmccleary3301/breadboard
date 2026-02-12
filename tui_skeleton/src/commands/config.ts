import { Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { stringify } from "yaml"
import { resolveTuiConfig } from "../tui_config/load.js"

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

      if (output === "yaml") {
        console.log(stringify(resolved))
        return
      }

      if (output === "summary") {
        console.log("Effective TUI config")
        console.log(`preset: ${resolved.preset}`)
        console.log(`display.asciiOnly: ${resolved.display.asciiOnly}`)
        console.log(`display.colorMode: ${resolved.display.colorMode}`)
        console.log(`landing.variant: ${resolved.landing.variant}`)
        console.log(`composer.promptPrefix: ${resolved.composer.promptPrefix}`)
        console.log(`composer.placeholderClaude: ${resolved.composer.placeholderClaude}`)
        console.log(`status.position: ${resolved.statusLine.position}`)
        console.log(`status.align: ${resolved.statusLine.align}`)
        console.log(`markdown.shikiTheme: ${resolved.markdown.shikiTheme}`)
        console.log(`diff.previewMaxLines: ${resolved.diff.previewMaxLines}`)
        console.log(`diff.maxTokenizedLines: ${resolved.diff.maxTokenizedLines}`)
        console.log(`subagents.enabled: ${resolved.subagents.enabled}`)
        console.log(`subagents.coalesceMs: ${resolved.subagents.coalesceMs}`)
        console.log(`subagents.maxWorkItems: ${resolved.subagents.maxWorkItems}`)
        console.log(`subagents.maxStepsPerTask: ${resolved.subagents.maxStepsPerTask}`)
        console.log(`meta.strict: ${resolved.meta.strict}`)
        console.log(`meta.sources: ${resolved.meta.sources.join(" -> ")}`)
        if (resolved.meta.warnings.length > 0) {
          console.log("meta.warnings:")
          for (const warning of resolved.meta.warnings) {
            console.log(`- ${warning}`)
          }
        }
        return
      }

      console.log(JSON.stringify(resolved, null, 2))
    }),
)
