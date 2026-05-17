import { Effect, Option } from "effect"
import type { SessionEvent } from "../api/types.js"
import { runAsk } from "./askLogic.js"
import { createOneShotRenderState, renderOneShotEvent } from "./oneshotRender.js"
import { resolveInvocationContext } from "./invocationContext.js"

export type OneShotOutputMode = "text" | "json"

export interface OneShotRenderFlags {
  readonly sawAssistantDelta: boolean
  readonly sawAssistantMessage: boolean
}

export interface ExecuteOneShotCommandOptions {
  readonly prompt: string
  readonly config: string
  readonly workspace: Option.Option<string>
  readonly overrides: Option.Option<string>
  readonly metadata: Option.Option<string>
  readonly model: Option.Option<string>
  readonly remoteStream: Option.Option<boolean>
  readonly permissionMode: Option.Option<string>
  readonly analysis: boolean
  readonly fallbackModel: string | null
  readonly shouldRenderEvent?: (event: SessionEvent, flags: OneShotRenderFlags) => boolean
}

const renderWrites = (writes: ReadonlyArray<{ stream: "stdout" | "stderr"; text: string }>): Effect.Effect<void> =>
  Effect.sync(() => {
    for (const write of writes) {
      if (!write.text) continue
      if (write.stream === "stderr") {
        process.stderr.write(write.text)
      } else {
        process.stdout.write(write.text)
      }
    }
  })

export const normalizeOneShotOutputMode = (value: string): OneShotOutputMode => (value === "json" ? "json" : "text")

export const defaultShouldRenderOneShotEvent = (event: SessionEvent): boolean => event.type !== "completion"

export const executeOneShotCommand = async (
  options: ExecuteOneShotCommandOptions,
): Promise<{
  readonly result: Awaited<ReturnType<typeof runAsk>>
  readonly flags: OneShotRenderFlags
}> => {
  const oneShotRenderState = createOneShotRenderState({
    showCommentary: process.env.BREADBOARD_SHOW_COMMENTARY === "1",
  })
  const {
    permissionValue,
    remotePreference,
    requestMetadata,
    requestOverrides,
    resolvedConfigPath,
    resolvedWorkspace,
  } = resolveInvocationContext({
    config: options.config,
    workspace: Option.getOrNull(options.workspace),
    overrides: Option.getOrNull(options.overrides),
    metadata: Option.getOrNull(options.metadata),
    model: Option.getOrNull(options.model),
    remoteStream: options.remoteStream,
    permissionMode: Option.getOrNull(options.permissionMode),
    analysis: options.analysis,
    fallbackModel: options.fallbackModel,
  })

  let sawAssistantDelta = false
  let sawAssistantMessage = false

  const result = await runAsk(
    {
      prompt: options.prompt,
      configPath: resolvedConfigPath,
      workspace: resolvedWorkspace,
      overrides: Object.keys(requestOverrides).length ? requestOverrides : undefined,
      metadata: Object.keys(requestMetadata).length ? requestMetadata : undefined,
      remoteStream: remotePreference,
      permissionMode: permissionValue ?? undefined,
    },
    async (event) => {
      if (event.type === "assistant.message.delta") {
        sawAssistantDelta = true
      }
      if (event.type === "assistant_message") {
        sawAssistantMessage = true
      }
      const flags: OneShotRenderFlags = {
        sawAssistantDelta,
        sawAssistantMessage,
      }
      const shouldRender = options.shouldRenderEvent?.(event, flags) ?? defaultShouldRenderOneShotEvent(event)
      if (!shouldRender) {
        return
      }
      await Effect.runPromise(renderWrites(renderOneShotEvent(oneShotRenderState, event)))
    },
  )

  return {
    result,
    flags: {
      sawAssistantDelta,
      sawAssistantMessage,
    },
  }
}

