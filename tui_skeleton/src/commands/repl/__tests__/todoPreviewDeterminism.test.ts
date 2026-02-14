import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"
import { buildTodoPreviewModel } from "../../../repl/components/replView/composer/todoPreview.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

describe("todo preview determinism", () => {
  it("does not change the preview model when only assistant deltas arrive", () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" }) as any

    controller.applyEvent(
      event(1, "tool_result", {
        todo: { op: "replace", revision: 1, items: [{ content: "Ship it", status: "todo" }] },
      }),
    )

    const model1 = buildTodoPreviewModel(controller.getState().todoStore, { maxItems: 7, strategy: "first_n" })

    controller.applyEvent(event(2, "tool_call", { tool_name: "ReadFile", path: "README.md" }))
    controller.applyEvent(event(3, "assistant.message.delta", { delta: "a" }))
    controller.applyEvent(event(4, "assistant.message.delta", { delta: "b" }))
    controller.applyEvent(event(5, "tool_result", { tool_name: "ReadFile", result: "ok" }))
    controller.applyEvent(event(6, "assistant_message", { text: "final" }))

    const model2 = buildTodoPreviewModel(controller.getState().todoStore, { maxItems: 7, strategy: "first_n" })
    expect(model2).toEqual(model1)
  })

  it("clears stale flag only after an accepted todo update", () => {
    const controller = new ReplSessionController({ configPath: "agent_configs/test_simple_native.yaml" }) as any

    controller.applyEvent(
      event(1, "tool_result", {
        todo: { op: "replace", revision: 1, items: [{ content: "Old", status: "todo" }] },
      }),
    )

    controller.todoScopeStaleByKey.main = true
    expect(controller.getState().todoScopeStale).toBe(true)

    controller.applyEvent(
      event(2, "tool_result", {
        todo: { op: "replace", revision: 2, items: [{ content: "New", status: "todo" }] },
      }),
    )

    expect(controller.getState().todoScopeStale).toBe(false)
    expect(controller.getState().todos.map((t: any) => t.title)).toEqual(["New"])
  })
})
