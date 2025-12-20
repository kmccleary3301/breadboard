import { promises as fs } from "node:fs"
import path from "node:path"
import type { SlashSuggestion } from "../../repl/slashCommands.js"
import { buildSuggestions } from "../../repl/slashCommands.js"
import type { ReplSessionController, ReplState } from "./controller.js"
import { renderStateToText, type RenderTextOptions } from "./renderText.js"

type KeyInput =
  | "enter"
  | "escape"
  | "tab"
  | "up"
  | "down"
  | "left"
  | "right"
  | "backspace"
  | "ctrl+c"
  | "ctrl+l"

interface TypeStep {
  readonly action: "type"
  readonly text: string
  readonly delayMs?: number
}

interface SendStep {
  readonly action: "send"
  readonly text: string
  readonly delayMs?: number
}

interface PressStep {
  readonly action: "press"
  readonly key: KeyInput
  readonly repeat?: number
  readonly delayMs?: number
}

interface WaitStepCompletion {
  readonly action: "wait"
  readonly for: "completion"
  readonly timeoutMs?: number
}

interface WaitStepAssistant {
  readonly action: "wait"
  readonly for: "assistant"
  readonly timeoutMs?: number
}

interface WaitStepStatus {
  readonly action: "wait"
  readonly for: "status"
  readonly includes: string
  readonly timeoutMs?: number
}

interface WaitStepHint {
  readonly action: "wait"
  readonly for: "hint"
  readonly includes: string
  readonly timeoutMs?: number
}

interface WaitStepEvents {
  readonly action: "wait"
  readonly for: "events"
  readonly count: number
  readonly timeoutMs?: number
}

type WaitStep = WaitStepCompletion | WaitStepAssistant | WaitStepStatus | WaitStepHint | WaitStepEvents

interface DelayStep {
  readonly action: "delay"
  readonly ms: number
}

interface OpenModelMenuStep {
  readonly action: "openModelMenu"
}

interface SelectModelStep {
  readonly action: "selectModel"
  readonly value: string
}

interface SnapshotStep {
  readonly action: "snapshot"
  readonly label?: string
  readonly includeHints?: boolean
  readonly includeModelMenu?: boolean
  readonly colors?: boolean
  readonly includeHeader?: boolean
}

interface RunCommandStep {
  readonly action: "runCommand"
  readonly command: string
  readonly payload?: Record<string, unknown>
  readonly successMessage?: string
}

interface LogStep {
  readonly action: "log"
  readonly message: string
  readonly label?: string
}

interface ClearInputStep {
  readonly action: "clearInput"
}

type ScriptStep =
  | TypeStep
  | SendStep
  | PressStep
  | WaitStep
  | DelayStep
  | OpenModelMenuStep
  | SelectModelStep
  | SnapshotStep
  | RunCommandStep
  | LogStep
  | ClearInputStep

export interface ScriptDefinition {
  readonly steps: ScriptStep[]
}

export interface ScriptRunOptions {
  readonly baseDir?: string
  readonly renderOptions?: RenderTextOptions
  readonly snapshotAfterEachStep?: boolean
  readonly finalOnly?: boolean
}

export interface ScriptSnapshot {
  readonly index: number
  readonly label: string
  readonly text: string
}

export interface ScriptRunResult {
  readonly snapshots: ScriptSnapshot[]
  readonly finalState: ReplState
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const normalizeScript = (value: unknown): ScriptStep[] => {
  if (Array.isArray(value)) {
    return value as ScriptStep[]
  }
  if (isRecord(value) && Array.isArray(value.steps)) {
    return value.steps as ScriptStep[]
  }
  throw new Error("Script file must be an array of steps or an object with a 'steps' array.")
}

const resolveKey = (key: string): KeyInput => {
  const normalized = key.toLowerCase()
  switch (normalized) {
    case "enter":
    case "return":
      return "enter"
    case "esc":
    case "escape":
      return "escape"
    case "tab":
      return "tab"
    case "up":
    case "arrowup":
      return "up"
    case "down":
    case "arrowdown":
      return "down"
    case "left":
    case "arrowleft":
      return "left"
    case "right":
    case "arrowright":
      return "right"
    case "backspace":
      return "backspace"
    case "ctrl+c":
    case "control+c":
      return "ctrl+c"
    case "ctrl+l":
    case "control+l":
      return "ctrl+l"
    default:
      throw new Error(`Unsupported key "${key}".`)
  }
}

type ExecutionResult =
  | { readonly kind: "snapshot"; readonly label?: string; readonly options?: RenderTextOptions }
  | { readonly kind: "log"; readonly message: string; readonly label?: string }
  | null

class ScriptRuntime {
  private inputBuffer = ""
  private suggestions: SlashSuggestion[] = []
  private suggestionIndex = 0
  private state: ReplState
  private unsubscribe: () => void
  private modelIndex = 0

