const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null

const asString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value.trim() : null

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

export type AttachSessionParams = { sessionId: string }
export type SendMessageParams = { sessionId?: string; text: string }
export type StopSessionParams = { sessionId?: string }
export type DeleteSessionParams = { sessionId: string }
export type ListFilesParams = { sessionId: string; path?: string }
export type ReadSnippetParams = {
  sessionId: string
  path: string
  headLines?: number
  tailLines?: number
  maxBytes?: number
}
export type OpenDiffParams = { sessionId: string; filePath: string; artifactPath?: string }
export type ApprovePermissionParams = { sessionId: string; requestId: string; decision: "allow_once" | "deny" | "allow_rule" }

export const parseAttachSessionParams = (value: unknown): AttachSessionParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  return sessionId ? { sessionId } : null
}

export const parseSendMessageParams = (value: unknown): SendMessageParams | null => {
  const rec = asRecord(value)
  const text = asString(rec?.text)
  if (!text) return null
  const sessionId = asString(rec?.sessionId) ?? undefined
  return { ...(sessionId ? { sessionId } : {}), text }
}

export const parseStopSessionParams = (value: unknown): StopSessionParams => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId) ?? undefined
  return sessionId ? { sessionId } : {}
}

export const parseDeleteSessionParams = (value: unknown): DeleteSessionParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  return sessionId ? { sessionId } : null
}

export const parseListFilesParams = (value: unknown): ListFilesParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  if (!sessionId) return null
  const path = asString(rec?.path) ?? undefined
  return { sessionId, ...(path ? { path } : {}) }
}

export const parseReadSnippetParams = (value: unknown): ReadSnippetParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  const path = asString(rec?.path)
  if (!sessionId || !path) return null
  const headLines = asNumber(rec?.headLines) ?? undefined
  const tailLines = asNumber(rec?.tailLines) ?? undefined
  const maxBytes = asNumber(rec?.maxBytes) ?? undefined
  return {
    sessionId,
    path,
    ...(headLines !== undefined ? { headLines } : {}),
    ...(tailLines !== undefined ? { tailLines } : {}),
    ...(maxBytes !== undefined ? { maxBytes } : {}),
  }
}

export const parseOpenDiffParams = (value: unknown): OpenDiffParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  const filePath = asString(rec?.filePath)
  if (!sessionId || !filePath) return null
  const artifactPath = asString(rec?.artifactPath) ?? undefined
  return {
    sessionId,
    filePath,
    ...(artifactPath ? { artifactPath } : {}),
  }
}

export const parseApprovePermissionParams = (value: unknown): ApprovePermissionParams | null => {
  const rec = asRecord(value)
  const sessionId = asString(rec?.sessionId)
  const requestId = asString(rec?.requestId)
  const decisionRaw = asString(rec?.decision)
  if (!sessionId || !requestId || !decisionRaw) return null
  if (!["allow_once", "deny", "allow_rule"].includes(decisionRaw)) return null
  return { sessionId, requestId, decision: decisionRaw as ApprovePermissionParams["decision"] }
}
