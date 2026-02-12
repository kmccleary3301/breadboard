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
    runtimeFlags: Record<string, unknown>
  }

describe("runtime telemetry slash command", () => {
  it("prints deterministic runtime counters and timeline", async () => {
    const controller = createController()
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "assistant.message.start"))
    controller.applyEvent(event(3, "completion", { completed: true }))

    await controller.dispatchSlashCommand("runtime", ["telemetry"])
    const state = controller.getState()
    const last = state.toolEvents[state.toolEvents.length - 1]?.text ?? ""
    expect(last).toContain("[runtime] activity=completed")
    expect(last).toContain("statusTransitions=")
    expect(last).toContain("[timeline]")
    expect(last).toContain("idle ->")
  })

  it("shows disabled-transition behavior when activity runtime is off", async () => {
    const controller = createController()
    controller.runtimeFlags = { ...controller.runtimeFlags, activityEnabled: false }
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "assistant.message.start"))
    controller.applyEvent(event(3, "completion", { completed: true }))

    await controller.dispatchSlashCommand("runtime", [])
    const state = controller.getState()
    const last = state.toolEvents[state.toolEvents.length - 1]?.text ?? ""
    expect(state.activity?.primary).toBe("idle")
    expect(last).toContain("blocked:disabled")
  })
})
