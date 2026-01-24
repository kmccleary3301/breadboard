import { promises as fs } from "node:fs"
import path from "node:path"
import { loadAppConfig } from "../config/appConfig.js"
import type { SessionSummary } from "../api/types.js"

const CACHE_VERSION = 1
const MAX_RECENT = 50

export interface CachedSessionEntry {
  readonly sessionId: string
  readonly name?: string
  readonly createdAt: string
  readonly lastActivityAt: string
  readonly status: string
  readonly model?: string
  readonly loggingDir?: string | null
  readonly metadata?: Record<string, unknown> | null
  readonly draft?: DraftState | null
}

export interface SessionCache {
  version: number
  sessions: Record<string, CachedSessionEntry>
  recent: string[]
}

export interface DraftState {
  readonly text: string
  readonly cursor: number
  readonly updatedAt: string
}

const emptyCache = (): SessionCache => ({ version: CACHE_VERSION, sessions: {}, recent: [] })

const ensureDir = async (filePath: string): Promise<void> => {
  await fs.mkdir(path.dirname(filePath), { recursive: true })
}

const readJson = async (filePath: string): Promise<SessionCache> => {
  try {
    const raw = await fs.readFile(filePath, "utf8")
    const parsed = JSON.parse(raw) as SessionCache
    if (typeof parsed.version !== "number" || !parsed.sessions || !parsed.recent) {
      return emptyCache()
    }
    return parsed
  } catch {
    return emptyCache()
  }
}

export const loadSessionCache = async (): Promise<SessionCache> => {
  const { sessionCachePath } = loadAppConfig()
  return readJson(sessionCachePath)
}

export const writeSessionCache = async (cache: SessionCache): Promise<void> => {
  const { sessionCachePath } = loadAppConfig()
  await ensureDir(sessionCachePath)
  await fs.writeFile(sessionCachePath, JSON.stringify(cache, null, 2), "utf8")
}

const normalizeMetadata = (value: unknown): Record<string, unknown> | null => {
  if (value && typeof value === "object") {
    return value as Record<string, unknown>
  }
  return null
}

const ensureSessionEntry = (cache: SessionCache, sessionId: string): CachedSessionEntry => {
  const existing = cache.sessions[sessionId]
  if (existing) return existing
  const now = new Date().toISOString()
  const entry: CachedSessionEntry = {
    sessionId,
    createdAt: now,
    lastActivityAt: now,
    status: "unknown",
  }
  cache.sessions[sessionId] = entry
  cache.recent = [sessionId, ...cache.recent.filter((id) => id !== sessionId)].slice(0, MAX_RECENT)
  return entry
}

export const rememberSession = async (
  summary: SessionSummary,
  options: { name?: string; model?: string } = {},
): Promise<void> => {
  const cache = await loadSessionCache()
  const entry: CachedSessionEntry = {
    sessionId: summary.session_id,
    name: options.name,
    createdAt: summary.created_at,
    lastActivityAt: summary.last_activity_at,
    status: summary.status,
    model: options.model ?? (summary.metadata?.model as string | undefined),
    loggingDir: summary.logging_dir ?? null,
    metadata: normalizeMetadata(summary.metadata ?? null),
    draft: cache.sessions[summary.session_id]?.draft ?? null,
  }
  cache.sessions[entry.sessionId] = entry
  cache.recent = [entry.sessionId, ...cache.recent.filter((id) => id !== entry.sessionId)].slice(0, MAX_RECENT)
  await writeSessionCache(cache)
}

export const listCachedSessions = async (): Promise<CachedSessionEntry[]> => {
  const cache = await loadSessionCache()
  return Object.values(cache.sessions).sort((a, b) => (a.lastActivityAt > b.lastActivityAt ? -1 : 1))
}

export const forgetSession = async (sessionId: string): Promise<void> => {
  const cache = await loadSessionCache()
  if (!(sessionId in cache.sessions)) {
    return
  }
  delete cache.sessions[sessionId]
  cache.recent = cache.recent.filter((id) => id !== sessionId)
  await writeSessionCache(cache)
}

export const getMostRecentSessionId = async (): Promise<string | null> => {
  const cache = await loadSessionCache()
  return cache.recent.length > 0 ? cache.recent[0] : null
}

export const getSessionDraft = async (sessionId: string): Promise<DraftState | null> => {
  const cache = await loadSessionCache()
  return cache.sessions[sessionId]?.draft ?? null
}

export const updateSessionDraft = async (sessionId: string, draft: DraftState | null): Promise<void> => {
  const cache = await loadSessionCache()
  const entry = ensureSessionEntry(cache, sessionId)
  const current = entry.draft ?? null
  const nextDraft = draft ?? null
  if (
    current &&
    nextDraft &&
    current.text === nextDraft.text &&
    current.cursor === nextDraft.cursor &&
    current.updatedAt === nextDraft.updatedAt
  ) {
    return
  }
  cache.sessions[sessionId] = { ...entry, draft: nextDraft }
  await writeSessionCache(cache)
}
