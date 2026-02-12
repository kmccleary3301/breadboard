import type { CTreeSnapshotSummary } from "../../api/types.js"
import type { TodoItem, UsageMetrics } from "../../repl/types.js"

export const MAX_HINTS = 6
export const MAX_TOOL_HISTORY = 400
export const MAX_TOOL_EXEC_OUTPUT = 4000
export const MAX_RAW_EVENTS = 200
export const MAX_RAW_EVENT_CHARS = 400
const rawMaxRetries = Number(process.env.BREADBOARD_STREAM_MAX_RETRIES ?? "5")
export const MAX_RETRIES =
  Number.isFinite(rawMaxRetries) && rawMaxRetries > 0 ? rawMaxRetries : Number.POSITIVE_INFINITY
export const STOP_SOFT_TIMEOUT_MS = 30_000
export const DEBUG_EVENTS = process.env.BREADBOARD_DEBUG_EVENTS === "1"
export const DEBUG_WAIT = process.env.BREADBOARD_DEBUG_WAIT === "1"
export const DEBUG_MARKDOWN = process.env.BREADBOARD_DEBUG_MARKDOWN === "1"
export const DEFAULT_RICH_MARKDOWN = process.env.BREADBOARD_RICH_MARKDOWN !== "0"

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

export const formatErrorPayload = (payload: unknown): string => {
  if (!isRecord(payload)) return JSON.stringify(payload)
  if (typeof payload.message === "string" && payload.message.trim()) return payload.message.trim()
  if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail.trim()
  const detail = payload.detail
  if (isRecord(detail)) {
    const detailMessage =
      typeof detail.message === "string"
        ? detail.message
        : typeof detail.error === "string"
          ? detail.error
          : undefined
    if (detailMessage && detailMessage.trim()) return detailMessage.trim()
  }
  return JSON.stringify(payload)
}

export const stringifyReason = (value: string | undefined): string | undefined =>
  value?.replace(/[_-]+/g, " ").replace(/\\s+/g, " ").trim() || undefined

export const numberOrUndefined = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined

export const parseNumberish = (value: unknown): number | undefined => {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string") {
    const cleaned = value.trim().replace(/ms$/i, "")
    if (!cleaned) return undefined
    const parsed = Number(cleaned)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

export const createSlotId = (): string =>
  `slot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`

export const extractString = (payload: Record<string, unknown>, keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return undefined
}

export const extractRawString = (payload: Record<string, unknown>, keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === "string") return value
  }
  return undefined
}

export const extractProgress = (payload: Record<string, unknown>): number | undefined => {
  const candidate = payload.progress_pct ?? payload.progress ?? payload.percentage
  if (typeof candidate === "number") return candidate
  if (typeof candidate === "string") {
    const parsed = Number(candidate.replace(/%$/, ""))
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

export const extractCtreeSnapshotSummary = (payload: Record<string, unknown>): CTreeSnapshotSummary | null => {
  const schemaVersion = extractString(payload, ["schema_version"])
  const nodeCount = numberOrUndefined(payload.node_count)
  const eventCount = numberOrUndefined(payload.event_count)
  const lastId = typeof payload.last_id === "string" ? payload.last_id : null
  const nodeHash = typeof payload.node_hash === "string" ? payload.node_hash : payload.node_hash === null ? null : null
  if (!schemaVersion || nodeCount === undefined || eventCount === undefined) return null
  return {
    schema_version: schemaVersion,
    node_count: nodeCount,
    event_count: eventCount,
    last_id: lastId,
    node_hash: nodeHash,
  }
}

export const normalizeTodoStatus = (value: unknown): string => {
  const raw = String(value ?? "").trim().toLowerCase()
  switch (raw) {
    case "in_progress":
    case "progress":
    case "active":
      return "in_progress"
    case "done":
    case "complete":
    case "completed":
      return "done"
    case "blocked":
      return "blocked"
    case "cancelled":
    case "canceled":
      return "canceled"
    case "todo":
    case "pending":
    default:
      return "todo"
  }
}

export const parseTodoEntry = (entry: unknown, fallbackId: string): TodoItem | null => {
  if (!isRecord(entry)) return null
  const id =
    typeof entry.id === "string"
      ? entry.id
      : typeof entry.todo_id === "string"
        ? entry.todo_id
        : typeof entry.todoId === "string"
          ? entry.todoId
          : fallbackId
  const title =
    typeof entry.title === "string"
      ? entry.title
      : typeof entry.content === "string"
        ? entry.content
        : typeof entry.text === "string"
          ? entry.text
          : ""
  if (!title.trim()) return null
  const status = normalizeTodoStatus((entry as Record<string, unknown>).status ?? (entry as Record<string, unknown>).state)
  const metadata = isRecord((entry as Record<string, unknown>).metadata)
    ? ((entry as Record<string, unknown>).metadata as Record<string, unknown>)
    : null
  const priority = (entry as Record<string, unknown>).priority ?? null
  return {
    id,
    title: title.trim(),
    status,
    priority: typeof priority === "string" || typeof priority === "number" ? priority : null,
    metadata,
  }
}

export const parseTodoList = (value: unknown): TodoItem[] | null => {
  if (!Array.isArray(value)) return null
  const parsed: TodoItem[] = []
  value.forEach((entry, index) => {
    const item = parseTodoEntry(entry, `todo-${index + 1}`)
    if (item) parsed.push(item)
  })
  return parsed.length > 0 ? parsed : null
}

export const tryParseJsonTodos = (value: unknown): TodoItem[] | null => {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null
  try {
    const parsed = JSON.parse(trimmed) as unknown
    if (Array.isArray(parsed)) return parseTodoList(parsed)
    if (isRecord(parsed)) {
      if (Array.isArray((parsed as Record<string, unknown>).todos)) return parseTodoList((parsed as Record<string, unknown>).todos)
      if ((parsed as Record<string, unknown>).todo) {
        const single = parseTodoEntry((parsed as Record<string, unknown>).todo, "todo-1")
        return single ? [single] : null
      }
    }
  } catch {
    return null
  }
  return null
}

export const extractUsageMetrics = (payload: Record<string, unknown>): UsageMetrics | null => {
  const usage = payload.usage
  const raw = isRecord(usage) ? usage : isRecord(payload) ? payload : null
  if (!raw) return null
  const promptTokens = numberOrUndefined(raw.prompt_tokens ?? raw.promptTokens)
  const completionTokens = numberOrUndefined(raw.completion_tokens ?? raw.completionTokens)
  const totalTokens = numberOrUndefined(raw.total_tokens ?? raw.totalTokens)
  const costUsd = numberOrUndefined(raw.cost_usd ?? raw.costUsd ?? raw.cost)
  const latencyMs = numberOrUndefined(raw.latency_ms ?? raw.latencyMs ?? raw.latency)
  if (promptTokens == null && completionTokens == null && totalTokens == null && costUsd == null && latencyMs == null) {
    return null
  }
  return {
    promptTokens,
    completionTokens,
    totalTokens,
    costUsd,
    latencyMs,
  }
}

export const normalizeModeValue = (value: unknown): string | null => {
  const raw = String(value ?? "").trim().toLowerCase()
  if (!raw) return null
  if (["plan", "build", "auto"].includes(raw)) return raw
  if (raw === "default") return "auto"
  return raw
}

export const normalizePermissionMode = (value: unknown): string | null => {
  const raw = String(value ?? "").trim().toLowerCase()
  if (!raw) return null
  if (["auto", "allow", "auto-accept", "auto_accept"].includes(raw)) return "auto"
  if (["prompt", "ask", "interactive"].includes(raw)) return "prompt"
  return raw
}
