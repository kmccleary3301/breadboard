import test from "node:test"
import assert from "node:assert/strict"
import { HostController, type ConnectionState, type StreamEventEnvelope } from "../hostController"

type ContextLike = {
  secrets: { get: (key: string) => Promise<string | undefined> }
  globalState: { get: <T>(key: string) => T | undefined; update: (key: string, value: unknown) => Promise<void> }
}

const makeContext = (): ContextLike => {
  const store = new Map<string, unknown>()
  return {
    secrets: {
      get: async (_key: string) => undefined,
    },
    globalState: {
      get: <T>(key: string) => store.get(key) as T | undefined,
      update: async (key: string, value: unknown) => {
        store.set(key, value)
      },
    },
  }
}

const jsonResponse = (value: unknown, status = 200): Response =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  })

const sseFromEvents = (
  events: Array<{ id: string; data: Record<string, unknown> }>,
  options: { keepOpen?: boolean; splitAt?: number } = {},
): Response => {
  const { keepOpen = false, splitAt } = options
  const payload = events.map((evt) => `id: ${evt.id}\ndata: ${JSON.stringify(evt.data)}\n\n`).join("")
  const encoder = new TextEncoder()
  const chunks =
    typeof splitAt === "number" && splitAt > 0 && splitAt < payload.length
      ? [payload.slice(0, splitAt), payload.slice(splitAt)]
      : [payload]
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      if (!keepOpen) controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  })
}

const waitFor = async (predicate: () => boolean, timeoutMs = 1500): Promise<void> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (predicate()) return
    await new Promise<void>((resolve) => setTimeout(resolve, 10))
  }
  throw new Error("timeout waiting for condition")
}

const headerValue = (init: RequestInit | undefined, key: string): string | null => {
  const headers = new Headers(init?.headers ?? {})
  return headers.get(key)
}

test("host stream reconnects and resumes with Last-Event-ID", async () => {
  const ctx = makeContext()
  const seenIds: string[] = []
  const fetchCalls: Array<{ url: string; init: RequestInit | undefined }> = []
  let eventCall = 0

  const controller = new HostController({
    fetchFn: async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      const url = String(input)
      fetchCalls.push({ url, init })
      if (url.endsWith("/sessions")) return jsonResponse([])
      if (url.includes("/events")) {
        eventCall += 1
        if (eventCall === 1) {
          assert.equal(headerValue(init, "Last-Event-ID"), null)
          return sseFromEvents([
            {
              id: "e1",
              data: {
                id: "e1",
                type: "assistant.message.delta",
                session_id: "s1",
                payload: { text: "one" },
              },
            },
          ])
        }
        if (eventCall === 2) {
          assert.equal(headerValue(init, "Last-Event-ID"), "e1")
          return sseFromEvents(
            [
              {
                id: "e2",
                data: {
                  id: "e2",
                  type: "assistant.message.end",
                  session_id: "s1",
                  payload: { text: "two" },
                },
              },
            ],
            { keepOpen: true },
          )
        }
      }
      throw new Error(`unexpected fetch: ${url}`)
    },
    batchDelayMs: 1,
    retryBackoffMs: [1, 2, 5],
  })

  controller.setSink({
    onEvents: async ({ events }) => {
      for (const event of events) seenIds.push(event.id)
    },
  })

  await controller.attachToSession(ctx as never, "s1")
  await waitFor(() => seenIds.includes("e1") && seenIds.includes("e2") && eventCall >= 2)
  controller.stopAllStreams()
  assert.deepEqual(seenIds.slice(0, 2), ["e1", "e2"])
})

test("host emits continuity gap on 409 replay miss", async () => {
  const ctx = makeContext()
  const connections: ConnectionState[] = []

  const controller = new HostController({
    fetchFn: async (input: Parameters<typeof fetch>[0]) => {
      const url = String(input)
      if (url.endsWith("/sessions")) return jsonResponse([])
      if (url.includes("/events")) return new Response("", { status: 409 })
      throw new Error(`unexpected fetch: ${url}`)
    },
    batchDelayMs: 1,
    retryBackoffMs: [1, 2],
  })
  controller.setSink({
    onConnection: async (state) => {
      connections.push(state)
    },
  })

  await controller.attachToSession(ctx as never, "s1")
  await waitFor(() => connections.some((state) => state.status === "error" && state.gapDetected === true))
  controller.stopAllStreams()
})

