import { describe, it, expect } from "vitest"
import { createEmptyCTreeModel, reduceCTreeModel } from "../src/repl/ctrees/reducer.js"

describe("CTree reducer", () => {
  it("adds nodes and groups them by turn", () => {
    const state0 = createEmptyCTreeModel()
    const node = { id: "n1", digest: "d1", kind: "message", turn: 1, payload: { text: "hello" } }
    const snapshot = {
      schema_version: "0.1",
      node_count: 1,
      event_count: 1,
      last_id: "n1",
      node_hash: "h1",
    }

    const state1 = reduceCTreeModel(state0, { type: "ctree_node", node, snapshot })
    expect(state1.nodeOrder).toEqual(["n1"])
    expect(state1.byTurn["1"]).toEqual(["n1"])
    expect(state1.nodesById["n1"]).toEqual(node)
    expect(state1.snapshot).toEqual(snapshot)
  })

  it("dedupes repeated node ids", () => {
    const state0 = createEmptyCTreeModel()
    const node = { id: "n1", digest: "d1", kind: "message", turn: 1, payload: { text: "hello" } }
    const snapshot = {
      schema_version: "0.1",
      node_count: 1,
      event_count: 1,
      last_id: "n1",
      node_hash: "h1",
    }

    const state1 = reduceCTreeModel(state0, { type: "ctree_node", node, snapshot })
    const state2 = reduceCTreeModel(state1, { type: "ctree_node", node, snapshot })
    expect(state2.nodeOrder).toEqual(["n1"])
    expect(state2.byTurn["1"]).toEqual(["n1"])
  })

  it("resets to empty", () => {
    const state0 = createEmptyCTreeModel()
    const node = { id: "n1", digest: "d1", kind: "message", turn: 1, payload: { text: "hello" } }
    const snapshot = {
      schema_version: "0.1",
      node_count: 1,
      event_count: 1,
      last_id: "n1",
      node_hash: "h1",
    }
    const state1 = reduceCTreeModel(state0, { type: "ctree_node", node, snapshot })
    const state2 = reduceCTreeModel(state1, { type: "reset" })
    expect(state2).toEqual(createEmptyCTreeModel())
  })

  it("seeds snapshot without changing nodes", () => {
    const state0 = createEmptyCTreeModel()
    const seeded = reduceCTreeModel(state0, {
      type: "seed_snapshot",
      snapshot: {
        schema_version: "0.1",
        node_count: 0,
        event_count: 0,
        last_id: null,
        node_hash: null,
      },
    })
    expect(seeded.nodeOrder).toEqual([])
    expect(seeded.byTurn).toEqual({})
    expect(seeded.snapshot?.schema_version).toBe("0.1")
  })

  it("updates snapshot via ctree_snapshot action", () => {
    const state0 = createEmptyCTreeModel()
    const snapshot = {
      schema_version: "0.1",
      node_count: 2,
      event_count: 2,
      last_id: "n2",
      node_hash: "h2",
    }
    const state1 = reduceCTreeModel(state0, { type: "ctree_snapshot", snapshot })
    expect(state1.snapshot).toEqual(snapshot)
  })
})
