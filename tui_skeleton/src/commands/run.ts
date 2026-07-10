import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { shutdownEngine } from "../engine/engineSupervisor.js"
import { DEFAULT_MODEL_ID } from "../config/appConfig.js"
import { DEFAULT_REQUEST_CONFIG_PATH } from "./invocationContext.js"
import { reportRequestFailure } from "./commandRuntime.js"
import { renderCompletionBlock } from "./commandPresentation.js"
import { executeOneShotCommand } from "./oneShotCommandRuntime.js"
import { runCommandWithReportedError } from "./commandOutcome.js"

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_REQUEST_CONFIG_PATH))
const workspaceOption = Options.text("workspace").pipe(Options.optional)
const overridesOption = Options.text("overrides").pipe(Options.optional)
const metadataOption = Options.text("metadata").pipe(Options.optional)
const modelOption = Options.text("model").pipe(Options.optional)
const remoteStreamOption = Options.boolean("remote-stream").pipe(Options.optional)
const permissionOption = Options.text("permission-mode").pipe(Options.optional)
const analysisOption = Options.boolean("analysis").pipe(Options.optional)

export const runCommand = Command.make(
  "run",
  {
    prompt: Args.text({ name: "prompt" }),
    config: configOption,
    workspace: workspaceOption,
    overrides: overridesOption,
    metadata: metadataOption,
    model: modelOption,
    remoteStream: remoteStreamOption,
    permissionMode: permissionOption,
    analysis: analysisOption,
  },
  ({ prompt, config, workspace, overrides, metadata, model, remoteStream, permissionMode, analysis }) =>
    Effect.gen(function* () {
      const analysisFlag = Option.match(analysis, {
        onNone: () => false,
        onSome: (value) => value,
      })
      const envDefaultModel = process.env.BREADBOARD_DEFAULT_MODEL?.trim()
      const { result } = yield* Effect.promise(() =>
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
              fallbackModel: envDefaultModel && envDefaultModel.length > 0 ? DEFAULT_MODEL_ID : null,
            }),
          report: reportRequestFailure,
        }),
      )
      try {
        if (result.completion) {
          yield* Console.log(renderCompletionBlock(result.completion))
        }
      } finally {
        yield* Effect.promise(() => shutdownEngine().catch(() => undefined))
      }
    }),
)
