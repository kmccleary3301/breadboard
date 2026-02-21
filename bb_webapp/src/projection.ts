import type { SessionEvent } from "@breadboard/sdk"
import { parseCheckpointListEvent, parseCheckpointRestoreEvent, type CheckpointRestoreResult, type CheckpointRow } from "./checkpoints"
import { extractDiffPayload } from "./diffParser"
import { applyTaskGraphEvent, initialTaskGraphState, type TaskGraphState } from "./taskGraph"

export type TranscriptRow = {
  id: string
  role: "user" | "assistant" | "system"
  text: string
  final: boolean
}

export type ToolRow = {
  id: string
  type: "tool_call" | "tool_result"
  label: string
  summary: string
  timestamp: number
  diffText: string | null
  diffFilePath: string | null
}

export type PermissionScope = "session" | "project"

export type PermissionRequestRow = {
  requestId: string
  tool: string
  kind: string
  summary: string
  diffText: string | null
  ruleSuggestion: string | null
  defaultScope: PermissionScope
  rewindable: boolean
  createdAt: number
}

export type PermissionLedgerDecision =
  | "allow-once"
  | "allow-always"
  | "deny-once"
  | "deny-always"
  | "deny-stop"
  | "revoke"
  | "unknown"

export type PermissionLedgerRow = {
  requestId: string
  tool: string
  scope: PermissionScope
  decision: PermissionLedgerDecision
  rule: string | null
  note: string | null
  timestamp: number
  revoked: boolean
}

export type ProjectionState = {
  transcript: TranscriptRow[]
  toolRows: ToolRow[]
  events: SessionEvent[]
  pendingPermissions: PermissionRequestRow[]
  permissionLedger: PermissionLedgerRow[]
  checkpoints: CheckpointRow[]
  activeCheckpointId: string | null
  checkpointRestoreInFlight: boolean
  lastCheckpointRestore: CheckpointRestoreResult | null
  taskGraph: TaskGraphState
  activeAssistantRowId: string | null
}

export const PROJECTION_LIMITS = {
  events: 1000,
  transcript: 800,
  toolRows: 400,
  pendingPermissions: 64,
  permissionLedger: 256,
  checkpoints: 256,
  taskNodes: 512,
} as const

