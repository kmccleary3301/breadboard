import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"
import { collectRuntimeWarnings, validateRuntimeScenario } from "../controllerRuntimeScenarioValidator.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

describe("runtime scenario validator (warn-mode hook)", () => {
  it("flags no warnings for healthy run/tool/permission/completion sequence", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "turn_start"))
    controller.applyEvent(event(3, "assistant.message.start"))
    controller.applyEvent(event(4, "tool_call", { call_id: "call-warn", tool_name: "Write" }))
    controller.applyEvent(event(5, "permission.request", { request_id: "perm-warn", tool: "Write" }))
    controller.applyEvent(event(6, "permission.decision", { decision: "allow-once" }))
    controller.applyEvent(event(7, "completion", { completed: true }))

    const state = controller.getState()
    const result = validateRuntimeScenario(state, "warn")
    expect(result.warnings).toEqual([])
    expect(result.ok).toBe(true)
    expect(result.summary).toBe("ok")
  })

  it("captures warnings for intentionally bad fixtures and fails in strict mode", () => {
    const badState = {
      activity: { primary: "thinking" },
      completionSeen: true,
      permissionRequest: null,
      runtimeTelemetry: { illegalTransitions: 2 },
    }

    const warnResult = validateRuntimeScenario(badState, "warn")
    const strictResult = validateRuntimeScenario(badState, "strict")

    expect(warnResult.ok).toBe(true)
    expect(strictResult.ok).toBe(false)
    expect(collectRuntimeWarnings(badState)).toContain("illegal-transitions:2")
    expect(strictResult.summary).toContain("completion-with-nonterminal-activity")
  })

  it("validates stream-gap recovery scenario without stuck activity", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "stream.gap"))
    controller.applyEvent(event(2, "run.start"))
    controller.applyEvent(event(3, "assistant.message.start"))
    controller.applyEvent(event(4, "completion", { completed: true }))

    const state = controller.getState()
    const strictResult = validateRuntimeScenario(state, "strict")
    expect(strictResult.ok).toBe(true)
    expect(state.activity?.primary).toBe("completed")
  })

  it("validates compaction lifecycle scenario and exits compacting state", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "conversation.compaction.start"))
    controller.applyEvent(event(3, "conversation.compaction.end"))
    controller.applyEvent(event(4, "completion", { completed: true }))

    const state = controller.getState()
    const strictResult = validateRuntimeScenario(state, "strict")
    expect(strictResult.ok).toBe(true)
    expect(state.activity?.primary).toBe("completed")
    expect(state.hints.some((hint: string) => hint.includes("Compaction started"))).toBe(true)
    expect(state.hints.some((hint: string) => hint.includes("Compaction complete"))).toBe(true)
  })
})