test("host preserves event ordering across split SSE chunks", async () => {
  const ctx = makeContext()
  const seen: string[] = []

  const controller = new HostController({
    fetchFn: async (input: Parameters<typeof fetch>[0]) => {
      const url = String(input)
      if (url.endsWith("/sessions")) return jsonResponse([])
      if (url.includes("/events")) {
        return sseFromEvents(
          [
            { id: "e1", data: { id: "e1", type: "assistant.message.delta", session_id: "s1", payload: { text: "a" } } },
            { id: "e2", data: { id: "e2", type: "tool_call", session_id: "s1", payload: { tool: "apply_patch" } } },
            { id: "e3", data: { id: "e3", type: "tool.result", session_id: "s1", payload: { status: "ok" } } },
          ],
          { keepOpen: true, splitAt: 80 },
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    },
    batchDelayMs: 1,
    retryBackoffMs: [1, 2],
  })
  controller.setSink({
    onEvents: async ({ events }) => {
      for (const event of events) seen.push(event.id)
    },
  })

  await controller.attachToSession(ctx as never, "s1")
  await waitFor(() => seen.length >= 3)
  controller.stopAllStreams()
  assert.deepEqual(seen.slice(0, 3), ["e1", "e2", "e3"])
})

test("host suppresses replay-overlap duplicate event IDs", async () => {
  const ctx = makeContext()
  const seen: string[] = []
  let eventCall = 0

  const controller = new HostController({
    fetchFn: async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/sessions")) return jsonResponse([])
      if (url.includes("/events")) {
        eventCall += 1
        if (eventCall === 1) {
          return sseFromEvents([
            { id: "e1", data: { id: "e1", type: "assistant.message.delta", session_id: "s1", payload: { text: "one" } } },
            { id: "e2", data: { id: "e2", type: "assistant.message.delta", session_id: "s1", payload: { text: "two" } } },
          ])
        }
        if (eventCall === 2) {
          assert.equal(headerValue(init, "Last-Event-ID"), "e2")
          return sseFromEvents(
            [
              { id: "e2", data: { id: "e2", type: "assistant.message.delta", session_id: "s1", payload: { text: "dup" } } },
              { id: "e3", data: { id: "e3", type: "assistant.message.end", session_id: "s1", payload: { text: "three" } } },
            ],
            { keepOpen: true },
          )
        }
      }
      throw new Error(`unexpected fetch: ${url}`)
    },
    batchDelayMs: 1,
    retryBackoffMs: [1, 2],
  })
  controller.setSink({
    onEvents: async ({ events }) => {
      for (const event of events) seen.push(event.id)
    },
  })

  await controller.attachToSession(ctx as never, "s1")
  await waitFor(() => seen.includes("e3"))
  controller.stopAllStreams()
  assert.deepEqual(seen, ["e1", "e2", "e3"])
})

test("host operator-path integration: create->attach->prompt->permission->files->diff->stop", async () => {
  const ctx = makeContext()
  const seen: StreamEventEnvelope[] = []
  const commandBodies: Array<Record<string, unknown>> = []
  const openedCommands: string[] = []
  let eventCall = 0

  const runtime = {
    workspace: {
      workspaceFolders: [{ uri: { kind: "workspace" } }],
      openTextDocument: async (_arg: { content: string; language?: string }) => ({ uri: { kind: "memdoc" } }),
    },
    Uri: {
      joinPath: (_base: unknown, path: string) => ({ kind: "joined", path }),
      file: (path: string) => ({ kind: "file", path }),
    },
    commands: {
      executeCommand: async (command: string) => {
        openedCommands.push(command)
      },
    },
  }

  const controller = new HostController({
    fetchFn: async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      const url = String(input)
      const parsed = new URL(url)
      if (parsed.pathname === "/sessions" && (init?.method ?? "GET") === "GET") return jsonResponse([{ session_id: "s1", status: "running" }])
      if (parsed.pathname === "/sessions" && init?.method === "POST") return jsonResponse({ session_id: "s1", status: "starting" }, 201)
      if (parsed.pathname === "/sessions/s1/input") return jsonResponse({ status: "accepted" }, 202)
      if (parsed.pathname === "/sessions/s1/command") {
        const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>
        commandBodies.push(body)
        return jsonResponse({ status: "accepted" }, 202)
      }
      if (parsed.pathname === "/sessions/s1/files" && parsed.searchParams.get("path") === "." && !parsed.searchParams.get("mode")) {
        return jsonResponse([{ path: "main.c", type: "file", size: 12 }])
      }
      if (
        parsed.pathname === "/sessions/s1/files" &&
        parsed.searchParams.get("path") === "main.c" &&
        parsed.searchParams.get("mode") === "snippet"
      ) {
        return jsonResponse({ path: "main.c", content: "int main(){}", truncated: false, total_bytes: 12 })
      }
      if (parsed.pathname === "/sessions/s1/download" && parsed.searchParams.get("artifact") === "art/main.c.before") {
        return new Response("int main(){return 0;}", { status: 200 })
      }
      if (parsed.pathname === "/sessions/s1/events") {
        eventCall += 1
        if (eventCall === 1) {
          return sseFromEvents(
            [
              { id: "e1", data: { id: "e1", type: "permission_request", session_id: "s1", payload: { request_id: "p1", summary: "allow?" } } },
              { id: "e2", data: { id: "e2", type: "permission_response", session_id: "s1", payload: { request_id: "p1", decision: "allow_once" } } },
            ],
            { keepOpen: true },
          )
        }
      }
      throw new Error(`unexpected fetch: ${url}`)
    },
    batchDelayMs: 1,
    retryBackoffMs: [1, 2],
    runtimeProvider: () => runtime,
    configProvider: () => ({ engineBaseUrl: "http://127.0.0.1:9099", defaultConfigPath: "agent_configs/base_v2.yaml" }),
  })

  controller.setSink({
    onEvents: async ({ events }) => {
      seen.push(...events)
    },
  })

  const created = await controller.createSession(ctx as never, "implement hello")
  assert.equal(created.session_id, "s1")
  await controller.attachToSession(ctx as never, "s1")
  await waitFor(() => seen.length >= 2)
  await controller.sendInput(ctx as never, "s1", "continue")
  await controller.sendCommand(ctx as never, "s1", "permission_decision", {
    request_id: "p1",
    decision: "allow_once",
  })
  const files = await controller.listFiles(ctx as never, "s1", ".")
  assert.equal(files[0].path, "main.c")
  const snippet = await controller.readFileSnippet(ctx as never, "s1", "main.c")
  assert.equal(snippet.path, "main.c")
  await controller.openDiff(ctx as never, "s1", "main.c", "art/main.c.before")
  await controller.sendCommand(ctx as never, "s1", "stop")
  controller.stopAllStreams()

  assert.ok(commandBodies.some((body) => body.command === "permission_decision"))
  assert.ok(commandBodies.some((body) => body.command === "stop"))
  assert.ok(openedCommands.includes("vscode.diff"))
})
