import { Command } from "@effect/cli"
import { Effect } from "effect"
import { render } from "ink"
import type { Instance as InkInstance } from "ink"
import { randomUUID } from "node:crypto"
import { ClassicReplView } from "./components/ClassicReplView.js"
import type { ConversationEntry, StreamStats, ModelMenuState, ModelMenuItem } from "../repl/types.js"

const MAX_HINTS = 6
const MAX_TOOL_HISTORY = 200
const DEFAULT_MODEL_ID = "openrouter/x-ai/grok-4-fast"

interface DemoModel {
  readonly id: string
  readonly label: string
  readonly detail?: string
  readonly provider: "OpenRouter" | "OpenAI"
}

const DEMO_MODELS: ReadonlyArray<DemoModel> = [
  { id: "openrouter/x-ai/grok-4-fast", label: "Grok 4 Fast", provider: "OpenRouter", detail: "ctx 16k" },
  { id: "openrouter/anthropic/claude-3.5-sonnet", label: "Claude 3.5 Sonnet", provider: "OpenRouter", detail: "ctx 200k" },
  { id: "openrouter/google/gemini-pro", label: "Gemini Pro", provider: "OpenRouter", detail: "ctx 32k" },
  { id: "openrouter/mistral/mistral-large", label: "Mistral Large", provider: "OpenRouter", detail: "ctx 65k" },
  { id: "openai/gpt-4o-mini", label: "GPT-4o mini", provider: "OpenAI", detail: "ctx 128k" },
]

const DEMO_SLASH_COMMANDS: ReadonlyArray<{ readonly name: string; readonly summary: string; readonly usage?: string }> = [
  { name: "help", summary: "List available slash commands." },
  { name: "quit", summary: "Exit the prototype shell." },
  { name: "clear", summary: "Clear conversation and tool panes." },
  { name: "status", summary: "Show prototype status summary." },
  { name: "remote", usage: "on|off", summary: "Toggle the remote streaming indicator." },
  { name: "mode", usage: "<plan|build|auto>", summary: "Switch prototype mode label." },
  { name: "model", usage: "<id>", summary: "Set the active model identifier." },
  { name: "models", summary: "Open the model picker menu." },
  { name: "retry", summary: "Regenerate the last assistant reply." },
  { name: "test", usage: "[suite]", summary: "Log a fake test run into the tool pane." },
  { name: "files", usage: "[path]", summary: "Display a mock file tree." },
]

const SAMPLE_FILE_TREE: ReadonlyArray<string> = [
  "src/",
  "  app/",
  "    index.ts",
  "    repl.tsx",
  "  shared/",
  "    api.ts",
  "    models.ts",
  "package.json",
  "tsconfig.json",
]

const RESPONSE_TEMPLATES: ReadonlyArray<(prompt: string, model: string) => string> = [
  (prompt, model) => `(${model}) Here's a concise take on “${prompt}”. Sketch out the core steps, then iterate.`,
  (prompt, model) => `(${model}) Key ideas for “${prompt}”: focus on clarity, guardrails, and fast feedback.`,
  (prompt, model) =>
    `(${model}) Prototyping “${prompt}” — start with the simplest scaffolding, wire in telemetry, then polish.`,
  (prompt, model) => `(${model}) Let's break “${prompt}” into smaller tasks. Capture TODOs inline so you can track progress.`,
  (prompt, model) => `(${model}) Quick plan for “${prompt}”: identify inputs/outputs, note tricky edges, and prepare tests.`,
]

const randomFrom = <T,>(values: ReadonlyArray<T>): T => values[Math.floor(Math.random() * values.length)]

const toMenuItems = (currentModel: string): ReadonlyArray<ModelMenuItem> =>
  DEMO_MODELS.map<ModelMenuItem>((model) => ({
    label: `${model.provider} · ${model.label}`,
    value: model.id,
    provider: model.provider,
    detail: model.detail,
    isDefault: model.id === DEFAULT_MODEL_ID,
    isCurrent: model.id === currentModel,
  })).sort((a, b) => {
    if (a.isCurrent && !b.isCurrent) return -1
    if (!a.isCurrent && b.isCurrent) return 1
    if (a.isDefault && !b.isDefault) return -1
    if (!a.isDefault && b.isDefault) return 1
    return a.label.localeCompare(b.label)
  })

