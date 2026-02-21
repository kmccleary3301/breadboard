import { useMemo } from "react"
import type { TaskGraphState, TaskNodeRow } from "./taskGraph"

type TaskTreePanelProps = {
  graph: TaskGraphState
  expanded: Record<string, boolean>
  onToggleExpand: (id: string) => void
  onJumpToEvent?: (eventId: string) => void
  failedOnly?: boolean
  activeOnly?: boolean
}

const byUpdatedThenId = (a: TaskNodeRow, b: TaskNodeRow): number => {
  if (a.updatedAt !== b.updatedAt) return b.updatedAt - a.updatedAt
  return a.id.localeCompare(b.id)
}

export default function TaskTreePanel({ graph, expanded, onToggleExpand, onJumpToEvent, failedOnly, activeOnly }: TaskTreePanelProps) {
  const visibleRoots = useMemo(() => {
    const roots = graph.rootIds.map((id) => graph.nodesById[id]).filter((row): row is TaskNodeRow => Boolean(row))
    return roots.filter((row) => {
      if (failedOnly && row.statusRollup !== "failed") return false
      if (activeOnly && graph.activeNodeId && row.id !== graph.activeNodeId) return false
      return true
    })
  }, [activeOnly, failedOnly, graph.activeNodeId, graph.nodesById, graph.rootIds])

  const renderNode = (node: TaskNodeRow, depth: number): JSX.Element => {
    const children = (graph.childrenByParent[node.id] ?? [])
      .map((id) => graph.nodesById[id])
      .filter((row): row is TaskNodeRow => Boolean(row))
      .sort(byUpdatedThenId)
    const isExpanded = expanded[node.id] ?? true
    const isActive = graph.activeNodeId === node.id

    return (
      <div key={node.id} className={`taskNode ${isActive ? "active" : ""}`} style={{ marginLeft: `${depth * 10}px` }}>
        <div className="taskNodeRow">
          {children.length > 0 ? (
            <button type="button" onClick={() => onToggleExpand(node.id)}>
              {isExpanded ? "▾" : "▸"}
            </button>
          ) : (
            <span className="taskSpacer" />
          )}
          <strong>{node.title}</strong>
          <span className={`taskBadge ${node.statusRollup}`}>{node.statusRollup}</span>
          <span className="subtle">{new Date(node.updatedAt).toLocaleTimeString()}</span>
          {onJumpToEvent ? (
            <button type="button" onClick={() => onJumpToEvent(node.eventId)}>
              Jump
            </button>
          ) : null}
        </div>
        {isExpanded ? children.map((child) => renderNode(child, depth + 1)) : null}
      </div>
    )
  }

  if (visibleRoots.length === 0) {
    return <p className="subtle">No task graph nodes yet.</p>
  }

  return <div className="taskTree">{visibleRoots.sort(byUpdatedThenId).map((row) => renderNode(row, 0))}</div>
}
