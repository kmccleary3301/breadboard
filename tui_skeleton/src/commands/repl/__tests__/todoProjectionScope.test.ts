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

  it("ignores unversioned fallbacks once a revisioned update has been observed", () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" })

    ;(controller as any).handleToolResult(
      {
        todo: {
          op: "replace",
          scopeKey: "main",
          revision: 10,
          items: [{ title: "Authoritative", status: "todo" }],
        },
      },
      null,
    )

    // Unversioned replace should be ignored once we've seen a numeric revision for this scope.
    ;(controller as any).handleToolResult(
      {
        tool_name: "TodoWrite",
        todos: [{ content: "Fallback", status: "todo" }],
      },
      null,
    )

    expect(controller.getState().todos.map((t) => t.title)).toEqual(["Authoritative"])
  })

  it("does not fallback to tool parsing when payload.todo is present but invalid", () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" })

    ;(controller as any).handleToolResult(
      {
        todo: { op: "unknown_op", revision: 1 },
        tool_name: "TodoWrite",
        todos: [{ content: "Should not be applied", status: "todo" }],
      },
      null,
    )

    expect(controller.getState().todos).toEqual([])
  })
})
