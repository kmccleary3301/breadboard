import { afterEach, describe, expect, it, vi } from "vitest"
import { mkdtemp, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { ReplSessionController } from "../controller.js"
import { rememberSession } from "../../../cache/sessionCache.js"

const previousCacheEnv = process.env.BREADBOARD_SESSION_CACHE

const withTempSessionCache = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-recent-sessions-"))
  process.env.BREADBOARD_SESSION_CACHE = path.join(dir, "sessions.json")
  return dir
}

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/misc/test_simple_native.yaml",
    workspace: ".",
  }) as any

afterEach(() => {
  if (previousCacheEnv) process.env.BREADBOARD_SESSION_CACHE = previousCacheEnv
  else delete process.env.BREADBOARD_SESSION_CACHE
})

describe("recent session re-entry helpers", () => {
  it("merges backend and cached sessions for overlay rows", async () => {
    const dir = await withTempSessionCache()
    await rememberSession({
      session_id: "cached-session",
      status: "finished",
      created_at: "2026-04-15T10:00:00.000Z",
      last_activity_at: "2026-04-15T10:05:00.000Z",
      logging_dir: null,
      metadata: { model: "cached-model" },
    } as any)

    const controller = createController()
    controller.api = () => ({
      listSessions: vi.fn(async () => [
        {
          session_id: "backend-session",
          status: "ready",
          created_at: "2026-04-15T11:00:00.000Z",
          last_activity_at: "2026-04-15T11:10:00.000Z",
          logging_dir: null,
          metadata: { model: "backend-model" },
        },
      ]),
    })

    const rows = await controller.listRecentSessions()
    expect(rows.map((row: any) => `${row.sessionId}:${row.source}`)).toEqual([
      "backend-session:backend",
      "cached-session:cache",
    ])

    await rm(dir, { recursive: true, force: true })
  })

  it("attaches to an existing session and restarts the stream loop", async () => {
    const dir = await withTempSessionCache()
    const controller = createController()
    controller.sessionId = "old-session"
    controller.conversation.push({ id: "msg-old" })
    controller.toolEvents.push({ id: "tool-old" })
    controller.rawEvents.push({ id: "raw-old" })
    controller.hints.push("old hint")

    const stop = vi.fn(async () => undefined)
    const streamLoop = vi.fn(async () => undefined)
    const emitChange = vi.fn()
    const pushHint = vi.fn()
    const resolveProviderCapabilitiesSnapshot = vi.fn()
    const getSession = vi.fn(async (sessionId: string) => ({
      session_id: sessionId,
      status: "ready",
      mode: "build",
      created_at: "2026-04-15T12:00:00.000Z",
      last_activity_at: "2026-04-15T12:10:00.000Z",
      logging_dir: null,
      metadata: { model: "reentry-model" },
    }))
    const getCtreeSnapshot = vi.fn(async () => ({ nodes: [] }))

    controller.stop = stop
    controller.streamLoop = streamLoop
    controller.emitChange = emitChange
    controller.pushHint = pushHint
    controller.resolveProviderCapabilitiesSnapshot = resolveProviderCapabilitiesSnapshot
    controller.api = () => ({
      getSession,
      getCtreeSnapshot,
    })

    const attached = await controller.attachExistingSession("target-session")

    expect(attached).toBe(true)
    expect(stop).toHaveBeenCalled()
    expect(getSession).toHaveBeenCalledWith("target-session")
    expect(resolveProviderCapabilitiesSnapshot).toHaveBeenCalledWith("reentry-model")
    expect(controller.sessionId).toBe("target-session")
    expect(controller.conversation).toEqual([])
    expect(controller.toolEvents).toEqual([])
    expect(controller.rawEvents).toEqual([])
    expect(controller.status).toBe("Attached to target-session")
    expect(streamLoop).toHaveBeenCalled()
    expect(pushHint).toHaveBeenCalledWith("Attached to session target-session.")

    await rm(dir, { recursive: true, force: true })
  })

  it("starts a new session through slash command without retaining old transcript state", async () => {
    const controller = createController()
    controller.sessionId = "old-session"
    controller.conversation.push({ id: "msg-old" })
    controller.toolEvents.push({ id: "tool-old" })
    controller.rawEvents.push({ id: "raw-old" })

    const stop = vi.fn(async () => undefined)
    const start = vi.fn(async function (this: any) {
      this.conversation.length = 0
      this.toolEvents.length = 0
      this.rawEvents.length = 0
      this.sessionId = "new-session"
      this.status = "Ready"
      this.emitChange()
    })
    const emitChange = vi.fn()
    const pushHint = vi.fn()

    controller.stop = stop
    controller.start = start
    controller.emitChange = emitChange
    controller.pushHint = pushHint

    await controller.slashHandlers().new([])

    expect(stop).toHaveBeenCalled()
    expect(start).toHaveBeenCalled()
    expect(controller.sessionId).toBe("new-session")
    expect(controller.conversation).toEqual([])
    expect(controller.toolEvents).toEqual([])
    expect(controller.rawEvents).toEqual([])
    expect(pushHint).toHaveBeenCalledWith("Starting new session…")
    expect(pushHint).toHaveBeenCalledWith("Started new session new-session.")
  })
})
