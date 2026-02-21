import type { ConnectionState, StreamEventEnvelope } from "./hostController"

export type StatePayload = {
  sessions: Array<{ sessionId: string; status?: string; updatedAt?: number }>
  activeSessionId?: string
}

export type EventsPayload = {
  sessionId: string
  events: StreamEventEnvelope[]
  render?: {
    totalEvents: number
    lastEventType: string | null
    lines: string[]
    entries?: Array<Record<string, unknown>>
  }
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const asString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

export const sanitizeConnectionState = (state: ConnectionState): ConnectionState => {
  if (state.status === "connecting") return { status: "connecting" }
  if (state.status === "error") {
    return {
      status: "error",
      message: state.message,
      ...(state.sessionId ? { sessionId: state.sessionId } : {}),
      ...(typeof state.retryCount === "number" ? { retryCount: state.retryCount } : {}),
      ...(state.gapDetected ? { gapDetected: true } : {}),
    }
  }
  return {
    status: "connected",
    ...(state.sessionId ? { sessionId: state.sessionId } : {}),
    ...(state.protocolVersion ? { protocolVersion: state.protocolVersion } : {}),
    ...(state.engineVersion ? { engineVersion: state.engineVersion } : {}),
    ...(state.lastEventId ? { lastEventId: state.lastEventId } : {}),
    ...(typeof state.retryCount === "number" ? { retryCount: state.retryCount } : {}),
  }
}

export const sanitizeStatePayload = (value: unknown): StatePayload => {
  const rec = asRecord(value) ?? {}
  const rows = Array.isArray(rec.sessions) ? rec.sessions : []
  const sessions: StatePayload["sessions"] = []
  for (const row of rows) {
    const item = asRecord(row)
    if (!item) continue
    const sessionId = asString(item.sessionId)
    if (!sessionId) continue
    const status = asString(item.status) ?? undefined
    const updatedAt = asNumber(item.updatedAt) ?? undefined
    sessions.push({
      sessionId,
      ...(status ? { status } : {}),
      ...(updatedAt !== undefined ? { updatedAt } : {}),
    })
  }
  const activeSessionId = asString(rec.activeSessionId) ?? undefined
  return { sessions, ...(activeSessionId ? { activeSessionId } : {}) }
}

const sanitizeEvent = (value: unknown): StreamEventEnvelope | null => {
  const rec = asRecord(value)
  if (!rec) return null
  const id = asString(rec.id)
  const type = asString(rec.type)
  const sessionId = asString(rec.session_id)
  if (!id || !type || !sessionId) return null
  const payload = asRecord(rec.payload) ?? {}
  const turn = asNumber(rec.turn)
  const timestamp = asNumber(rec.timestamp) ?? Date.now()
  return {
    id,
    type,
    session_id: sessionId,
    ...(turn !== null ? { turn } : {}),
    timestamp,
    payload,
  }
}

export const sanitizeEventsPayload = (value: unknown): EventsPayload | null => {
  const rec = asRecord(value)
  if (!rec) return null
  const sessionId = asString(rec.sessionId)
  if (!sessionId) return null
  const rows = Array.isArray(rec.events) ? rec.events : []
  const events: StreamEventEnvelope[] = []
  for (const row of rows) {
    const normalized = sanitizeEvent(row)
    if (normalized) events.push(normalized)
  }
  const renderRec = asRecord(rec.render)
  const render =
    renderRec && asNumber(renderRec.totalEvents) !== null
      ? {
          totalEvents: asNumber(renderRec.totalEvents) ?? 0,
          lastEventType: asString(renderRec.lastEventType) ?? null,
          lines: Array.isArray(renderRec.lines) ? renderRec.lines.map((line) => String(line)) : [],
          ...(Array.isArray(renderRec.entries)
            ? { entries: renderRec.entries.map((entry) => (asRecord(entry) ?? {})) }
            : {}),
        }
      : undefined
  return {
    sessionId,
    events,
    ...(render ? { render } : {}),
  }
}