export const initialProjectionState: ProjectionState = {
  transcript: [],
  toolRows: [],
  events: [],
  pendingPermissions: [],
  permissionLedger: [],
  checkpoints: [],
  activeCheckpointId: null,
  checkpointRestoreInFlight: false,
  lastCheckpointRestore: null,
  taskGraph: initialTaskGraphState,
  activeAssistantRowId: null,
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const extractText = (payload: unknown): string | null => {
  if (!isRecord(payload)) return null
  return (
    readString(payload.text) ??
    readString(payload.content) ??
    readString(payload.delta) ??
    readString(payload.message) ??
    readString(payload.summary)
  )
}

const extractToolName = (payload: unknown): string => {
  if (!isRecord(payload)) return "tool"
  return (
    readString(payload.tool_name) ??
    readString(payload.tool) ??
    readString(payload.name) ??
    readString(payload.id) ??
    "tool"
  )
}

const safeJson = (value: unknown): string => {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const appendBounded = <T>(rows: readonly T[], next: T, max: number): T[] => {
  if (max <= 0) return []
  if (rows.length + 1 <= max) return [...rows, next]
  const dropCount = rows.length + 1 - max
  return [...rows.slice(dropCount), next]
}

const normalizePermissionScope = (value: unknown): PermissionScope =>
  typeof value === "string" && value.trim().toLowerCase() === "session" ? "session" : "project"

const normalizePermissionRequest = (payload: unknown, fallbackRequestId: string): PermissionRequestRow => {
  const record = isRecord(payload) ? payload : {}
  const metadata = isRecord(record.metadata) ? record.metadata : {}
  const requestId =
    readString(record.request_id) ??
    readString(record.requestId) ??
    readString(record.permission_id) ??
    readString(record.permissionId) ??
    readString(record.id) ??
    fallbackRequestId
  const tool =
    readString(record.tool) ??
    readString(record.tool_name) ??
    readString(record.name) ??
    readString(metadata.function) ??
    "tool"
  const kind =
    readString(record.kind) ??
    readString(record.category) ??
    readString(record.type) ??
    readString(metadata.kind) ??
    tool
  const summary =
    readString(record.summary) ??
    readString(record.message) ??
    readString(record.prompt) ??
    readString(metadata.summary) ??
    `Permission required for ${tool}`
  const diffText =
    readString(record.diff) ??
    readString(record.diff_text) ??
    readString(metadata.diff) ??
    null
  const ruleSuggestion =
    readString(record.rule_suggestion) ??
    readString(record.ruleSuggestion) ??
    readString(record.rule) ??
    readString(metadata.rule_suggestion) ??
    readString(metadata.approval_pattern) ??
    null

  return {
    requestId,
    tool,
    kind,
    summary,
    diffText,
    ruleSuggestion,
    defaultScope: normalizePermissionScope(record.default_scope ?? metadata.default_scope),
    rewindable: record.rewindable === false ? false : true,
    createdAt: Date.now(),
  }
}

const normalizePermissionDecision = (value: unknown): PermissionLedgerDecision => {
  const text = readString(value)?.toLowerCase()
  if (!text) return "unknown"
  if (text.includes("allow") && text.includes("always")) return "allow-always"
  if (text.includes("allow")) return "allow-once"
  if (text.includes("deny") && text.includes("always")) return "deny-always"
  if (text.includes("deny") && text.includes("stop")) return "deny-stop"
  if (text.includes("deny")) return "deny-once"
  if (text.includes("revoke")) return "revoke"
  return "unknown"
}

const upsertPermissionLedger = (rows: readonly PermissionLedgerRow[], next: PermissionLedgerRow): PermissionLedgerRow[] => {
  const index = rows.findIndex((row) => row.requestId === next.requestId)
  if (index < 0) return appendBounded(rows, next, PROJECTION_LIMITS.permissionLedger)
  const out = [...rows]
  out[index] = { ...rows[index], ...next }
  return out
}

const normalizePermissionResponseLedger = (payload: unknown, pending: readonly PermissionRequestRow[], timestamp: number): PermissionLedgerRow | null => {
  const record = isRecord(payload) ? payload : {}
  const requestId =
    readString(record.request_id) ??
    readString(record.requestId) ??
    readString(record.permission_id) ??
    readString(record.permissionId) ??
    readString(record.id)
  if (!requestId) return null

  const fromPending = pending.find((entry) => entry.requestId === requestId)
  const tool =
    readString(record.tool) ??
    readString(record.tool_name) ??
    readString(record.name) ??
    fromPending?.tool ??
    "tool"
  const scope = normalizePermissionScope(record.scope ?? record.default_scope ?? fromPending?.defaultScope)
  const decision = normalizePermissionDecision(record.decision ?? record.response ?? record.action)
  const rule = readString(record.rule) ?? readString(record.rule_suggestion) ?? fromPending?.ruleSuggestion ?? null
  const note = readString(record.note) ?? null
  const revoked = decision === "revoke"

  return {
    requestId,
    tool,
    scope,
    decision,
    rule,
    note,
    timestamp,
    revoked,
  }
}

const boundedTaskGraph = (graph: TaskGraphState, maxNodes: number): TaskGraphState => {
  const ids = Object.keys(graph.nodesById)
  if (ids.length <= maxNodes) return graph
  const sorted = ids.sort((a, b) => {
    const nodeA = graph.nodesById[a]
    const nodeB = graph.nodesById[b]
    if (nodeA.updatedAt !== nodeB.updatedAt) return nodeA.updatedAt - nodeB.updatedAt
    return a.localeCompare(b)
  })
  const keep = new Set(sorted.slice(-maxNodes))
  const nodesById: TaskGraphState["nodesById"] = {}
  for (const id of keep) {
    nodesById[id] = graph.nodesById[id]
  }
  const childrenByParent: TaskGraphState["childrenByParent"] = {}
  for (const [parentId, children] of Object.entries(graph.childrenByParent)) {
    if (!keep.has(parentId)) continue
    const filtered = children.filter((id) => keep.has(id))
    if (filtered.length > 0) childrenByParent[parentId] = filtered
  }
  return {
    ...graph,
    nodesById,
    childrenByParent,
    rootIds: graph.rootIds.filter((id) => keep.has(id)),
    activeNodeId: graph.activeNodeId && keep.has(graph.activeNodeId) ? graph.activeNodeId : null,
  }
}

export const dismissPermissionRequest = (state: ProjectionState, requestId: string): ProjectionState => ({
  ...state,
  pendingPermissions: state.pendingPermissions.filter((entry) => entry.requestId !== requestId),
})

export const applyEventToProjection = (state: ProjectionState, event: SessionEvent): ProjectionState => {
  if (state.events.some((row) => row.id === event.id && row.type === event.type && row.session_id === event.session_id)) {
    return state
  }

  const nextEvents = [...state.events, event]
  if (nextEvents.length > PROJECTION_LIMITS.events) {
    nextEvents.splice(0, nextEvents.length - PROJECTION_LIMITS.events)
  }

  if (event.type === "user_message") {
    const text = extractText(event.payload)
    if (!text) return { ...state, events: nextEvents }
    return {
      ...state,
      events: nextEvents,
      transcript: appendBounded(state.transcript, { id: event.id, role: "user", text, final: true }, PROJECTION_LIMITS.transcript),
    }
  }

  if (event.type === "assistant_message") {
    const text = extractText(event.payload)
    if (!text) return { ...state, events: nextEvents, activeAssistantRowId: null }
    return {
      ...state,
      events: nextEvents,
      activeAssistantRowId: null,
      transcript: appendBounded(
        state.transcript,
        { id: event.id, role: "assistant", text, final: true },
        PROJECTION_LIMITS.transcript,
      ),
    }
  }

  if (event.type === "assistant.message.delta" || event.type === "assistant_delta") {
    const delta = extractText(event.payload)
    if (!delta) return { ...state, events: nextEvents }
    if (state.activeAssistantRowId) {
      const idx = state.transcript.findIndex((row) => row.id === state.activeAssistantRowId)
      if (idx >= 0) {
        const nextTranscript = [...state.transcript]
        nextTranscript[idx] = { ...nextTranscript[idx], text: `${nextTranscript[idx].text}${delta}` }
        return {
          ...state,
          events: nextEvents,
          transcript: nextTranscript,
        }
      }
    }
    const rowId = `assistant-stream-${event.id}`
    return {
      ...state,
      events: nextEvents,
      activeAssistantRowId: rowId,
      transcript: appendBounded(
        state.transcript,
        { id: rowId, role: "assistant", text: delta, final: false },
        PROJECTION_LIMITS.transcript,
      ),
    }
  }

  if (event.type === "assistant.message.end") {
    if (!state.activeAssistantRowId) return { ...state, events: nextEvents }
    const idx = state.transcript.findIndex((row) => row.id === state.activeAssistantRowId)
    if (idx < 0) return { ...state, events: nextEvents, activeAssistantRowId: null }
    const nextTranscript = [...state.transcript]
    nextTranscript[idx] = { ...nextTranscript[idx], final: true }
    return {
      ...state,
      events: nextEvents,
      activeAssistantRowId: null,
      transcript: nextTranscript,
    }
  }

  if (event.type === "tool_call") {
    const label = extractToolName(event.payload)
    return {
      ...state,
      events: nextEvents,
      toolRows: appendBounded(
        state.toolRows,
        {
          id: event.id,
          type: "tool_call",
          label,
          summary: safeJson(event.payload),
          timestamp: event.timestamp,
          diffText: null,
          diffFilePath: null,
        },
        PROJECTION_LIMITS.toolRows,
      ),
    }
  }

  if (event.type === "tool.result" || event.type === "tool_result") {
    const label = extractToolName(event.payload)
    const diff = extractDiffPayload(event.payload)
    return {
      ...state,
      events: nextEvents,
      toolRows: appendBounded(
        state.toolRows,
        {
          id: event.id,
          type: "tool_result",
          label,
          summary: safeJson(event.payload),
          timestamp: event.timestamp,
          diffText: diff?.diffText ?? null,
          diffFilePath: diff?.filePath ?? null,
        },
        PROJECTION_LIMITS.toolRows,
      ),
    }
  }

  if (event.type === "error") {
    const text = extractText(event.payload) ?? "Engine error event"
    return {
      ...state,
      events: nextEvents,
      transcript: appendBounded(state.transcript, { id: event.id, role: "system", text, final: true }, PROJECTION_LIMITS.transcript),
    }
  }

  if (event.type === "permission_request") {
    const request = normalizePermissionRequest(event.payload, event.id)
    const filtered = state.pendingPermissions.filter((entry) => entry.requestId !== request.requestId)
    return {
      ...state,
      events: nextEvents,
      pendingPermissions: appendBounded(filtered, request, PROJECTION_LIMITS.pendingPermissions),
    }
  }

  if (event.type === "permission_response") {
    const record = isRecord(event.payload) ? event.payload : {}
    const requestId =
      readString(record.request_id) ??
      readString(record.requestId) ??
      readString(record.permission_id) ??
      readString(record.permissionId) ??
      readString(record.id)
    const ledgerRow = normalizePermissionResponseLedger(event.payload, state.pendingPermissions, event.timestamp)
    return {
      ...state,
      events: nextEvents,
      pendingPermissions: requestId ? state.pendingPermissions.filter((entry) => entry.requestId !== requestId) : state.pendingPermissions,
      permissionLedger: ledgerRow ? upsertPermissionLedger(state.permissionLedger, ledgerRow) : state.permissionLedger,
    }
  }

  if (event.type === "checkpoint_list") {
    const checkpoints = parseCheckpointListEvent(event).slice(0, PROJECTION_LIMITS.checkpoints)
    const activeCheckpointId =
      state.activeCheckpointId && checkpoints.some((row) => row.id === state.activeCheckpointId)
        ? state.activeCheckpointId
        : checkpoints[0]?.id ?? null
    return {
      ...state,
      events: nextEvents,
      checkpoints,
      activeCheckpointId,
      checkpointRestoreInFlight: false,
    }
  }

  if (event.type === "checkpoint_restored") {
    const restore = parseCheckpointRestoreEvent(event)
    return {
      ...state,
      events: nextEvents,
      checkpointRestoreInFlight: false,
      lastCheckpointRestore: restore,
      activeCheckpointId: restore.checkpointId ?? state.activeCheckpointId,
    }
  }

  if (event.type === "task_event" || event.type === "ctree_node" || event.type === "ctree_snapshot") {
    const nextGraph = boundedTaskGraph(applyTaskGraphEvent(state.taskGraph, event), PROJECTION_LIMITS.taskNodes)
    return {
      ...state,
      events: nextEvents,
      taskGraph: nextGraph,
    }
  }

  return {
    ...state,
    events: nextEvents,
  }
}
