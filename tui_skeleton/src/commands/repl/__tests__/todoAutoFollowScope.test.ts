import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

describe("todo auto-follow scope", () => {
  it("switches active scope to the most recently updated non-active scope when enabled", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      todoAutoFollowScope: "on",
      todoAutoFollowHysteresisMs: 0,
      todoAutoFollowManualOverrideMs: 15000,
    }) as any

    expect(controller.getState().todoScopeKey).toBe("main")

    controller.applyEvent(
      event(1, "tool_result", {
        todo: {
          op: "replace",
          revision: 1,
          scopeKey: "lane_a",
          items: [{ content: "Ship it", status: "in_progress" }],
        },
      }),
    )

    expect(controller.getState().todoScopeKey).toBe("lane_a")
    expect(controller.getState().todos.map((t: any) => t.title)).toEqual(["Ship it"])
  })

  it("respects manual override window", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      todoAutoFollowScope: "on",
      todoAutoFollowHysteresisMs: 0,
      todoAutoFollowManualOverrideMs: 10_000,
    }) as any

    controller.activeTodoScopeKey = "main"
    controller.noteTodoScopeManualSelection()
    const overrideUntil = controller.todoAutoFollowManualOverrideUntilAt
    expect(typeof overrideUntil).toBe("number")

    controller.applyEvent(
      event(1, "tool_result", {
        todo: {
          op: "replace",
          revision: 1,
          scopeKey: "lane_b",
          items: [{ content: "Background", status: "todo" }],
        },
      }),
    )

    expect(controller.getState().todoScopeKey).toBe("main")
  })

  it("applies hysteresis to avoid bouncing between scopes", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      todoAutoFollowScope: "on",
      todoAutoFollowHysteresisMs: 10_000,
      todoAutoFollowManualOverrideMs: 0,
    }) as any

    controller.applyEvent(
      event(1, "tool_result", {
        todo: {
          op: "replace",
          revision: 1,
          scopeKey: "lane_a",
          items: [{ content: "A", status: "todo" }],
        },
      }),
    )
    expect(controller.getState().todoScopeKey).toBe("lane_a")

    controller.applyEvent(
      event(2, "tool_result", {
        todo: {
          op: "replace",
          revision: 2,
          scopeKey: "lane_b",
          items: [{ content: "B", status: "todo" }],
        },
      }),
    )

    // Still lane_a due to hysteresis.
    expect(controller.getState().todoScopeKey).toBe("lane_a")
  })
})

