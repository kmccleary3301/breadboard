import type { CTreeNode, CTreeSnapshotSummary } from "../../api/types.js"

export interface CTreeModel {
  readonly nodesById: Record<string, CTreeNode>
  readonly nodeOrder: string[]
  readonly byTurn: Record<string, string[]>
  readonly snapshot: CTreeSnapshotSummary | null
}

export type CTreeModelAction =
  | { readonly type: "reset" }
  | { readonly type: "ctree_node"; readonly node: CTreeNode; readonly snapshot: CTreeSnapshotSummary }
  | { readonly type: "seed_snapshot"; readonly snapshot: CTreeSnapshotSummary | null }
  | { readonly type: "ctree_snapshot"; readonly snapshot: CTreeSnapshotSummary | null }

export const createEmptyCTreeModel = (): CTreeModel => ({
  nodesById: {},
  nodeOrder: [],
  byTurn: {},
  snapshot: null,
})

const turnKey = (turn: number | null | undefined): string => (typeof turn === "number" && Number.isFinite(turn) ? String(turn) : "unknown")

export const reduceCTreeModel = (state: CTreeModel, action: CTreeModelAction): CTreeModel => {
  switch (action.type) {
    case "reset":
      return createEmptyCTreeModel()
    case "seed_snapshot":
      return { ...state, snapshot: action.snapshot }
    case "ctree_snapshot":
      return { ...state, snapshot: action.snapshot }
    case "ctree_node": {
      const id = action.node.id
      if (!id) return state
      const exists = Object.prototype.hasOwnProperty.call(state.nodesById, id)
      const nextNodesById = exists ? state.nodesById : { ...state.nodesById, [id]: action.node }
      const nextNodeOrder = exists ? state.nodeOrder : [...state.nodeOrder, id]
      const key = turnKey(action.node.turn)
      const existingTurn = state.byTurn[key] ?? []
      const nextByTurn = exists
        ? state.byTurn
        : {
            ...state.byTurn,
            [key]: [...existingTurn, id],
          }
      return {
        nodesById: nextNodesById,
        nodeOrder: nextNodeOrder,
        byTurn: nextByTurn,
        snapshot: action.snapshot,
      }
    }
    default:
      return state
  }
}
