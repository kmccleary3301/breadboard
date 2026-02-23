import type { Page, Route } from "@playwright/test"

type SessionProfile = "standard" | "gap"

type SessionState = {
  sessionId: string
  status: string
  createdAt: string
  lastActivityAt: string
  profile: SessionProfile
  streamCalls: number
  pendingRestoreEvent: boolean
}

const json = async (route: Route, body: unknown, status = 200): Promise<void> => {
  await route.fulfill({
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
      "access-control-allow-headers": "content-type,authorization,last-event-id",
    },
    body: JSON.stringify(body),
  })
}

const text = async (route: Route, body: string, status = 200): Promise<void> => {
  await route.fulfill({
    status,
    headers: {
      "content-type": "text/plain",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
      "access-control-allow-headers": "content-type,authorization,last-event-id",
    },
    body,
  })
}

const sse = async (route: Route, events: unknown[]): Promise<void> => {
  const lines: string[] = []
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index]
    lines.push(`id: bootstrap-${index + 1}`)
    lines.push("event: message")
    lines.push(`data: ${JSON.stringify(event)}`)
    lines.push("")
  }
  await route.fulfill({
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
      "access-control-allow-headers": "content-type,authorization,last-event-id",
    },
    body: `${lines.join("\n")}\n`,
  })
}

const nowIso = (): string => new Date().toISOString()

const buildStandardSessionEvents = (sessionId: string, includeRestoreEvent: boolean): unknown[] => {
  const events: unknown[] = [
    {
      id: `${sessionId}-evt-001`,
      type: "assistant_message",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now(),
      seq: 1,
      payload: { text: "bootstrap stream event" },
    },
    {
      id: `${sessionId}-evt-002`,
      type: "checkpoint_list",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 1,
      seq: 2,
      payload: {
        checkpoints: [
          {
            checkpoint_id: "cp-001",
            label: "Bootstrap checkpoint",
            created_at_ms: Date.now(),
            summary: "Initial mocked checkpoint",
          },
        ],
      },
    },
    {
      id: `${sessionId}-evt-003`,
      type: "permission_request",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 2,
      seq: 3,
      payload: {
        request_id: "perm-live-1",
        tool: "run_command",
        kind: "shell",
        summary: "Run CI test command",
        rule_suggestion: "^npm run test$",
        default_scope: "project",
      },
    },
    {
      id: `${sessionId}-evt-004`,
      type: "task_event",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 3,
      seq: 4,
      payload: {
        task_id: "task-live-1",
        title: "Verify release readiness",
        status: "running",
      },
    },
    {
      id: `${sessionId}-evt-005`,
      type: "tool_call",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 4,
      seq: 5,
      payload: {
        tool_name: "read_file",
        path: "README.md",
      },
    },
    {
      id: `${sessionId}-evt-006`,
      type: "tool.result",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 5,
      seq: 6,
      payload: {
        tool_name: "write_file",
        path: "docs/plan.md",
        diff: "--- a/docs/plan.md\n+++ b/docs/plan.md\n@@ -1 +1 @@\n-old plan\n+new release plan",
      },
    },
    {
      id: `${sessionId}-evt-007`,
      type: "assistant_message",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 6,
      seq: 7,
      payload: { text: "Ready for the next operation." },
    },
  ]

  if (includeRestoreEvent) {
    events.push({
      id: `${sessionId}-evt-008`,
      type: "checkpoint_restored",
      session_id: sessionId,
      turn: 1,
      timestamp: Date.now() + 7,
      seq: 8,
      payload: {
        checkpoint_id: "cp-001",
        status: "ok",
        message: "checkpoint restored: cp-001",
      },
    })
  }

  return events
}

const extractSessionId = (path: string): string | null => {
  const match = /^\/sessions\/([^/]+)/.exec(path)
  return match?.[1] ?? null
}

