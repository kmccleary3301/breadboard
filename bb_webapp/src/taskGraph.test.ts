import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { applyTaskGraphEvent, initialTaskGraphState } from "./taskGraph"

const event = (partial: Partial<SessionEvent> & Pick<SessionEvent, "id" | "type">): SessionEvent => ({
  id: partial.id,
  type: partial.type,
  session_id: partial.session_id ?? "session-1",
  turn: partial.turn ?? null,
  timestamp: partial.timestamp ?? 1_000,
  payload: partial.payload ?? {},
})

describe("taskGraph", () => {
  it("builds parent-child indexes and active node from task events", () => {
    let graph = initialTaskGraphState
    graph = applyTaskGraphEvent(
      graph,
      event({
        id: "t1",
        type: "task_event",
        payload: { task_id: "root", title: "Root", status: "running" },
      }),
    )
    graph = applyTaskGraphEvent(
      graph,
      event({
        id: "t2",
        type: "task_event",
        payload: { task_id: "child", parent_id: "root", title: "Child", status: "queued" },
      }),
    )
    expect(graph.rootIds).toEqual(["root"])
    expect(graph.childrenByParent.root).toEqual(["child"])
    expect(graph.activeNodeId).toBe("root")
  })

  it("handles out-of-order ctree snapshot then incremental node updates", () => {
    let graph = initialTaskGraphState
    graph = applyTaskGraphEvent(
      graph,
      event({
        id: "s1",
        type: "ctree_snapshot",
        payload: {
          nodes: [{ node_id: "n1", title: "Node 1", status: "queued" }],
        },
      }),
    )
    graph = applyTaskGraphEvent(
      graph,
      event({
        id: "n1-update",
        type: "ctree_node",
        payload: { node_id: "n1", status: "failed" },
      }),
    )
    expect(graph.nodesById.n1.status).toBe("failed")
    expect(graph.nodesById.n1.statusRollup).toBe("failed")
  })
})
