import type { SessionEvent } from "@breadboard/sdk"

export type CheckpointRow = {
  id: string
  label: string
  createdAt: number
  summary: string
}

export type CheckpointRestoreResult = {
  checkpointId: string | null
  status: "ok" | "error"
  message: string
  at: number
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const readNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

const asRecordArray = (value: unknown): Record<string, unknown>[] => {
  if (!Array.isArray(value)) return []
  return value.filter((entry): entry is Record<string, unknown> => isRecord(entry))
}

const parseCheckpointRow = (value: Record<string, unknown>, fallbackId: string, fallbackTimestamp: number): CheckpointRow => {
  const id =
    readString(value.checkpoint_id) ??
    readString(value.checkpointId) ??
    readString(value.id) ??
    fallbackId
  const label =
    readString(value.label) ??
    readString(value.name) ??
    readString(value.title) ??
    id
  const createdAt =
    readNumber(value.created_at_ms) ??
    readNumber(value.created_at) ??
    readNumber(value.timestamp_ms) ??
    readNumber(value.timestamp) ??
    fallbackTimestamp
  const summary =
    readString(value.summary) ??
    readString(value.message) ??
    readString(value.description) ??
    label
  return { id, label, createdAt, summary }
}

export const parseCheckpointListEvent = (event: SessionEvent): CheckpointRow[] => {
  const payload = isRecord(event.payload) ? event.payload : {}
  const rows = asRecordArray(payload.checkpoints ?? payload.items ?? payload.list)
  const checkpoints = rows.map((row, index) => parseCheckpointRow(row, `${event.id}-${index}`, event.timestamp))
  const deduped = new Map<string, CheckpointRow>()
  for (const row of checkpoints) {
    deduped.set(row.id, row)
  }
  return [...deduped.values()].sort((a, b) => {
    if (a.createdAt !== b.createdAt) return b.createdAt - a.createdAt
    return a.id.localeCompare(b.id)
  })
}

export const parseCheckpointRestoreEvent = (event: SessionEvent): CheckpointRestoreResult => {
  const payload = isRecord(event.payload) ? event.payload : {}
  const checkpointId =
    readString(payload.checkpoint_id) ??
    readString(payload.checkpointId) ??
    readString(payload.id) ??
    null
  const statusRaw = readString(payload.status) ?? readString(payload.result)
  const hasFailureSignal = payload.ok === false || payload.error === true
  const hasSuccessSignal = payload.ok === true || statusRaw === "ok" || statusRaw === "success"
  const status: "ok" | "error" = hasFailureSignal ? "error" : hasSuccessSignal || !statusRaw ? "ok" : "error"
  const message =
    readString(payload.message) ??
    (status === "ok"
      ? `checkpoint restored${checkpointId ? `: ${checkpointId}` : ""}`
      : `checkpoint restore failed${checkpointId ? `: ${checkpointId}` : ""}`)
  return {
    checkpointId,
    status,
    message,
    at: event.timestamp,
  }
}
