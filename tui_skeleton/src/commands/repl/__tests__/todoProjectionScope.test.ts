import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

describe("todo projection scoping + revision gating", () => {
  it("routes updates into per-scope stores and ignores stale revisions", async () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" })

    ;(controller as any).handleToolCall(
      {
        todo: {
          op: "replace",
          scopeKey: "lane-a",
          scopeLabel: "Subagent A",
          revision: 2,
          items: [{ title: "First", status: "todo" }],
        },
      },
      null,
      true,
      false,
    )

    expect(controller.getState().todoScopeOrder).toContain("lane-a")

    await (controller as any).dispatchSlashCommand("todo-scope", ["set", "lane-a"])
    expect(controller.getState().todoScopeKey).toBe("lane-a")
    expect(controller.getState().todoScopeLabel).toBe("Subagent A")
    expect(controller.getState().todos.map((t) => t.title)).toEqual(["First"])

    ;(controller as any).handleToolCall(
      {
        todo: {
          op: "replace",
          scopeKey: "lane-a",
          revision: 1,
          items: [
            { title: "First", status: "todo" },
            { title: "Second", status: "todo" },
          ],
        },
      },
      null,
      true,
      false,
    )

    // Stale revision ignored.
    expect(controller.getState().todos.map((t) => t.title)).toEqual(["First"])
  })
})
