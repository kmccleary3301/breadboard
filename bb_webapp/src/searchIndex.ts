import type { SessionEvent } from "@breadboard/sdk"
import type { ToolRow, TranscriptRow } from "./projection"

export type SearchEntryType = "transcript" | "tool" | "artifact"

export type SearchEntry = {
  id: string
  type: SearchEntryType
  text: string
  path?: string
  timestamp: number
}

export type SearchResult = SearchEntry & {
  score: number
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const readTimestamp = (value: unknown, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback

export const buildSearchEntries = (args: {
  transcript: readonly TranscriptRow[]
  toolRows: readonly ToolRow[]
  events: readonly SessionEvent[]
  limits: { transcript: number; toolRows: number; events: number }
}): SearchEntry[] => {
  const entries: SearchEntry[] = []

  for (const row of args.transcript.slice(-args.limits.transcript)) {
    entries.push({
      id: row.id,
      type: "transcript",
      text: row.text,
      timestamp: 0,
    })
  }

  for (const row of args.toolRows.slice(-args.limits.toolRows)) {
    entries.push({
      id: row.id,
      type: "tool",
      text: `${row.label} ${row.summary}`,
      timestamp: row.timestamp,
    })
  }

  for (const event of args.events.slice(-args.limits.events)) {
    const payload = isRecord(event.payload) ? event.payload : {}
    const path =
      readString(payload.path) ??
      readString(payload.file) ??
      readString(payload.artifact) ??
      readString(payload.artifact_path)
    if (!path) continue
    const text =
      readString(payload.summary) ??
      readString(payload.message) ??
      readString(payload.description) ??
      `${event.type} ${path}`
    entries.push({
      id: `artifact-${event.id}`,
      type: "artifact",
      text,
      path,
      timestamp: readTimestamp(payload.timestamp_ms ?? payload.timestamp, event.timestamp),
    })
  }

  return entries
}

export const searchEntries = (
  entries: readonly SearchEntry[],
  query: string,
  filters?: { type?: SearchEntryType | "all" },
): SearchResult[] => {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const rows: SearchResult[] = []
  for (const entry of entries) {
    if (filters?.type && filters.type !== "all" && entry.type !== filters.type) continue
    const haystack = `${entry.text} ${entry.path ?? ""}`.toLowerCase()
    const index = haystack.indexOf(q)
    if (index < 0) continue
    const score = (index === 0 ? 200 : 0) + Math.max(0, 100 - index) + Math.min(80, q.length * 4)
    rows.push({ ...entry, score })
  }
  return rows.sort((a, b) => {
    if (a.score !== b.score) return b.score - a.score
    if (a.timestamp !== b.timestamp) return b.timestamp - a.timestamp
    return a.id.localeCompare(b.id)
  })
}
