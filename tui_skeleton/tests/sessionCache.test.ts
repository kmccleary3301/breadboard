import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import {
  loadSessionCache,
  writeSessionCache,
  rememberSession,
  getMostRecentSessionId,
  listCachedSessions,
} from "../src/cache/sessionCache.js"
import type { SessionSummary } from "../src/api/types.js"

let tempDir: string
let cachePath: string

const sampleSummary = (id: string): SessionSummary => ({
  session_id: id,
  status: "running",
  created_at: new Date().toISOString(),
  last_activity_at: new Date().toISOString(),
  completion_summary: null,
  reward_summary: null,
  logging_dir: null,
  metadata: { model: "test-model" },
})

beforeEach(() => {
  tempDir = mkdtempSync(path.join(tmpdir(), "kyle-cli-cache-"))
  cachePath = path.join(tempDir, "cache.json")
  process.env.BREADBOARD_SESSION_CACHE = cachePath
})

afterEach(() => {
  try {
    rmSync(tempDir, { recursive: true, force: true })
  } catch {}
  delete process.env.BREADBOARD_SESSION_CACHE
})

describe("session cache", () => {
  it("initializes empty cache", async () => {
    const cache = await loadSessionCache()
    expect(cache.sessions).toEqual({})
    expect(cache.recent).toEqual([])
  })

  it("remembers sessions and tracks recent order", async () => {
    const first = sampleSummary("sess-1")
    const second = sampleSummary("sess-2")
    await rememberSession(first)
    await rememberSession(second)
    await rememberSession(first)
    const sessions = await listCachedSessions()
    expect(sessions.length).toBe(2)
    const recent = await getMostRecentSessionId()
    expect(recent).toBe("sess-1")
  })

  it("persists cache to disk", async () => {
    const cache = { version: 1, sessions: {}, recent: [] }
    await writeSessionCache(cache)
    const loaded = await loadSessionCache()
    expect(loaded.version).toBe(1)
  })
})
