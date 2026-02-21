export type SessionNavCommand = "session_child_next" | "session_child_previous" | "session_parent"

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const extractString = (detail: Record<string, unknown>, keys: readonly string[]): string | null => {
  for (const key of keys) {
    const raw = detail[key]
    if (typeof raw === "string" && raw.trim()) return raw.trim()
  }
  return null
}

export const resolveCommandTargetSessionId = (
  command: SessionNavCommand,
  detail: Record<string, unknown> | null | undefined,
): string | null => {
  if (!isRecord(detail)) return null
  if (typeof detail.switched === "boolean" && !detail.switched) return null

  const shared = ["target_session_id", "targetSessionId", "active_session_id", "activeSessionId"] as const
  const parentPreferred = ["parent_session_id", "parentSessionId", "session_id", "sessionId", "child_session_id", "childSessionId"] as const
  const childPreferred = ["child_session_id", "childSessionId", "session_id", "sessionId", "parent_session_id", "parentSessionId"] as const

  const keys = command === "session_parent" ? [...shared, ...parentPreferred] : [...shared, ...childPreferred]
  return extractString(detail, keys)
}