const createSessionId = (): string => randomUUID()
const createEntryId = (): string => randomUUID()

const addConversationLine = (conversation: ConversationEntry[], speaker: ConversationEntry["speaker"], text: string) => {
  conversation.push({ id: createEntryId(), speaker, text, phase: "final" })
}

const addToolLine = (toolEvents: string[], text: string) => {
  toolEvents.push(text)
  if (toolEvents.length > MAX_TOOL_HISTORY) {
    toolEvents.splice(0, toolEvents.length - MAX_TOOL_HISTORY)
  }
}

const launchLegacyPrototype = (): Promise<void> =>
  new Promise<void>((resolve) => {
          const sessionId = createSessionId()
          const conversation: ConversationEntry[] = []
          const toolEvents: string[] = []
          const hints: string[] = []
          const stats: StreamStats = {
            eventCount: 0,
            toolCount: 0,
            lastTurn: null,
            remote: false,
            model: DEFAULT_MODEL_ID,
          }

          let status = "Prototype ready"
          let mode: "plan" | "build" | "auto" = "auto"
          let modelMenu: ModelMenuState = { status: "hidden" }
          let ink: InkInstance | null = null
          let lastUserInput: string | null = null
          const timers = new Set<NodeJS.Timeout>()
          let shuttingDown = false

          const pushHint = (message: string) => {
            hints.push(message)
            if (hints.length > MAX_HINTS) hints.shift()
          }

          const rerender = () => {
            if (!ink) return
            ink.rerender(
              <ClassicReplView
                sessionId={sessionId}
                conversation={[...conversation]}
                toolEvents={[...toolEvents]}
                status={status}
                hints={[...hints]}
                stats={{ ...stats }}
                modelMenu={modelMenu}
                onSubmit={handleSubmit}
                onModelSelect={handleModelSelect}
                onModelMenuCancel={handleModelMenuCancel}
                slashCommands={DEMO_SLASH_COMMANDS}
              />,
            )
          }

          const finish = () => {
            if (shuttingDown) return
            shuttingDown = true
            for (const timer of timers) {
              clearTimeout(timer)
            }
            timers.clear()
            ink?.unmount()
            resolve()
          }

          const scheduleAssistantResponse = (prompt: string, { replay = false }: { readonly replay?: boolean } = {}) => {
            const delay = 400 + Math.floor(Math.random() * 900)
            status = replay ? "Regenerating suggestion…" : "Drafting response…"
            rerender()
            const timer = setTimeout(() => {
              timers.delete(timer)
              const response = randomFrom(RESPONSE_TEMPLATES)(prompt, stats.model)
              addConversationLine(conversation, "assistant", response)
              addToolLine(
                toolEvents,
                `[demo] ${replay ? "Retry" : "Response"} generated in ${delay}ms using ${stats.model}`,
              )
              stats.eventCount += 1
              stats.toolCount += 1
              status = "Prototype ready"
              pushHint(replay ? "New draft generated." : "Assistant draft ready. Iterate freely.")
              rerender()
            }, delay)
            timers.add(timer)
          }

          const handleModelSelect = async (item: ModelMenuItem) => {
            stats.model = item.value
            modelMenu = { status: "hidden" }
            status = `Model selected: ${item.value}`
            pushHint(`Model switched to ${item.label}.`)
            rerender()
          }

          const handleModelMenuCancel = () => {
            if (modelMenu.status === "hidden") return
            modelMenu = { status: "hidden" }
            status = "Prototype ready"
            pushHint("Model picker closed.")
            rerender()
          }

          const openModelMenu = () => {
            modelMenu = { status: "ready", items: toMenuItems(stats.model) }
            status = "Select a model…"
            pushHint("Use ↑/↓ to browse, Enter to select, Esc to cancel.")
            rerender()
          }

          const listFiles = (scope: string | undefined) => {
            const prefix = scope && scope !== "." ? `(${scope})` : "(root)"
            pushHint(`Showing sample files ${prefix}.`)
            SAMPLE_FILE_TREE.forEach((line) => addToolLine(toolEvents, `[files] ${line}`))
            rerender()
          }

          const runDemoTests = (suite: string | undefined) => {
            const label = suite && suite.length > 0 ? suite : "default"
            pushHint(`Simulated test run for ${label}.`)
            const duration = (Math.random() * 1.2 + 0.4).toFixed(2)
            addToolLine(toolEvents, `[tests] ${label} → passed in ${duration}s`)
            stats.toolCount += 1
            rerender()
          }

          const setRemote = (value: boolean | undefined) => {
            stats.remote = value ?? !stats.remote
            pushHint(`Remote streaming ${stats.remote ? "enabled" : "disabled"} (visual only).`)
            status = `Remote ${stats.remote ? "on" : "off"}`
            rerender()
          }

          const summarizeStatus = () => {
            pushHint(
              `Session ${sessionId.slice(0, 8)} | mode ${mode} | model ${stats.model} | remote ${
                stats.remote ? "on" : "off"
              }`,
            )
            rerender()
          }

          const handleSlashCommand = async (raw: string) => {
            const [command, ...rest] = raw.split(/\s+/)
            const args = rest.filter((part) => part.length > 0)
            switch (command.toLowerCase()) {
              case "help": {
                pushHint(
                  DEMO_SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`).join(" | "),
                )
                break
              }
              case "quit": {
                pushHint("Exiting prototype…")
                finish()
                return
              }
              case "clear": {
                conversation.length = 0
                toolEvents.length = 0
                pushHint("Cleared panes.")
                stats.eventCount = 0
                stats.toolCount = 0
                stats.lastTurn = null
                break
              }
              case "status": {
                summarizeStatus()
                break
              }
              case "remote": {
                const choice = args[0]?.toLowerCase()
                if (choice === "on") setRemote(true)
                else if (choice === "off") setRemote(false)
                else setRemote(undefined)
                break
              }
              case "mode": {
                const next = args[0]?.toLowerCase()
                if (!next || !["plan", "build", "auto"].includes(next)) {
                  pushHint("Usage: /mode <plan|build|auto>")
                  break
                }
                mode = next as typeof mode
                pushHint(`Mode set to ${mode}.`)
                status = `Mode: ${mode}`
                break
              }
              case "model": {
                const requested = args[0]
                if (!requested) {
                  pushHint("Usage: /model <id>")
                  break
                }
                stats.model = requested
                pushHint(`Model set to ${requested}.`)
                status = `Model: ${requested}`
                break
              }
              case "models": {
                openModelMenu()
                return
              }
              case "retry": {
                if (!lastUserInput) {
                  pushHint("No user input to retry yet.")
                  break
                }
                scheduleAssistantResponse(lastUserInput, { replay: true })
                break
              }
              case "test": {
                runDemoTests(args.join(" ") || undefined)
                break
              }
              case "files": {
                listFiles(args[0])
                break
              }
              default: {
                pushHint(`Unknown command: /${command}`)
                break
              }
            }
            status = "Prototype ready"
            rerender()
          }

          const handleSubmit = async (value: string) => {
            const trimmed = value.trim()
            if (trimmed.length === 0) return
            if (modelMenu.status !== "hidden") {
              pushHint("Close the model menu before sending input (Esc).")
              rerender()
              return
            }
            if (trimmed.startsWith("/")) {
              await handleSlashCommand(trimmed.slice(1))
              return
            }
            stats.lastTurn = (stats.lastTurn ?? 0) + 1
            addConversationLine(conversation, "user", trimmed)
            lastUserInput = trimmed
            scheduleAssistantResponse(trimmed)
            rerender()
          }

          pushHint("Prototype shell loaded. Type /help to explore demo commands.")
          ink = render(
            <ClassicReplView
              sessionId={sessionId}
              conversation={conversation}
              toolEvents={toolEvents}
              status={status}
              hints={hints}
              stats={stats}
              modelMenu={modelMenu}
              onSubmit={handleSubmit}
              onModelSelect={handleModelSelect}
              onModelMenuCancel={handleModelMenuCancel}
              slashCommands={DEMO_SLASH_COMMANDS}
            />,
          )
  })

export const runLegacyShell = (): Promise<void> => launchLegacyPrototype()

export const replClassicCommand = Command.make(
  "repl",
  {},
  () => Effect.tryPromise(launchLegacyPrototype),
)
