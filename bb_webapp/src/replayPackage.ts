import type { SessionEvent } from "@breadboard/sdk"

const REPLAY_FORMAT = "breadboard.webapp.replay"
const REPLAY_VERSION = 1

type ReplayPackageInput = {
  sessionId: string
  events: readonly SessionEvent[]
  exportedAt?: string
  projectionHash?: string
}

export type ReplayPackageV1 = {
  format: typeof REPLAY_FORMAT
  version: typeof REPLAY_VERSION
  session_id: string
  exported_at: string
  event_count: number
  projection_hash?: string
  events: SessionEvent[]
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const readNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

const sortEvents = (events: readonly SessionEvent[]): SessionEvent[] => {
  return [...events].sort((a, b) => {
    const seqA = typeof a.seq === "number" ? a.seq : Number.MAX_SAFE_INTEGER
    const seqB = typeof b.seq === "number" ? b.seq : Number.MAX_SAFE_INTEGER
    if (seqA !== seqB) return seqA - seqB
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp
    return a.id.localeCompare(b.id)
  })
}

const dedupeEvents = (events: readonly SessionEvent[]): SessionEvent[] => {
  const seen = new Set<string>()
  const rows: SessionEvent[] = []
  for (const event of events) {
    const key = `${event.id}|${event.type}|${event.session_id}`
    if (seen.has(key)) continue
    seen.add(key)
    rows.push(event)
  }
  return rows
}

const validateEvent = (value: unknown, expectedSessionId: string, index: number): SessionEvent => {
  if (!isRecord(value)) {
    throw new Error(`invalid replay event at index ${index}: expected object`)
  }

  const id = readString(value.id)
  if (!id) throw new Error(`invalid replay event at index ${index}: missing id`)

  const type = readString(value.type)
  if (!type) throw new Error(`invalid replay event at index ${index}: missing type`)

  const sessionId = readString(value.session_id)
  if (!sessionId) throw new Error(`invalid replay event at index ${index}: missing session_id`)
  if (sessionId !== expectedSessionId) {
    throw new Error(`invalid replay event at index ${index}: session_id mismatch`)
  }

  const timestamp = readNumber(value.timestamp)
  if (timestamp == null) throw new Error(`invalid replay event at index ${index}: missing timestamp`)

  const turnRaw = value.turn
  const turn = turnRaw == null ? null : readNumber(turnRaw)
  if (turnRaw != null && turn == null) {
    throw new Error(`invalid replay event at index ${index}: invalid turn`)
  }

  const seq = readNumber(value.seq)
  const timestampMs = readNumber(value.timestamp_ms)
  const runId = readString(value.run_id)
  const threadId = readString(value.thread_id)
  const turnId = value.turn_id

  const event: SessionEvent = {
    id,
    type: type as SessionEvent["type"],
    session_id: sessionId,
    turn,
    timestamp,
    payload: (value.payload ?? {}) as SessionEvent["payload"],
    ...(seq != null ? { seq } : {}),
    ...(timestampMs != null ? { timestamp_ms: timestampMs } : {}),
    ...(runId != null ? { run_id: runId } : {}),
    ...(threadId != null ? { thread_id: threadId } : {}),
    ...(typeof turnId === "string" || typeof turnId === "number" || turnId === null ? { turn_id: turnId } : {}),
  }
  return event
}

export const buildReplayPackage = (input: ReplayPackageInput): ReplayPackageV1 => {
  const sessionId = input.sessionId.trim()
  if (!sessionId) throw new Error("session id is required")
  const onlySessionEvents = input.events.filter((event) => event.session_id === sessionId)
  const events = sortEvents(dedupeEvents(onlySessionEvents))
  const projectionHash = readString(input.projectionHash) ?? undefined
  return {
    format: REPLAY_FORMAT,
    version: REPLAY_VERSION,
    session_id: sessionId,
    exported_at: input.exportedAt ?? new Date().toISOString(),
    event_count: events.length,
    ...(projectionHash ? { projection_hash: projectionHash } : {}),
    events,
  }
}

export const serializeReplayPackage = (pkg: ReplayPackageV1): string => `${JSON.stringify(pkg, null, 2)}\n`

export const parseReplayPackage = (raw: string): ReplayPackageV1 => {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw) as unknown
  } catch {
    throw new Error("invalid replay package: expected JSON")
  }
  if (!isRecord(parsed)) throw new Error("invalid replay package: expected object")

  const format = readString(parsed.format)
  if (format !== REPLAY_FORMAT) throw new Error("invalid replay package: unsupported format")

  const version = readNumber(parsed.version)
  if (version !== REPLAY_VERSION) throw new Error("invalid replay package: unsupported version")

  const sessionId = readString(parsed.session_id)
  if (!sessionId) throw new Error("invalid replay package: missing session_id")

  const exportedAt = readString(parsed.exported_at) ?? new Date(0).toISOString()
  const projectionHash = readString(parsed.projection_hash) ?? undefined

  const rows = Array.isArray(parsed.events) ? parsed.events : null
  if (!rows) throw new Error("invalid replay package: events must be an array")

  const events = sortEvents(dedupeEvents(rows.map((event, index) => validateEvent(event, sessionId, index))))
  return {
    format: REPLAY_FORMAT,
    version: REPLAY_VERSION,
    session_id: sessionId,
    exported_at: exportedAt,
    event_count: events.length,
    ...(projectionHash ? { projection_hash: projectionHash } : {}),
    events,
  }
}
