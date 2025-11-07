import { Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { render } from "ink"
import type { Instance as InkInstance } from "ink"
import { ApiClient, ApiError } from "../../api/client.js"
import { streamSessionEvents } from "../../api/stream.js"
import type { SessionEvent } from "../../api/types.js"
import { loadAppConfig, DEFAULT_MODEL_ID } from "../../config/appConfig.js"
import { formatFileList } from "../files.js"
import { getModelCatalog, type ProviderModel } from "../../providers/modelCatalog.js"
import { ReplView } from "./components/ReplView.js"
import type { ConversationEntry, StreamStats, ModelMenuState, ModelMenuItem } from "./types.js"
import { buildSuggestions, SLASH_COMMANDS } from "./slashCommands.js"

const DEFAULT_CONFIG = process.env.KYLECODE_DEFAULT_CONFIG ?? "agent_configs/opencode_grok4fast_c_fs_v2.yaml"
const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_CONFIG))
const workspaceOption = Options.text("workspace").pipe(Options.optional)
const modelOption = Options.text("model").pipe(Options.optional)
const remoteStreamOption = Options.boolean("remote-stream").pipe(Options.optional)
const permissionOption = Options.text("permission-mode").pipe(Options.optional)

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const MAX_HINTS = 6
const MAX_TOOL_HISTORY = 400
const MAX_RETRIES = 5

const addConversationLine = (conversation: ConversationEntry[], speaker: ConversationEntry["speaker"], text: string) => {
  conversation.push({ speaker, text })
}

const addToolLine = (toolEvents: string[], text: string) => {
  toolEvents.push(text)
  if (toolEvents.length > MAX_TOOL_HISTORY) {
    toolEvents.splice(0, toolEvents.length - MAX_TOOL_HISTORY)
  }
}

const abbreviateContext = (ctx?: number | null): string | undefined => {
  if (!ctx || ctx <= 0) return undefined
  if (ctx >= 1_000) return `${Math.round(ctx / 1_000)}k ctxt`
  return `${ctx} ctxt`
}

const formatPricing = (pricing?: string | null): string | undefined => {
  if (!pricing) return undefined
  const parts = pricing.split(",").map((part) => part.trim()).filter(Boolean)
  if (parts.length === 0) return undefined
  const formatted = parts
    .map((part) => {
      const [label, raw] = part.split(/\s+/)
      const value = Number(raw)
      if (!label || Number.isNaN(value)) return undefined
      const perThousand = value * 1000
      const rounded =
        perThousand >= 1 ? perThousand.toFixed(2) : perThousand >= 0.1 ? perThousand.toFixed(3) : perThousand.toFixed(4)
      return `${label} $${rounded.replace(/0+$/, "").replace(/\.$/, "")}`
    })
    .filter((entry): entry is string => Boolean(entry))
  return formatted.length > 0 ? formatted.join(" • ") : undefined
}

const toMenuItems = (models: ReadonlyArray<ProviderModel>, currentModel: string): ModelMenuItem[] =>
  models
    .map<ModelMenuItem>((model) => {
      const providerLabel = model.provider === "openrouter" ? "OpenRouter" : "OpenAI"
      const detailParts: string[] = []
      const contextDetail = abbreviateContext(model.contextLength)
      if (contextDetail) detailParts.push(contextDetail)
      const pricingDetail = formatPricing(model.pricing)
      if (pricingDetail) detailParts.push(pricingDetail)
      const detail = detailParts.length > 0 ? detailParts.join(" • ") : undefined
      return {
        label: `${providerLabel} · ${model.name}`,
        value: model.id,
        provider: model.provider,
        detail,
        isDefault: model.id === DEFAULT_MODEL_ID,
        isCurrent: model.id === currentModel,
      }
    })
    .sort((a, b) => {
      if (a.isCurrent && !b.isCurrent) return -1
      if (!a.isCurrent && b.isCurrent) return 1
      if (a.isDefault && !b.isDefault) return -1
      if (!a.isDefault && b.isDefault) return 1
      return a.label.localeCompare(b.label)
    })

