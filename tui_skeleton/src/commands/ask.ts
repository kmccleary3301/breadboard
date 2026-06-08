import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { DEFAULT_MODEL_ID } from "../config/appConfig.js"
import { DEFAULT_REQUEST_CONFIG_PATH } from "./invocationContext.js"
import { reportRequestFailure } from "./commandRuntime.js"
import { printCommandPresentation, renderCompletionBlock } from "./commandPresentation.js"
import { runCommandWithReportedError } from "./commandOutcome.js"
import {
  executeOneShotCommand,
  normalizeOneShotOutputMode,
} from "./oneShotCommandRuntime.js"

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_REQUEST_CONFIG_PATH))
const workspaceOption = Options.text("workspace").pipe(Options.optional)
const overridesOption = Options.text("overrides").pipe(Options.optional)
const metadataOption = Options.text("metadata").pipe(Options.optional)
const modelOption = Options.text("model").pipe(Options.optional)
const remoteStreamOption = Options.boolean("remote-stream").pipe(Options.optional)
const permissionOption = Options.text("permission-mode").pipe(Options.optional)
const outputOption = Options.text("output").pipe(Options.withDefault("text"))
const analysisOption = Options.boolean("analysis").pipe(Options.optional)

export const askCommand = Command.make(
  "ask",
  {
    prompt: Args.text({ name: "prompt" }),
    config: configOption,
    workspace: workspaceOption,
    overrides: overridesOption,
    metadata: metadataOption,
    model: modelOption,
    remoteStream: remoteStreamOption,
    permissionMode: permissionOption,
    output: outputOption,
    analysis: analysisOption,
  },
  ({ prompt, config, workspace, overrides, metadata, model, remoteStream, permissionMode, output, analysis }) =>
    Effect.gen(function* () {
      const analysisFlag = Option.match(analysis, {
        onNone: () => false,
        onSome: (value) => value,
      })
      const { result, flags } = yield* Effect.promise(() =>
        runCommandWithReportedError({
          run: () =>
            executeOneShotCommand({
              prompt,
              config,
              workspace,
              overrides,
              metadata,
              model,
              remoteStream,
              permissionMode,
              analysis: analysisFlag,
              fallbackModel: process.env.BREADBOARD_DEFAULT_MODEL?.trim() ? DEFAULT_MODEL_ID : null,
              shouldRenderEvent: (event, renderFlags) =>
                event.type !== "completion" && !(event.type === "assistant_message" && renderFlags.sawAssistantDelta),
            }),
          report: reportRequestFailure,
        }),
      )
      const mode = normalizeOneShotOutputMode(output)
      if (mode === "json") {
        yield* Effect.promise(() =>
          printCommandPresentation({
            mode,
            jsonValue: {
              sessionId: result.sessionId,
              completion: result.completion,
            },
          }),
        )
      } else if (flags.sawAssistantDelta) {
        yield* Effect.sync(() => {
          process.stdout.write("\n")
        })
      } else if (result.completion && !flags.sawAssistantDelta && !flags.sawAssistantMessage) {
        yield* Console.log(renderCompletionBlock(result.completion))
      }
    }),
)
