import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"
import { markTodoScopesStale } from "../controllerEventMethods.js"

describe("todo stale semantics", () => {
  it("marks existing scopes stale (reconnect behavior)", () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" })

    // Seed an authoritative update so the scope exists in controller state.
    ;(controller as any).handleToolResult(
      {
        todo: {
          op: "replace",
          scopeKey: "main",
          revision: 1,
          items: [{ title: "Seed", status: "todo" }],
        },
      },
      null,
    )

    expect(controller.getState().todoScopeStale).toBe(false)
    markTodoScopesStale(controller as any)
    expect(controller.getState().todoScopeStale).toBe(true)
  })
})