  constructor(
    private readonly controller: ReplSessionController,
    private readonly baseRenderOptions: RenderTextOptions = {},
  ) {
    this.state = controller.getState()
    this.unsubscribe = controller.onChange((next) => {
      this.state = next
      if (next.modelMenu.status === "ready") {
        const current = next.modelMenu.items.findIndex((item) => item.isCurrent)
        this.modelIndex = current >= 0 ? current : 0
      }
    })
  }

  dispose(): void {
    this.unsubscribe()
  }

  getState(): ReplState {
    return this.state
  }

  private updateSuggestions(): void {
    this.suggestions = buildSuggestions(this.inputBuffer, 5)
    if (this.suggestionIndex >= this.suggestions.length) {
      this.suggestionIndex = 0
    }
  }

  private async typeText(text: string, delayMs = 0): Promise<void> {
    for (const char of text) {
      this.inputBuffer += char
      this.updateSuggestions()
      if (delayMs > 0) {
        await sleep(delayMs)
      }
    }
  }

  private applyBackspace(): void {
    if (this.inputBuffer.length === 0) return
    this.inputBuffer = this.inputBuffer.slice(0, -1)
    this.updateSuggestions()
  }

  private cycleSuggestion(direction: 1 | -1): void {
    if (this.suggestions.length === 0) return
    const next = (this.suggestionIndex + direction + this.suggestions.length) % this.suggestions.length
    this.suggestionIndex = next
  }

  private acceptSuggestion(): void {
    if (this.suggestions.length === 0) return
    const choice = this.suggestions[Math.max(0, Math.min(this.suggestionIndex, this.suggestions.length - 1))]
    this.inputBuffer = `${choice.command}${choice.usage ? ` ${choice.usage}` : ""}`
    this.updateSuggestions()
  }

  private async submitInput(): Promise<void> {
    const content = this.inputBuffer.trim()
    if (!content) return
        await this.controller.handleInput(content)
    this.inputBuffer = ""
    this.suggestionIndex = 0
    this.updateSuggestions()
  }

  private async selectCurrentModel(): Promise<void> {
    const menu = this.state.modelMenu
    if (menu.status !== "ready" || menu.items.length === 0) {
      this.controller.closeModelMenu()
      return
    }
    const index = Math.max(0, Math.min(this.modelIndex, menu.items.length - 1))
    const item = menu.items[index]
    await this.controller.selectModel(item.value)
  }

  private moveModelCursor(direction: 1 | -1): void {
    const menu = this.state.modelMenu
    if (menu.status !== "ready" || menu.items.length === 0) return
    const next = (this.modelIndex + direction + menu.items.length) % menu.items.length
    this.modelIndex = next
  }

  private async pressKey(key: KeyInput): Promise<void> {
    switch (key) {
      case "backspace":
        this.applyBackspace()
        return
      case "tab":
        if (this.state.modelMenu.status === "ready") {
          this.moveModelCursor(1)
        } else {
          this.cycleSuggestion(1)
        }
        return
      case "up":
        if (this.state.modelMenu.status === "ready") {
          this.moveModelCursor(-1)
        } else {
          this.cycleSuggestion(-1)
        }
        return
      case "down":
        if (this.state.modelMenu.status === "ready") {
          this.moveModelCursor(1)
        } else {
          this.cycleSuggestion(1)
        }
        return
      case "enter":
        if (this.state.modelMenu.status === "ready") {
          await this.selectCurrentModel()
        } else {
          await this.submitInput()
        }
        return
      case "escape":
        this.controller.closeModelMenu()
        this.inputBuffer = ""
        this.updateSuggestions()
        return
      case "ctrl+c":
        await this.controller.dispatchSlashCommand("quit", [])
        return
      case "ctrl+l":
        await this.controller.dispatchSlashCommand("clear", [])
        return
      case "left":
      case "right":
        return
      default:
        return
    }
  }

  async execute(step: ScriptStep): Promise<ExecutionResult> {
    switch (step.action) {
      case "type":
        await this.typeText(step.text, step.delayMs ?? 0)
        return null
      case "send":
        await this.controller.handleInput(step.text)
        return null
      case "press": {
        const repeat = Math.max(1, step.repeat ?? 1)
        const key = resolveKey(step.key)
        for (let i = 0; i < repeat; i += 1) {
          await this.pressKey(key)
          if ((step.delayMs ?? 0) > 0) {
            await sleep(step.delayMs ?? 0)
          }
        }
        return null
      }
      case "wait":
        await this.handleWait(step)
        return null
      case "delay":
        await sleep(step.ms)
        return null
      case "openModelMenu":
        await this.controller.openModelMenu()
        return null
      case "selectModel":
        await this.controller.selectModel(step.value)
        return null
      case "snapshot":
        return {
          kind: "snapshot",
          label: step.label,
          options: {
            ...this.baseRenderOptions,
            colors: step.colors ?? this.baseRenderOptions.colors,
            includeHints: step.includeHints ?? this.baseRenderOptions.includeHints,
            includeModelMenu: step.includeModelMenu ?? this.baseRenderOptions.includeModelMenu,
            includeHeader: step.includeHeader ?? this.baseRenderOptions.includeHeader,
          },
        }
      case "runCommand":
        await this.controller.runSessionCommand(step.command, step.payload, step.successMessage)
        return null
      case "log":
        return {
          kind: "log",
          message: step.message,
          label: step.label,
        }
      case "clearInput":
        this.inputBuffer = ""
        this.updateSuggestions()
        return null
      default:
        return null
    }
  }

