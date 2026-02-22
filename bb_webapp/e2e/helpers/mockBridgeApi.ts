import type { Page, Route } from "@playwright/test"

const json = async (route: Route, body: unknown, status = 200): Promise<void> => {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

const text = async (route: Route, body: string, status = 200): Promise<void> => {
  await route.fulfill({
    status,
    contentType: "text/plain",
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
    },
    body: `${lines.join("\n")}\n`,
  })
}

export const installMockBridgeApi = async (page: Page): Promise<void> => {
  await page.route("**/*", async (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname

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
      await json(route, [])
      return
    }

    if (method === "POST" && path === "/sessions") {
      await json(route, {
        session_id: "mock-session-1",
        status: "running",
        created_at: "2026-02-22T00:00:00Z",
      })
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/files$/.test(path)) {
      await json(route, [])
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/ctrees$/.test(path)) {
      await json(route, {
        snapshot: { nodes: [] },
      })
      return
    }

    if (method === "POST" && /^\/sessions\/[^/]+\/command$/.test(path)) {
      await json(route, { status: "ok" })
      return
    }

    if (method === "POST" && /^\/sessions\/[^/]+\/input$/.test(path)) {
      await json(route, { status: "ok" })
      return
    }

    if (method === "GET" && /^\/sessions\/[^/]+\/events$/.test(path)) {
      await sse(route, [
        {
          id: "evt-bootstrap",
          type: "assistant_message",
          session_id: path.split("/")[2] ?? "mock-session-1",
          turn: 1,
          timestamp: Date.now(),
          seq: 1,
          payload: { text: "bootstrap stream event" },
        },
      ])
      return
    }

    await route.continue()
  })
}