export const installMockBridgeApi = async (page: Page): Promise<void> => {
  const sessions = new Map<string, SessionState>()
  let sessionCounter = 0

  const ensureSession = (sessionId: string, profile: SessionProfile = "standard"): SessionState => {
    const existing = sessions.get(sessionId)
    if (existing) return existing
    const created: SessionState = {
      sessionId,
      status: "running",
      createdAt: nowIso(),
      lastActivityAt: nowIso(),
      profile,
      streamCalls: 0,
      pendingRestoreEvent: false,
    }
    sessions.set(sessionId, created)
    return created
  }

  await page.route("**/*", async (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname
    const sessionId = extractSessionId(path)
    const session = sessionId ? ensureSession(sessionId) : null

    if (method === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
          "access-control-allow-headers": "content-type,authorization,last-event-id",
          "access-control-max-age": "86400",
        },
      })
      return
    }

    if (method === "GET" && path === "/health") {
      await json(route, {
        status: "ok",
        protocol_version: "v1",
        engine_version: "test-engine",
      })
      return
    }

    if (method === "GET" && path === "/status") {
      await text(route, "ok")
      return
    }

    if (method === "GET" && path === "/models") {
      await json(route, {
        models: [{ id: "mock-model" }],
        default_model: "mock-model",
      })
      return
    }

    if (method === "GET" && path === "/sessions") {
      const rows = [...sessions.values()].map((row) => ({
        session_id: row.sessionId,
        status: row.status,
        created_at: row.createdAt,
        last_activity_at: row.lastActivityAt,
      }))
      await json(route, rows)
      return
    }

    if (method === "POST" && path === "/sessions") {
      const body = (request.postDataJSON?.() ?? {}) as { task?: string }
      const task = String(body.task ?? "")
      const profile: SessionProfile = /\bgap\b/i.test(task) ? "gap" : "standard"
      sessionCounter += 1
      const created = ensureSession(`mock-session-${sessionCounter}`, profile)
      await json(route, {
        session_id: created.sessionId,
        status: created.status,
        created_at: created.createdAt,
      })
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/files$/.test(path)) {
      const mode = url.searchParams.get("mode")
      const filePath = url.searchParams.get("path") ?? ""
      if (mode) {
        const contentMap: Record<string, string> = {
          "README.md": "README snippet content",
          "src/main.tsx": "export const boot = () => 'ok'\n",
        }
        await json(route, {
          path: filePath,
          content: contentMap[filePath] ?? `snippet for ${filePath}`,
          truncated: false,
        })
        return
      }

      if (filePath === "src") {
        await json(route, [{ path: "src/main.tsx", type: "file", size: 128 }])
        return
      }

      await json(route, [
        { path: "README.md", type: "file", size: 64 },
        { path: "src", type: "directory" },
      ])
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/ctrees$/.test(path)) {
      await json(route, {
        snapshot: {
          nodes: [
            {
              id: "task-live-1",
              title: "Verify release readiness",
              status: "running",
            },
          ],
        },
      })
      return
    }

    if (method === "POST" && /^\/sessions\/[^/]+\/command$/.test(path) && session) {
      const body = (request.postDataJSON?.() ?? {}) as { command?: string }
      if (body.command === "restore_checkpoint") {
        session.pendingRestoreEvent = true
      }
      await json(route, { status: "ok" })
      return
    }

    if (method === "POST" && /^\/sessions\/[^/]+\/input$/.test(path)) {
      await json(route, { status: "ok" })
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/download$/.test(path)) {
      await route.fulfill({
        status: 200,
        headers: {
          "content-type": "application/octet-stream",
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
          "access-control-allow-headers": "content-type,authorization,last-event-id",
        },
        body: `mock artifact bytes for ${url.searchParams.get("artifact") ?? "artifact"}`,
      })
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/events$/.test(path) && sessionId && session) {
      const replay = url.searchParams.get("replay") === "true"
      const fromId = url.searchParams.get("from_id")
      const lastEventId = await request.headerValue("last-event-id")
      session.streamCalls += 1

      if (session.profile === "gap") {
        if (session.streamCalls === 1 && !replay && !fromId && !lastEventId) {
          await sse(route, [
            {
              id: `${sessionId}-gap-1`,
              type: "assistant_message",
              session_id: sessionId,
              turn: 1,
              timestamp: Date.now(),
              seq: 1,
              payload: { text: "gap bootstrap event" },
            },
            {
              id: `${sessionId}-gap-3`,
              type: "assistant_message",
              session_id: sessionId,
              turn: 1,
              timestamp: Date.now() + 1,
              seq: 3,
              payload: { text: "gap event with skipped seq" },
            },
          ])
          return
        }
        if (replay) {
          await sse(route, [
            {
              id: `${sessionId}-gap-replay-3`,
              type: "assistant_message",
              session_id: sessionId,
              turn: 1,
              timestamp: Date.now() + 2,
              seq: 3,
              payload: { text: "gap replay still out of sequence" },
            },
          ])
          return
        }
      }

      if (lastEventId || fromId) {
        await sse(route, [])
        return
      }

      const events = buildStandardSessionEvents(sessionId, session.pendingRestoreEvent)
      session.pendingRestoreEvent = false
      await sse(route, events)
      return
    }

    await route.continue()
  })
}
