import type { CTreeTreeNode, CTreeTreeResponse } from "../../api/types.js"

export type CTreeTreeRow = {
  readonly id: string
  readonly node: CTreeTreeNode
  readonly depth: number
  readonly hasChildren: boolean
  readonly isOrphan: boolean
}

export const buildCTreeTreeRows = (
  tree: CTreeTreeResponse | null | undefined,
  collapsed: ReadonlySet<string>,
): {
  readonly rows: CTreeTreeRow[]
  readonly nodeById: Map<string, CTreeTreeNode>
  readonly childrenByParent: Map<string | null, string[]>
} => {
  const rows: CTreeTreeRow[] = []
  const nodeById = new Map<string, CTreeTreeNode>()
  const childrenByParent = new Map<string | null, string[]>()
  if (!tree?.nodes?.length) {
    return { rows, nodeById, childrenByParent }
  }

  for (const node of tree.nodes) {
    nodeById.set(node.id, node)
    const parentId = node.parent_id ?? null
    const list = childrenByParent.get(parentId)
    if (list) {
      list.push(node.id)
    } else {
      childrenByParent.set(parentId, [node.id])
    }
  }

  const visited = new Set<string>()
  const walk = (nodeId: string, depth: number, isOrphan: boolean) => {
    const node = nodeById.get(nodeId)
    if (!node) return
    if (visited.has(nodeId)) return
    visited.add(nodeId)
    const children = childrenByParent.get(nodeId) ?? []
    rows.push({ id: nodeId, node, depth, hasChildren: children.length > 0, isOrphan })
    if (collapsed.has(nodeId)) return
    for (const childId of children) {
      walk(childId, depth + 1, isOrphan)
    }
  }

  const rootId = tree.root_id
  if (rootId && nodeById.has(rootId)) {
    walk(rootId, 0, false)
  } else {
    const roots = childrenByParent.get(null) ?? []
    for (const root of roots) {
      walk(root, 0, false)
    }
  }

  for (const nodeId of nodeById.keys()) {
    if (!visited.has(nodeId)) {
      walk(nodeId, 0, true)
    }
  }

  return { rows, nodeById, childrenByParent }
}
