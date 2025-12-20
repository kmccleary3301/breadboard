import { describe, it, expect, vi, beforeEach } from "vitest"
import type { SessionEvent, SessionSummary } from "../src/api/types.js"
import { runAsk } from "../src/commands/askLogic.js"
import { runResume } from "../src/commands/resumeLogic.js"
import { mergeSessions } from "../src/commands/sessions.js"
import { formatFileList, summarizeDiffFiles } from "../src/commands/files.js"
import { buildRenderSections } from "../src/commands/render.js"
import type { SessionFileInfo } from "../src/api/types.js"

vi.mock("../src/config/appConfig.js", () => ({
  loadAppConfig: vi.fn(() => ({
    baseUrl: "http://localhost:9099",
    authToken: undefined,
    sessionCachePath: "/tmp/cache.json",
    remoteStreamDefault: false,
    requestTimeoutMs: 30000,
  })),
}))

const mocks = vi.hoisted(() => {
  const createSessionMock = vi.fn()
  const postInputMock = vi.fn()
  const getSessionMock = vi.fn()
  const rememberSessionMock = vi.fn()
  const listCachedSessionsMock = vi.fn(async () => [])
  const loadCacheMock = vi.fn(async () => ({ version: 1, sessions: {}, recent: [] }))
  return {
    createSessionMock,
    postInputMock,
    getSessionMock,
    rememberSessionMock,
    listCachedSessionsMock,
    loadCacheMock,
  }
})

vi.mock("../src/api/client.js", () => ({
  ApiClient: {
    createSession: mocks.createSessionMock,
    postInput: mocks.postInputMock,
    getSession: mocks.getSessionMock,
  },
  ApiError: class extends Error {
    status: number
    body?: unknown
    constructor(message: string, status: number, body?: unknown) {
      super(message)
      this.status = status
      this.body = body
    }
  },
}))

const streamMock = vi.hoisted(() => vi.fn())

vi.mock("../src/api/stream.js", () => ({
  streamSessionEvents: streamMock,
}))

vi.mock("../src/cache/sessionCache.js", () => ({
  rememberSession: mocks.rememberSessionMock,
  listCachedSessions: mocks.listCachedSessionsMock,
  loadSessionCache: mocks.loadCacheMock,
  getMostRecentSessionId: vi.fn(async () => "cached"),
  writeSessionCache: vi.fn(),
}))

describe("ask logic", () => {
  beforeEach(() => {
    mocks.createSessionMock.mockReset()
    streamMock.mockReset()
  })

  it("streams events and returns completion", async () => {
    mocks.createSessionMock.mockResolvedValue({ session_id: "sess-123" })
    const events: SessionEvent[] = [
      {
        id: "1",
        type: "assistant_message",
        session_id: "sess-123",
        turn: 1,
        timestamp: Date.now(),
        payload: { text: "hello" },
      },
      {
        id: "2",
        type: "completion",
        session_id: "sess-123",
        turn: 1,
        timestamp: Date.now(),
        payload: { summary: { completed: true } },
      },
    ]
    streamMock.mockReturnValue((async function* () {
      for (const event of events) {
        yield event
      }
    })())

    const yielded: SessionEvent[] = []
    const result = await runAsk(
      { prompt: "Test", configPath: "cfg.yaml" },
      (event) => void yielded.push(event),
    )

    expect(result.sessionId).toBe("sess-123")
    expect(result.completion).toEqual({ completed: true })
    expect(result.events).toHaveLength(2)
    expect(yielded.map((e) => e.type)).toEqual(["assistant_message", "completion"])
  })
})