  private async handleWait(step: WaitStep): Promise<void> {
    switch (step.for) {
      case "completion":
        await this.controller.waitForCompletion(step.timeoutMs)
        return
      case "assistant": {
        const initialCount = this.state.conversation.filter((entry) => entry.speaker === "assistant").length
        await this.controller.waitFor(
          (state) => state.conversation.filter((entry) => entry.speaker === "assistant").length > initialCount,
          step.timeoutMs,
        )
        return
      }
      case "status":
        await this.controller.waitFor((state) => state.status.toLowerCase().includes(step.includes.toLowerCase()), step.timeoutMs)
        return
      case "hint":
        await this.controller.waitFor(
          (state) => state.hints.some((hint) => hint.toLowerCase().includes(step.includes.toLowerCase())),
          step.timeoutMs,
        )
        return
      case "events":
        await this.controller.waitFor((state) => state.stats.eventCount >= step.count, step.timeoutMs)
        return
      default:
        return
    }
  }
}

export const loadScript = async (scriptPath: string): Promise<ScriptDefinition> => {
  const absolute = path.isAbsolute(scriptPath) ? scriptPath : path.join(process.cwd(), scriptPath)
  const raw = await fs.readFile(absolute, "utf8")
  const data = JSON.parse(raw) as unknown
  const steps = normalizeScript(data)
  return { steps }
}

export const runScript = async (
  controller: ReplSessionController,
  definition: ScriptDefinition,
  options: ScriptRunOptions = {},
): Promise<ScriptRunResult> => {
  const finalOnly = options.finalOnly === true
  const baseRenderOptions = options.renderOptions ?? {}
  const runtime = new ScriptRuntime(controller, baseRenderOptions)
  const snapshots: ScriptSnapshot[] = []

  try {
    let headerRendered = false
    let snapshotIndex = 0

    const renderSnapshot = (label: string, overrides?: RenderTextOptions) => {
      if (finalOnly) return
      const includeHeader =
        overrides?.includeHeader !== undefined
          ? overrides.includeHeader
          : !headerRendered && (baseRenderOptions.includeHeader ?? true)
      const includeStatus =
        overrides?.includeStatus !== undefined
          ? overrides.includeStatus
          : (!headerRendered && includeHeader) || (baseRenderOptions.includeStatus ?? false)
      const text = renderStateToText(runtime.getState(), {
        ...baseRenderOptions,
        ...overrides,
        includeHeader,
        includeStatus,
      })
      if (includeHeader) headerRendered = true
      snapshots.push({ index: snapshotIndex, label, text })
      snapshotIndex += 1
    }

    const logMessage = (message: string, label?: string) => {
      if (finalOnly) return
      const entryLabel = label ?? `log-${snapshotIndex}`
      snapshots.push({
        index: snapshotIndex,
        label: entryLabel,
        text: message,
      })
      snapshotIndex += 1
    }

    // Always capture the initial state so the first snapshot owns the header.
    if (!finalOnly) {
      renderSnapshot("initial")
    }

    for (let i = 0; i < definition.steps.length; i += 1) {
      const step = definition.steps[i]
      const result = await runtime.execute(step)

      if (result?.kind === "snapshot") {
        renderSnapshot(result.label ?? `step-${i + 1}`, result.options)
      } else if (result?.kind === "log") {
        logMessage(result.message, result.label ? `${result.label}-${i + 1}` : undefined)
      }

      if (!finalOnly && options.snapshotAfterEachStep && step.action !== "snapshot") {
        renderSnapshot(`step-${i + 1}`)
      }
    }

    if (finalOnly) {
      const text = renderStateToText(runtime.getState(), {
        ...baseRenderOptions,
        includeHeader: true,
        includeStatus: true,
        includeStreamingTail: false,
      })
      snapshots.push({ index: 0, label: "final", text })
    } else {
      renderSnapshot("final", { includeHeader: !headerRendered && (baseRenderOptions.includeHeader ?? true) })
    }
    return { snapshots, finalState: runtime.getState() }
  } finally {
    runtime.dispose()
  }
}
