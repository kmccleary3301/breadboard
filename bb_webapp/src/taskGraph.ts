import type { SessionEvent } from "@breadboard/sdk"

export type TaskNodeStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "unknown"
export type TaskNodeSource = "task_event" | "ctree_node" | "ctree_snapshot"

export type TaskNodeRow = {
  id: string
  parentId: string | null
  title: string
  status: TaskNodeStatus
  statusRollup: TaskNodeStatus
  updatedAt: number
  eventId: string
  source: TaskNodeSource
}

export type TaskGraphState = {
  nodesById: Record<string, TaskNodeRow>
  childrenByParent: Record<string, string[]>
  rootIds: string[]
  activeNodeId: string | null
}

export const initialTaskGraphState: TaskGraphState = {
  nodesById: {},
  childrenByParent: {},
  rootIds: [],
  activeNodeId: null,
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const readNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

const normalizeStatus = (value: unknown): TaskNodeStatus => {
  const text = readString(value)?.toLowerCase()
  if (!text) return "unknown"
  if (text.includes("queue")) return "queued"
  if (text.includes("run") || text.includes("progress") || text.includes("active")) return "running"
  if (text.includes("success") || text.includes("done") || text.includes("complete")) return "succeeded"
  if (text.includes("fail") || text.includes("error")) return "failed"
  if (text.includes("cancel")) return "cancelled"
  return "unknown"
}

const priority = (status: TaskNodeStatus): number => {
  if (status === "failed") return 5
  if (status === "running") return 4
  if (status === "cancelled") return 3
  if (status === "queued") return 2
  if (status === "succeeded") return 1
  return 0
}

const toTaskNode = (payload: unknown, event: SessionEvent, source: TaskNodeSource, fallbackId: string): TaskNodeRow | null => {
  if (!isRecord(payload)) return null
  const id =
    readString(payload.node_id) ??
    readString(payload.nodeId) ??
    readString(payload.task_id) ??
    readString(payload.taskId) ??
    readString(payload.id) ??
    fallbackId
  const parentId =
    readString(payload.parent_id) ??
    readString(payload.parentId) ??
    readString(payload.parent) ??
    null
  const title =
    readString(payload.title) ??
    readString(payload.label) ??
    readString(payload.name) ??
    readString(payload.task) ??
    id
  const updatedAt =
    readNumber(payload.updated_at_ms) ??
    readNumber(payload.updated_at) ??
    readNumber(payload.timestamp_ms) ??
    readNumber(payload.timestamp) ??
    event.timestamp
  const status = normalizeStatus(payload.status ?? payload.state ?? payload.phase)
  return {
    id,
    parentId,
    title,
    status,
    statusRollup: status,
    updatedAt,
    eventId: event.id,
    source,
  }
}

const computeIndexes = (nodesById: Record<string, TaskNodeRow>): Pick<TaskGraphState, "childrenByParent" | "rootIds"> => {
  const childrenByParent: Record<string, string[]> = {}
  const rootIds: string[] = []

  const ids = Object.keys(nodesById).sort((a, b) => {
    const nodeA = nodesById[a]
    const nodeB = nodesById[b]
    if (nodeA.updatedAt !== nodeB.updatedAt) return nodeA.updatedAt - nodeB.updatedAt
    return a.localeCompare(b)
  })

  for (const id of ids) {
    const node = nodesById[id]
    if (node.parentId) {
      const rows = childrenByParent[node.parentId] ?? []
      rows.push(id)
      childrenByParent[node.parentId] = rows
    } else {
      rootIds.push(id)
    }
  }

  return { childrenByParent, rootIds }
}

const rollupStatuses = (graph: TaskGraphState): Record<string, TaskNodeStatus> => {
  const memo: Record<string, TaskNodeStatus> = {}
  const visit = (id: string): TaskNodeStatus => {
    if (memo[id]) return memo[id]
    const node = graph.nodesById[id]
    if (!node) return "unknown"
    let current = node.status
    for (const childId of graph.childrenByParent[id] ?? []) {
      const child = visit(childId)
      if (priority(child) > priority(current)) {
        current = child
      }
    }
    memo[id] = current
    return current
  }
  for (const id of Object.keys(graph.nodesById)) {
    visit(id)
  }
  return memo
}

const withIndexesAndRollup = (nodesById: Record<string, TaskNodeRow>, activeNodeId: string | null): TaskGraphState => {
  const indexes = computeIndexes(nodesById)
  const graph: TaskGraphState = {
    nodesById,
    childrenByParent: indexes.childrenByParent,
    rootIds: indexes.rootIds,
    activeNodeId,
  }
  const rollups = rollupStatuses(graph)
  const nextNodes: Record<string, TaskNodeRow> = {}
  for (const [id, node] of Object.entries(nodesById)) {
    nextNodes[id] = { ...node, statusRollup: rollups[id] ?? node.status }
  }
  return { ...graph, nodesById: nextNodes }
}

const mergeNode = (state: TaskGraphState, node: TaskNodeRow): TaskGraphState => {
  const prev = state.nodesById[node.id]
  const merged = prev
    ? {
        ...prev,
        ...node,
        title: node.title || prev.title,
        parentId: node.parentId ?? prev.parentId,
        status: node.status === "unknown" ? prev.status : node.status,
        updatedAt: Math.max(prev.updatedAt, node.updatedAt),
      }
    : node

  let activeNodeId = state.activeNodeId
  if (node.status === "running") {
    activeNodeId = node.id
  } else if (state.activeNodeId === node.id) {
    activeNodeId = null
  }

  return withIndexesAndRollup(
    {
      ...state.nodesById,
      [node.id]: merged,
    },
    activeNodeId,
  )
}

export const applyTaskGraphEvent = (state: TaskGraphState, event: SessionEvent): TaskGraphState => {
  if (event.type === "ctree_snapshot") {
    const payload = isRecord(event.payload) ? event.payload : {}
    const snapshot = isRecord(payload.snapshot) ? payload.snapshot : payload
    const nodes = Array.isArray(snapshot.nodes) ? snapshot.nodes : Array.isArray(payload.nodes) ? payload.nodes : []
    let next = state
    for (let index = 0; index < nodes.length; index += 1) {
      const parsed = toTaskNode(nodes[index], event, "ctree_snapshot", `${event.id}-snapshot-${index}`)
      if (!parsed) continue
      next = mergeNode(next, parsed)
    }
    return next
  }

  if (event.type === "ctree_node" || event.type === "task_event") {
    const parsed = toTaskNode(event.payload, event, event.type, event.id)
    if (!parsed) return state
    return mergeNode(state, parsed)
  }

  return state
}
