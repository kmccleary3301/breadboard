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

export type ProjectionState = {
  transcript: TranscriptRow[]
  toolRows: ToolRow[]
  events: SessionEvent[]
  activeAssistantRowId: string | null
}

export const initialProjectionState: ProjectionState = {
  transcript: [],
  toolRows: [],
  events: [],
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

export const applyEventToProjection = (state: ProjectionState, event: SessionEvent): ProjectionState => {
  const nextEvents = [...state.events, event]
  if (nextEvents.length > 1000) {
    nextEvents.splice(0, nextEvents.length - 1000)
  }

  if (event.type === "user_message") {
    const text = extractText(event.payload)
    if (!text) return { ...state, events: nextEvents }
    return {
      ...state,
      events: nextEvents,
      transcript: [...state.transcript, { id: event.id, role: "user", text, final: true }],
    }
  }

  if (event.type === "assistant_message") {
    const text = extractText(event.payload)
    if (!text) return { ...state, events: nextEvents, activeAssistantRowId: null }
    return {
      ...state,
      events: nextEvents,
      activeAssistantRowId: null,
      transcript: [...state.transcript, { id: event.id, role: "assistant", text, final: true }],
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
      transcript: [...state.transcript, { id: rowId, role: "assistant", text: delta, final: false }],
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
      toolRows: [
        ...state.toolRows,
        {
          id: event.id,
          type: "tool_call",
          label,
          summary: safeJson(event.payload),
          timestamp: event.timestamp,
        },
      ],
    }
  }

  if (event.type === "tool.result" || event.type === "tool_result") {
    const label = extractToolName(event.payload)
    return {
      ...state,
      events: nextEvents,
      toolRows: [
        ...state.toolRows,
        {
          id: event.id,
          type: "tool_result",
          label,
          summary: safeJson(event.payload),
          timestamp: event.timestamp,
        },
      ],
    }
  }

  if (event.type === "error") {
    const text = extractText(event.payload) ?? "Engine error event"
    return {
      ...state,
      events: nextEvents,
      transcript: [...state.transcript, { id: event.id, role: "system", text, final: true }],
    }
  }

  return {
    ...state,
    events: nextEvents,
  }
}
