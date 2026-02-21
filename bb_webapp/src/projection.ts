import type { SessionEvent } from "@breadboard/sdk"

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

export type ProjectionState = {
  transcript: TranscriptRow[]
  toolRows: ToolRow[]
  events: SessionEvent[]
  pendingPermissions: PermissionRequestRow[]
  activeAssistantRowId: string | null
}

export const PROJECTION_LIMITS = {
  events: 1000,
  transcript: 800,
  toolRows: 400,
  pendingPermissions: 64,
} as const

export const initialProjectionState: ProjectionState = {
  transcript: [],
  toolRows: [],
  events: [],
  pendingPermissions: [],
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
        },
        PROJECTION_LIMITS.toolRows,
      ),
    }
  }

  if (event.type === "tool.result" || event.type === "tool_result") {
    const label = extractToolName(event.payload)
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
    if (!requestId) {
      return {
        ...state,
        events: nextEvents,
      }
    }
    return {
      ...state,
      events: nextEvents,
      pendingPermissions: state.pendingPermissions.filter((entry) => entry.requestId !== requestId),
    }
  }

  return {
    ...state,
    events: nextEvents,
  }
}
