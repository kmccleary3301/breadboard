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
    getState: () => any
    viewPrefs: Record<string, unknown>
    runtimeFlags: Record<string, unknown>
  }

describe("ReplSessionController thinking policy modes", () => {
  it("starts a thinking artifact at turn start even before reasoning deltas arrive", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: false }

    controller.applyEvent(event(1, "turn_start"))

    const state = controller.getState()
    expect(state.thinkingArtifact?.mode).toBe("summary")
    expect(state.thinkingArtifact?.summary).toBe("")
    expect(state.thinkingArtifact?.finalizedAt).toBeNull()
  })

  it("defaults to summary mode and keeps raw reasoning hidden", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: false }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "step one" }))

    const state = controller.getState()
    expect(state.thinkingArtifact?.mode).toBe("summary")
    expect(state.thinkingArtifact?.summary).toContain("step one")
    expect(state.thinkingArtifact?.rawText).toBeNull()
    expect(state.runtimeTelemetry?.thinkingUpdates).toBe(1)
  })

  it("supports full mode only when reasoning view is explicitly enabled", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: true }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "full detail" }))

    const state = controller.getState()
    expect(state.thinkingArtifact?.mode).toBe("full")
    expect(state.thinkingArtifact?.summary).toContain("full detail")
    expect(state.thinkingArtifact?.rawText).toContain("full detail")
    expect(state.runtimeTelemetry?.thinkingUpdates).toBe(1)
  })

  it("disables thinking artifacts when runtime thinking is off", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: false, allowFullThinking: true }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "should not persist" }))

    const state = controller.getState()
    expect(state.thinkingArtifact).toBeNull()
    expect(state.runtimeTelemetry?.thinkingUpdates).toBe(0)
  })

  it("keeps summary mode when reasoning view is on but full mode guard is off", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: false }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "guarded detail" }))

    const state = controller.getState()
    expect(state.thinkingArtifact?.mode).toBe("summary")
    expect(state.thinkingArtifact?.rawText).toBeNull()
  })

  it("keeps reasoning peek summary-only unless explicit raw peek override is enabled", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: true,
      allowRawThinkingPeek: false,
      statusUpdateMs: 0,
    }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "raw should stay hidden in peek" }))
    controller.applyEvent(event(3, "assistant.thought_summary.delta", { delta: "summary is allowed in peek" }))
    let state = controller.getState()
    let reasoningLines = state.toolEvents
      .map((entry: any) => entry.text)
      .filter((line: string) => line.startsWith("[reasoning]"))
    expect(reasoningLines.some((line: string) => line.includes("raw should stay hidden"))).toBe(false)
    expect(reasoningLines.some((line: string) => line.includes("summary is allowed"))).toBe(true)

    controller.runtimeFlags = { ...controller.runtimeFlags, allowRawThinkingPeek: true }
    controller.applyEvent(event(4, "turn_start"))
    controller.applyEvent(event(5, "assistant.reasoning.delta", { delta: "raw now visible" }))
    state = controller.getState()
    reasoningLines = state.toolEvents
      .map((entry: any) => entry.text)
      .filter((line: string) => line.startsWith("[reasoning]"))
    expect(reasoningLines.some((line: string) => line.includes("raw now visible"))).toBe(true)
  })

  it("finalizes thinking artifact on cancel and error terminal paths", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: true }
    controller.runtimeFlags = { ...controller.runtimeFlags, thinkingEnabled: true, allowFullThinking: true }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "interrupted detail" }))
    controller.applyEvent(event(3, "cancel.acknowledged"))
    let state = controller.getState()
    expect(state.thinkingArtifact?.finalizedAt).not.toBeNull()
    expect(state.activity?.primary).toBe("cancelled")

    controller.applyEvent(event(4, "run.start"))
    controller.applyEvent(event(5, "turn_start"))
    controller.applyEvent(event(6, "assistant.reasoning.delta", { delta: "error detail" }))
    controller.applyEvent(event(7, "error", { message: "boom" }))
    state = controller.getState()
    expect(state.thinkingArtifact?.finalizedAt).not.toBeNull()
    expect(state.activity?.primary).toBe("error")
  })
})