describe("resume logic", () => {
  beforeEach(() => {
    mocks.getSessionMock.mockReset()
    streamMock.mockReset()
    mocks.rememberSessionMock.mockReset()
  })

  it("replays session events", async () => {
    mocks.getSessionMock.mockResolvedValue({
      session_id: "sess-5",
      status: "running",
      created_at: new Date().toISOString(),
      last_activity_at: new Date().toISOString(),
      completion_summary: null,
      reward_summary: null,
      logging_dir: null,
      metadata: {},
    })
    const events: SessionEvent[] = [
      {
        id: "1",
        type: "assistant_message",
        session_id: "sess-5",
        turn: 1,
        timestamp: Date.now(),
        payload: { text: "res" },
      },
      {
        id: "2",
        type: "completion",
        session_id: "sess-5",
        turn: 1,
        timestamp: Date.now(),
        payload: { summary: { completed: true } },
      },
    ]
    streamMock.mockReturnValue((async function* () {
      for (const event of events) {
        yield event
      }
    })())

    const seen: SessionEvent[] = []
    const result = await runResume({ sessionId: "sess-5" }, (event) => void seen.push(event))
    expect(mocks.rememberSessionMock).toHaveBeenCalled()
    expect(result.events).toHaveLength(2)
    expect(seen.map((e) => e.type)).toEqual(["assistant_message", "completion"])
    expect(result.completion).toEqual({ completed: true })
  })
})

describe("merge sessions", () => {
  it("combines backend and cache entries", async () => {
    mocks.listCachedSessionsMock.mockResolvedValueOnce([
      {
        sessionId: "cached",
        name: undefined,
        createdAt: "2025-01-01T00:00:00Z",
        lastActivityAt: "2025-01-02T00:00:00Z",
        status: "completed",
        model: "m",
        loggingDir: null,
        metadata: null,
      },
    ])
    const backend: SessionSummary[] = [
      {
        session_id: "backend",
        status: "running",
        created_at: "2025-02-01T00:00:00Z",
        last_activity_at: "2025-02-01T01:00:00Z",
        completion_summary: null,
        reward_summary: null,
        logging_dir: null,
        metadata: {},
      },
    ]
    const merged = await mergeSessions(backend)
    expect(merged).toHaveLength(2)
    expect(merged[0].sessionId).toBe("backend")
    expect(merged[1].sessionId).toBe("cached")
  })
})

describe("formatFileList", () => {
  it("renders table output", () => {
    const files: SessionFileInfo[] = [
      { path: "src/index.ts", type: "file", size: 1200 },
      { path: "src", type: "directory" },
    ]
    const output = formatFileList(files)
    expect(output).toContain("Type")
    expect(output).toContain("src/index.ts")
    expect(output).toContain("directory")
  })
})

describe("summarizeDiffFiles", () => {
  it("returns normalized file paths without duplicates", () => {
    const diff = `diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@\n-line1\n+line2\ndiff --git a/docs/old.txt b/docs/old.txt\n--- a/docs/old.txt\n+++ /dev/null\n`
    const files = summarizeDiffFiles(diff)
    expect(files).toEqual(["docs/old.txt", "src/app.ts"])
  })
})

describe("buildRenderSections", () => {
  it("categorizes conversation and tool events", () => {
    const base = {
      session_id: "sess-test",
      timestamp: Date.now(),
    }
    const events: SessionEvent[] = [
      { id: "1", type: "user_message", turn: 1, payload: { text: "Hi" }, ...base },
      { id: "2", type: "assistant_message", turn: 1, payload: { text: "Hello" }, ...base },
      { id: "3", type: "tool_call", turn: 1, payload: { call: { name: "run_shell" } }, ...base },
      { id: "4", type: "tool_result", turn: 1, payload: { content: "done" }, ...base },
      { id: "5", type: "completion", turn: 1, payload: { summary: { completed: true } }, ...base },
    ]
    const sections = buildRenderSections(events)
    expect(sections.conversation).toHaveLength(2)
    expect(sections.conversation[0]).toContain("USER")
    expect(sections.conversation[1]).toContain("ASSISTANT")
    expect(sections.tools).toHaveLength(3)
    expect(sections.tools[0]).toContain("TOOL CALL")
    expect(sections.tools[2]).toContain("COMPLETION")
  })
})