interface SlashHandlerContext {
  abort: () => void
  pushHint: (message: string) => void
  pushToolEvent: (message: string) => void
  updateStatus: (value: string) => void
  remotePreference: { value: boolean | undefined; apply: (value: boolean) => void }
  stats: StreamStats
  conversation: ConversationEntry[]
  toolEvents: string[]
  sessionId: string
  completionReached: boolean
  requestRetry: () => void
  runSessionCommand: (command: string, payload?: Record<string, unknown>, successMessage?: string) => Promise<boolean>
  openModelMenu: (state: ModelMenuState) => void
  closeModelMenu: () => void
  modelMenuState: ModelMenuState
  loadModelMenuItems: () => Promise<ReadonlyArray<ModelMenuItem>>
}

type SlashHandler = (args: string[], ctx: SlashHandlerContext) => Promise<void>

const createSlashHandlers = (): Record<string, SlashHandler> => ({
  quit: async (_args, ctx) => {
    ctx.pushHint("Exiting session…")
    ctx.updateStatus("Exiting…")
    ctx.abort()
  },
  help: async (_args, ctx) => {
    const summary = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""} — ${entry.summary}`).join(" | ")
    ctx.pushHint(summary)
  },
  clear: async (_args, ctx) => {
    ctx.conversation.length = 0
    ctx.toolEvents.length = 0
    ctx.pushHint("Cleared conversation and tool logs.")
  },
  status: async (_args, ctx) => {
    try {
      const summary = await ApiClient.getSession(ctx.sessionId)
      ctx.pushHint(`Status: ${summary.status}, last activity ${summary.last_activity_at}`)
      ctx.updateStatus(`Status: ${summary.status}`)
      if (summary.completion_summary) {
        ctx.pushToolEvent(`[status] completion ${JSON.stringify(summary.completion_summary)}`)
      }
    } catch (error) {
      ctx.pushHint(`Status check failed: ${(error as Error).message}`)
    }
  },
  remote: async (args, ctx) => {
    const action = args[0]?.toLowerCase()
    if (action === "on") {
      ctx.remotePreference.apply(true)
      ctx.pushHint("Remote streaming preference enabled (next session).")
      ctx.updateStatus("Remote preference: on")
    } else if (action === "off") {
      ctx.remotePreference.apply(false)
      ctx.pushHint("Remote streaming preference disabled (next session).")
      ctx.updateStatus("Remote preference: off")
    } else {
      ctx.pushHint(`Remote currently ${ctx.remotePreference.value ? "enabled" : "disabled"}. Use /remote on|off.`)
    }
  },
  retry: async (_args, ctx) => {
    if (ctx.completionReached) {
      ctx.pushHint("Session already completed. Start a new session to continue.")
      return
    }
    ctx.pushHint("Manual retry requested.")
    ctx.updateStatus("Restarting…")
    ctx.requestRetry()
  },
  plan: async (_args, ctx) => {
    const ok = await ctx.runSessionCommand("set_mode", { mode: "plan" }, "Requested plan-focused mode.")
    if (ok) ctx.updateStatus("Mode request: plan")
  },
  mode: async (args, ctx) => {
    const target = args[0]?.toLowerCase()
    if (!target) {
      ctx.pushHint("Usage: /mode <plan|build|auto>. Alias: /plan.")
      return
    }
    if (!["plan", "build", "auto", "default"].includes(target)) {
      ctx.pushHint(`Unknown mode "${target}". Expected plan, build, or auto.`)
      return
    }
    const normalized = target === "default" ? "auto" : target
    const ok = await ctx.runSessionCommand("set_mode", { mode: normalized }, `Mode set request sent (${normalized}).`)
    if (ok) ctx.updateStatus(`Mode request: ${normalized}`)
  },
  model: async (args, ctx) => {
    const newModel = args[0]
    if (!newModel) {
      ctx.pushHint("Usage: /model <provider/model-id>")
      return
    }
    const ok = await ctx.runSessionCommand("set_model", { model: newModel }, `Model switch requested (${newModel}).`)
    if (ok) ctx.updateStatus(`Model request: ${newModel}`)
  },
  test: async (args, ctx) => {
    const suite = args.join(" ").trim()
    const payload = suite.length > 0 ? { suite } : undefined
    const ok = await ctx.runSessionCommand(
      "run_tests",
      payload,
      suite.length > 0 ? `Test suite requested (${suite}).` : "Default test suite requested.",
    )
    if (ok) ctx.updateStatus(suite.length > 0 ? `Test request: ${suite}` : "Test request: default")
  },
  files: async (args, ctx) => {
    const scope = args[0] ?? "."
    try {
      const files = await ApiClient.listSessionFiles(ctx.sessionId, scope === "." ? undefined : scope)
      const output = formatFileList(files)
      ctx.pushHint(`Files listed for ${scope === "." ? "(root)" : scope}.`)
      output.split("\n").forEach((line) => ctx.pushToolEvent(`[files] ${line}`))
    } catch (error) {
      if (error instanceof ApiError) {
        ctx.pushHint(`File listing failed (${error.status}).`)
      } else {
        ctx.pushHint(`File listing error: ${(error as Error).message}`)
      }
    }
  },
  models: async (_args, ctx) => {
    if (ctx.modelMenuState.status !== "hidden") {
      ctx.pushHint("Model picker already open. Use Esc to cancel or select a model.")
      return
    }
    ctx.updateStatus("Loading models…")
    ctx.openModelMenu({ status: "loading" })
    try {
      const items = await ctx.loadModelMenuItems()
      if (items.length === 0) {
        ctx.openModelMenu({ status: "error", message: "No models available for active credentials." })
        ctx.updateStatus("No models available")
        return
      }
      ctx.openModelMenu({ status: "ready", items })
      ctx.pushHint("Use ↑/↓ to choose a model. Press Enter to confirm, Esc to cancel.")
      ctx.updateStatus("Model picker ready")
    } catch (error) {
      ctx.openModelMenu({ status: "error", message: `Failed to load models: ${(error as Error).message}` })
      ctx.updateStatus("Model catalog unavailable")
    }
  },
})

export const replCommand = Command.make(
  "repl",
  {
    config: configOption,
    workspace: workspaceOption,
    model: modelOption,
    remoteStream: remoteStreamOption,
    permissionMode: permissionOption,
  },
  ({ config, workspace, model, remoteStream, permissionMode }) =>
    Effect.tryPromise(async () => {
      const appConfig = loadAppConfig()
      const workspaceValue = Option.getOrNull(workspace)
      const explicitModel = Option.getOrNull(model)
      const modelToUse = explicitModel ?? DEFAULT_MODEL_ID
      let remotePreference = Option.match(remoteStream, {
        onNone: () => undefined,
        onSome: (value) => value,
      })
      const permissionValue = Option.getOrNull(permissionMode)

      const requestMetadata: Record<string, unknown> = { model: modelToUse }
      const requestOverrides: Record<string, unknown> = { "providers.default_model": modelToUse }
      if (permissionValue) requestMetadata.permission_mode = permissionValue
      if (remotePreference ?? appConfig.remoteStreamDefault) requestMetadata.enable_remote_stream = true

      const payload = {
        config_path: config,
        task: "Interactive REPL session",
        workspace: workspaceValue ?? undefined,
        metadata: Object.keys(requestMetadata).length ? requestMetadata : undefined,
        overrides: Object.keys(requestOverrides).length ? requestOverrides : undefined,
        stream: true,
      }

      const conversation: ConversationEntry[] = []
      const toolEvents: string[] = []
      const hints: string[] = []
      const stats: StreamStats = {
        eventCount: 0,
        toolCount: 0,
        lastTurn: null,
        remote: remotePreference ?? appConfig.remoteStreamDefault,
        model: modelToUse,
      }

      const pushHint = (msg: string) => {
        hints.push(msg)
        if (hints.length > MAX_HINTS) hints.shift()
      }

      const updateStatus = (value: string) => {
        status = value
      }

      const addTool = (message: string) => addToolLine(toolEvents, message)

      let status = "Starting session…"
      let modelMenu: ModelMenuState = { status: "hidden" }
      let abortRequested = false
      let restartRequested = false
      let completionReached = false
      let sessionId = ""
      let consecutiveFailures = 0
      let ink: InkInstance | null = null
      let controller = new AbortController()

      const closeModelMenu = () => {
        modelMenu = { status: "hidden" }
        rerender()
      }

      const loadModelMenuItems = async () => toMenuItems(await getModelCatalog(), stats.model)

      const runSessionCommand = async (command: string, payload?: Record<string, unknown>, successMessage?: string): Promise<boolean> => {
        if (!sessionId) {
          pushHint("Session not ready yet.")
          return false
        }
        const body: Record<string, unknown> = { command }
        if (payload && Object.keys(payload).length > 0) body.payload = payload
        try {
          await ApiClient.postCommand(sessionId, body)
          if (command === "set_model") {
            const candidate = payload as { model?: unknown } | undefined
            if (candidate && typeof candidate.model === "string") {
              stats.model = candidate.model
            }
          }
          addTool(`[command] ${command}${payload ? ` ${JSON.stringify(payload)}` : ""}`)
          pushHint(successMessage ?? `Sent command "${command}".`)
          rerender()
          return true
        } catch (error) {
          if (error instanceof ApiError) {
            pushHint(`Command failed (${error.status}).`)
          } else {
            pushHint(`Command error: ${(error as Error).message}`)
          }
          rerender()
          return false
        }
      }

      const slashHandlers = createSlashHandlers()

      const submit = async (text: string) => {
        if (modelMenu.status !== "hidden") {
          pushHint("Close the model menu before sending input (Esc).")
          rerender()
          return
        }
        if (text.startsWith("/")) {
          const [command, ...args] = text.slice(1).split(/\s+/)
          const handler = slashHandlers[command.toLowerCase()]
          if (handler) {
            await handler(args, {
              abort: () => {
                abortRequested = true
                controller.abort()
              },
              pushHint,
              pushToolEvent: addTool,
              updateStatus,
              remotePreference: {
                value: remotePreference,
                apply: (value: boolean) => {
                  remotePreference = value
                  stats.remote = value
                },
              },
              stats,
              conversation,
              toolEvents,
              sessionId,
              completionReached,
              requestRetry: () => {
                restartRequested = true
                controller.abort()
              },
              runSessionCommand,
              openModelMenu: (state: ModelMenuState) => {
                modelMenu = state
                rerender()
              },
              closeModelMenu,
              modelMenuState: modelMenu,
              loadModelMenuItems,
            })
          } else {
            pushHint(`Unknown command: /${command}`)
          }
          rerender()
          return
        }
        if (completionReached) {
          pushHint("Session already completed. Start a new session to continue.")
          rerender()
          return
        }
        if (!sessionId) {
          pushHint("Session not ready yet. Please wait.")
          rerender()
          return
        }
        addConversationLine(conversation, "user", text)
        rerender()
        try {
          await ApiClient.postInput(sessionId, { content: text })
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            pushHint("Backend does not yet support sending interactive input (404).")
          } else {
            pushHint(`Failed to send input: ${(error as Error).message}`)
          }
          rerender()
        }
      }

      const handleModelSelect = async (item: ModelMenuItem) => {
        closeModelMenu()
        const ok = await runSessionCommand("set_model", { model: item.value }, `Model switch requested (${item.label}).`)
        if (ok) updateStatus(`Model request: ${item.value}`)
      }

      const handleModelMenuCancel = () => {
        closeModelMenu()
        updateStatus("Streaming…")
        rerender()
      }

      const rerender = () => {
        if (!ink) return
        ink.rerender(
          <ReplView
            sessionId={sessionId}
            conversation={[...conversation]}
            toolEvents={[...toolEvents]}
            status={status}
            hints={[...hints]}
            stats={{ ...stats }}
            modelMenu={modelMenu}
            onSubmit={submit}
            onModelSelect={handleModelSelect}
            onModelMenuCancel={handleModelMenuCancel}
          />,
        )
      }

      const session = await ApiClient.createSession(payload)
      sessionId = session.session_id
      pushHint(`Session ${sessionId} started (remote ${stats.remote ? "enabled" : "disabled"}, model ${stats.model}).`)
      status = "Streaming…"

      ink = render(
        <ReplView
          sessionId={sessionId}
          conversation={conversation}
          toolEvents={toolEvents}
          status={status}
          hints={hints}
          stats={stats}
          modelMenu={modelMenu}
          onSubmit={submit}
          onModelSelect={handleModelSelect}
          onModelMenuCancel={handleModelMenuCancel}
        />,
      )

      try {
        while (!abortRequested && !completionReached) {
          controller = new AbortController()
          try {
            for await (const event of streamSessionEvents(sessionId, { config: appConfig, signal: controller.signal })) {
              consecutiveFailures = 0
              stats.eventCount += 1
              stats.lastTurn = event.turn ?? stats.lastTurn
              switch (event.type) {
                case "assistant_message":
                  addConversationLine(
                    conversation,
                    "assistant",
                    typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload),
                  )
                  break
                case "user_message":
                  addConversationLine(
                    conversation,
                    "user",
                    typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload),
                  )
                  break
                case "tool_call":
                  stats.toolCount += 1
                  addTool(`[call] ${JSON.stringify(event.payload)}`)
                  break
                case "tool_result":
                  stats.toolCount += 1
                  addTool(`[result] ${JSON.stringify(event.payload)}`)
                  break
                case "reward_update":
                  addTool(`[reward] ${JSON.stringify(event.payload.summary ?? event.payload)}`)
                  break
                case "error": {
                  const message = JSON.stringify(event.payload)
                  pushHint(`[error] ${message}`)
                  addTool(`[error] ${message}`)
                  break
                }
                case "completion": {
                  completionReached = true
                  const summary = event.payload?.summary ?? event.payload ?? { completed: true }
                  status = "Completed"
                  addTool(`[completion] ${JSON.stringify(summary)}`)
                  pushHint("Completion event received.")
                  break
                }
                default:
                  break
              }
              rerender()
              if (completionReached) break
            }
            if (!restartRequested) {
              if (completionReached) break
              consecutiveFailures += 1
              if (consecutiveFailures > MAX_RETRIES) {
                pushHint("Stream ended unexpectedly and reconnection attempts exhausted.")
                status = "Disconnected"
                rerender()
                break
              }
              const retryDelay = Math.min(4000, 500 * 2 ** (consecutiveFailures - 1))
              pushHint(`Stream ended unexpectedly. Retrying in ${retryDelay}ms (attempt ${consecutiveFailures}/${MAX_RETRIES}).`)
              status = `Reconnecting (${consecutiveFailures}/${MAX_RETRIES})`
              rerender()
              await sleep(retryDelay)
              continue
            }
            restartRequested = false
            consecutiveFailures = 0
            status = "Restarting…"
            rerender()
            pushHint("Restarting stream…")
            await sleep(250)
          } catch (error) {
            if (controller.signal.aborted) {
              if (abortRequested) {
                status = "Aborted"
                rerender()
                break
              }
              if (restartRequested) {
                restartRequested = false
                consecutiveFailures = 0
                status = "Restarting…"
                rerender()
                pushHint("Stream aborted. Retrying…")
                await sleep(250)
                continue
              }
              status = "Stream stopped"
              rerender()
              break
            }
            consecutiveFailures += 1
            const delay = Math.min(4000, 500 * 2 ** (consecutiveFailures - 1))
            const message = error instanceof Error ? error.message : String(error)
            if (consecutiveFailures > MAX_RETRIES) {
              pushHint(`Stream interruption: ${message}. Giving up after ${MAX_RETRIES} attempts.`)
              status = "Disconnected"
              rerender()
              break
            }
            pushHint(`Stream interruption: ${message}. Retrying in ${delay}ms (attempt ${consecutiveFailures}/${MAX_RETRIES}).`)
            status = `Reconnecting (${consecutiveFailures}/${MAX_RETRIES})`
            rerender()
            await sleep(delay)
          }
        }
      } finally {
        ink?.unmount()
      }
    }),
)
