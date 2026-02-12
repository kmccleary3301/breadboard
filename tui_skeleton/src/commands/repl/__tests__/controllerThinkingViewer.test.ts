import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    dispatchSlashCommand: (command: string, args: string[]) => Promise<void>
    getState: () => any
    viewPrefs: Record<string, unknown>
    runtimeFlags: Record<string, unknown>
  }

describe("thinking viewer slash command", () => {
  it("shows thinking summary artifact", async () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: false }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "summary line" }))

    await controller.dispatchSlashCommand("thinking", [])

    const state = controller.getState()
    const lastTool = state.toolEvents[state.toolEvents.length - 1]
    expect(lastTool?.text).toContain("[thinking]")
    expect(lastTool?.text).toContain("summary line")
  })

  it("guards raw thinking output when full mode is not explicitly enabled", async () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: false }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "hidden raw text" }))

    await controller.dispatchSlashCommand("thinking", ["raw"])

    const state = controller.getState()
    expect(state.hints.some((hint: string) => hint.includes("Raw thinking unavailable"))).toBe(true)
    const lastTool = state.toolEvents[state.toolEvents.length - 1]
    expect(lastTool?.text).not.toContain("[raw]")
  })

  it("shows bounded raw thinking when full mode opt-in is enabled", async () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: true,
      allowRawThinkingPeek: true,
    }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "full raw text visible" }))

    await controller.dispatchSlashCommand("thinking", ["raw"])

    const state = controller.getState()
    const lastTool = state.toolEvents[state.toolEvents.length - 1]
    expect(lastTool?.text).toContain("[raw]")
    expect(lastTool?.text).toContain("full raw text visible")
  })

  it("enforces thinking preview line/char caps from runtime flags", async () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: true,
      allowRawThinkingPeek: true,
      thinkingMaxChars: 20,
      thinkingMaxLines: 2,
    }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "line1\nline2\nline3-overflow" }))

    await controller.dispatchSlashCommand("thinking", ["summary"])

    const state = controller.getState()
    const lastTool = state.toolEvents[state.toolEvents.length - 1]
    expect(lastTool?.text).toContain("line1")
    expect(lastTool?.text).toContain("line2")
    expect(lastTool?.text).not.toContain("line3-overflow")
  })

  it("throttles repeated reasoning peek updates during delta bursts", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: true,
      allowRawThinkingPeek: true,
      statusUpdateMs: 10_000,
    }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "first thought" }))
    controller.applyEvent(event(3, "assistant.reasoning.delta", { delta: "second thought" }))

    const state = controller.getState()
    const reasoningEntries = state.toolEvents.filter((entry: any) => entry.text.startsWith("[reasoning]"))
    expect(reasoningEntries).toHaveLength(1)
  })
})
