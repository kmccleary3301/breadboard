import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"
import { validateRuntimeScenario } from "../controllerRuntimeScenarioValidator.js"

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

describe("runtime safety gate", () => {
  it("keeps raw reasoning hidden by default user-visible surfaces", () => {
    const controller = createController()
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: false,
      allowRawThinkingPeek: false,
      statusUpdateMs: 0,
    }

    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.reasoning.delta", { delta: "secret-raw-chain-of-thought" }))
    controller.applyEvent(event(3, "assistant.thought_summary.delta", { delta: "safe summary" }))

    const state = controller.getState()
    const renderedToolText = state.toolEvents.map((entry: any) => entry.text).join("\n")
    expect(renderedToolText).not.toContain("secret-raw-chain-of-thought")
    expect(state.thinkingArtifact?.summary).toContain("safe summary")
    expect(state.thinkingArtifact?.rawText).toBeNull()
  })

  it("keeps permission + tool + answer lifecycle strict-clean", () => {
    const controller = createController()
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "turn_start"))
    controller.applyEvent(event(3, "assistant.message.start"))
    controller.applyEvent(event(4, "tool_call", { call_id: "gate-call", tool_name: "Write" }))
    controller.applyEvent(event(5, "permission.request", { request_id: "gate-perm", tool: "Write" }))
    controller.applyEvent(event(6, "permission.decision", { decision: "allow-once" }))
    controller.applyEvent(event(7, "assistant.message.start"))
    controller.applyEvent(event(8, "assistant.message.delta", { delta: "acknowledged" }))
    controller.applyEvent(event(9, "completion", { completed: true }))

    const state = controller.getState()
    const strictResult = validateRuntimeScenario(state, "strict")
    expect(strictResult.ok).toBe(true)
    expect(state.runtimeTelemetry?.illegalTransitions ?? 0).toBe(0)
    expect(state.activity?.primary).toBe("completed")
  })
})
