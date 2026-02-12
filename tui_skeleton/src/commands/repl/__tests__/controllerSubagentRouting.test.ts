import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  timestamp: seq,
  payload,
})

describe("ReplSessionController subagent routing", () => {
  it("keeps task_event tool-rail lines in baseline mode", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-1", status: "running", description: "Index" }))
    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(true)
  })

  it("routes task_event away from tool rail when subagent v2 is enabled", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: false,
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-2", status: "running", description: "Plan" }))
    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(false)
    expect(state.workGraph.itemOrder).toContain("task-2")
  })

  it("emits subagent toast slots when enabled", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-3", status: "running", description: "Research" }))
    const state = controller.getState()
    expect(state.liveSlots.some((slot: any) => String(slot.text).includes("[subagent]"))).toBe(true)
  })
})
