import { Args, Command, Options } from "@effect/cli"
import { Console, Effect, Option } from "effect"
import { ApiError } from "../api/client.js"
import type { SessionEvent } from "../api/types.js"
import { parseJsonObject } from "../utils/json.js"
import { runAsk } from "./askLogic.js"
import { DEFAULT_MODEL_ID } from "../config/appConfig.js"
import { resolveBreadboardPath, resolveBreadboardWorkspace } from "../utils/paths.js"

const DEFAULT_CONFIG = process.env.BREADBOARD_DEFAULT_CONFIG ?? "agent_configs/claude_code_haiku45_c_fs_v2.yaml"
const ANALYSIS_CONFIG =
  process.env.BREADBOARD_ANALYSIS_CONFIG ?? "agent_configs/opencode_openai_gpt5nano_c_fs_cli_analysis.yaml"

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_CONFIG))
const workspaceOption = Options.text("workspace").pipe(Options.optional)
const overridesOption = Options.text("overrides").pipe(Options.optional)
const metadataOption = Options.text("metadata").pipe(Options.optional)
const modelOption = Options.text("model").pipe(Options.optional)
const remoteStreamOption = Options.boolean("remote-stream").pipe(Options.optional)
const permissionOption = Options.text("permission-mode").pipe(Options.optional)
const outputOption = Options.text("output").pipe(Options.withDefault("text"))
const analysisOption = Options.boolean("analysis").pipe(Options.optional)

type OutputMode = "text" | "json"

const normalizeMode = (value: string): OutputMode => (value === "json" ? "json" : "text")

const renderEvent = (event: SessionEvent): Effect.Effect<void> => {
  switch (event.type) {
    case "assistant_message": {
      const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
      return Console.log(text)
    }
    case "tool_call": {
      const payload = (event.payload ?? {}) as Record<string, any>
      const call = (payload.call ?? payload) as Record<string, any>
      const name = call?.function?.name ?? call?.name ?? "tool"
      return Console.log(`[tool] ${name}`)
    }
    case "tool_result": {
      const result = event.payload?.message ?? event.payload
      return Console.log(`[tool-result] ${JSON.stringify(result)}`)
    }
    case "reward_update": {
      return Console.log(`[reward] ${JSON.stringify(event.payload.summary ?? event.payload)}`)
    }
    case "error": {
      const payload = (event.payload ?? {}) as Record<string, any>
      const message = typeof payload.message === "string" ? payload.message : JSON.stringify(payload)
      if (message.includes("No such file or directory") && message.includes("agent_configs")) {
        return Console.log(
          `[error] Config path appears invalid on the engine side. Check --config (or use --analysis to select the analysis profile) and try again.\nDetails: ${message}`,
        )
      }
      return Console.log(`[error] ${JSON.stringify(payload)}`)
    }
    default:
      return Effect.succeed(undefined)
  }
}

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
      const workspaceValue = Option.getOrNull(workspace)
      const overridesValue = Option.getOrNull(overrides)
      const metadataValue = Option.getOrNull(metadata)
      const modelValue = Option.getOrNull(model)
      const permissionValue = Option.getOrNull(permissionMode)
      const analysisFlag = Option.match(analysis, {
        onNone: () => false,
        onSome: (value) => value,
      })
      const remoteStreamValue = Option.match(remoteStream, {
        onNone: () => undefined,
        onSome: (value) => value,
      })
      const requestOverrides = overridesValue ? parseJsonObject(overridesValue, "--overrides") : {}
      const requestMetadata = metadataValue ? parseJsonObject(metadataValue, "--metadata") : {}
      const overrideModelValue = typeof requestOverrides["providers.default_model"] === "string" ? (requestOverrides["providers.default_model"] as string) : undefined
      const resolvedModel = modelValue ?? overrideModelValue ?? DEFAULT_MODEL_ID
      requestMetadata.model = resolvedModel
      requestOverrides["providers.default_model"] = resolvedModel
      if (permissionValue) {
        requestMetadata.permission_mode = permissionValue
      }
      let configPath = config
      if (analysisFlag) {
        // If the user did not supply an explicit config (or left it at the global default),
        // switch to the analysis-focused profile and tag the request metadata.
        const defaultConfigMarker = DEFAULT_CONFIG
        if (!configPath || configPath === defaultConfigMarker) {
          configPath = ANALYSIS_CONFIG
        }
        requestMetadata.mode = "analysis"
        if (!permissionValue) {
          requestMetadata.permission_mode = "analysis"
        }
      }
      const resolvedConfigPath = resolveBreadboardPath(configPath)
      const resolvedWorkspace = resolveBreadboardWorkspace(workspaceValue)
      try {
        const result = yield* Effect.promise(() =>
          runAsk(
            {
              prompt,
              configPath: resolvedConfigPath,
              workspace: resolvedWorkspace,
              overrides: Object.keys(requestOverrides).length ? requestOverrides : undefined,
              metadata: Object.keys(requestMetadata).length ? requestMetadata : undefined,
              remoteStream: remoteStreamValue,
            },
            async (event) => {
              if (event.type !== "completion") {
                await Effect.runPromise(renderEvent(event))
              }
            },
          ),
        )
        const mode = normalizeMode(output)
        if (mode === "json") {
          yield* Console.log(
            JSON.stringify(
              {
                sessionId: result.sessionId,
                completion: result.completion,
              },
              null,
              2,
            ),
          )
        } else if (result.completion) {
          yield* Console.log(`
---
Completion: ${JSON.stringify(result.completion)}`)
        }
      } catch (error) {
        if (error instanceof ApiError) {
          yield* Console.error(`Request failed (status ${error.status})`)
          if (error.body) {
            yield* Console.error(JSON.stringify(error.body))
          }
        } else {
          yield* Console.error((error as Error).message)
        }
        return yield* Effect.fail(error as Error)
      }
    }),
)
