import { afterEach, describe, expect, it, vi } from "vitest"
import { openEventStream, streamSessionEvents } from "../stream.js"

const originalToken = process.env.BREADBOARD_API_TOKEN

const sseResponse = () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(
          'id: evt-1\ndata: {"type":"assistant_message","payload":{"text":"ok"}}\n\n',
        ),
      )
      controller.close()
    },
  })
  return new Response(body, { status: 200 })
}

afterEach(() => {
  if (originalToken === undefined) {
    delete process.env.BREADBOARD_API_TOKEN
  } else {
    process.env.BREADBOARD_API_TOKEN = originalToken
  }
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

class MockEventSource extends EventTarget {
  static instances: MockEventSource[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  readonly url: string
  closed = false

  constructor(url: string | URL) {
    super()
    this.url = String(url)
    MockEventSource.instances.push(this)
  }

  close() {
    this.closed = true
  }

  emitOpen() {
    this.onopen?.(new Event("open"))
  }

  emitMessage(data: string, lastEventId = "") {
    this.onmessage?.(new MessageEvent("message", { data, lastEventId }))
  }

  emitError() {
    this.onerror?.(new Event("error"))
  }
}

describe("streamSessionEvents auth", () => {
  it("resolves environment auth tokens at stream request time", async () => {
    process.env.BREADBOARD_API_TOKEN = "stream-env-token"
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse())

    const events = []
    for await (const event of streamSessionEvents("session-1", { config: { baseUrl: "http://127.0.0.1:9099" } })) {
      events.push(event)
    }

    expect(events).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer stream-env-token" })
  })

  it("supports async auth token providers without making stream construction awaitable", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse())
    const stream = streamSessionEvents("session-1", {
      config: {
        baseUrl: "http://127.0.0.1:9099",
        authToken: async () => "stream-provider-token",
      },
    })

    expect(Symbol.asyncIterator in Object(stream)).toBe(true)
    const first = await stream.next()

    expect(first.done).toBe(false)
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({ Authorization: "Bearer stream-provider-token" })

    await stream.return?.()
  })

  it("does not resolve auth or fetch when constructed with a pre-aborted signal", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse())
    const authToken = vi.fn(async () => "late-token")
    const controller = new AbortController()
    controller.abort()

    const stream = streamSessionEvents("session-1", {
      signal: controller.signal,
      config: {
        baseUrl: "http://127.0.0.1:9099",
        authToken,
      },
    })
    const first = await stream.next()

    expect(first.done).toBe(true)
    expect(authToken).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("stops waiting for async auth when aborted before auth resolves", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse())
    let resolveToken: (value: string) => void = () => undefined
    const authToken = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolveToken = resolve
        }),
    )
    const controller = new AbortController()
    const stream = streamSessionEvents("session-1", {
      signal: controller.signal,
      config: {
        baseUrl: "http://127.0.0.1:9099",
        authToken,
      },
    })

    const first = stream.next()
    await Promise.resolve()
    controller.abort()

    await expect(first).resolves.toMatchObject({ done: true })
    expect(authToken).toHaveBeenCalledOnce()
    expect(fetchMock).not.toHaveBeenCalled()
    resolveToken("late-token")
  })

  it("does not fetch when async auth provider throws synchronously", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse())
    const authToken = vi.fn(() => {
      throw new Error("sync auth failure")
    })
    const stream = streamSessionEvents("session-1", {
      config: {
        baseUrl: "http://127.0.0.1:9099",
        authToken,
      },
    })

    await expect(stream.next()).rejects.toThrow("sync auth failure")
    expect(authToken).toHaveBeenCalledOnce()
    expect(fetchMock).not.toHaveBeenCalled()
  })


  it("finishes cleanly when aborted during a pending fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"))
          })
        }),
    )
    const controller = new AbortController()
    const stream = streamSessionEvents("session-1", {
      signal: controller.signal,
      config: { baseUrl: "http://127.0.0.1:9099", authToken: "stream-token" },
    })

    const first = stream.next()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    controller.abort()

    await expect(first).resolves.toMatchObject({ done: true })
  })
  it("finishes cleanly when aborted during a pending stream read", async () => {
    let bodyController: ReadableStreamDefaultController<Uint8Array> | undefined
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        bodyController = controller
        controller.enqueue(
          new TextEncoder().encode(
            'id: evt-1\ndata: {"type":"assistant_message","payload":{"text":"ok"}}\n\n',
          ),
        )
      },
    })
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      init?.signal?.addEventListener("abort", () => {
        bodyController?.error(new DOMException("Aborted", "AbortError"))
      })
      return new Response(body, { status: 200 })
    })
    const controller = new AbortController()
    const stream = streamSessionEvents("session-1", {
      signal: controller.signal,
      config: { baseUrl: "http://127.0.0.1:9099", authToken: "stream-token" },
    })

    const first = await stream.next()
    const second = stream.next()
    await Promise.resolve()
    controller.abort()

    expect(first.done).toBe(false)
    await expect(second).resolves.toMatchObject({ done: true })
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})

describe("openEventStream", () => {
  it("opens versioned SSE URLs and normalizes EventSource messages", () => {
    MockEventSource.instances = []
    vi.stubGlobal("EventSource", MockEventSource)
    const events: unknown[] = []
    const opened: string[] = []

    const handle = openEventStream(
      "session-1",
      {
        onOpen: () => opened.push("open"),
        onEvent: (event) => events.push(event),
      },
      {
        config: { baseUrl: "http://127.0.0.1:9099" },
        lastEventId: "evt-0",
      },
    )

    const instance = MockEventSource.instances[0]
    expect(instance.url).toBe("http://127.0.0.1:9099/v1/sessions/session-1/events?schema=2&include_legacy=false&from_id=evt-0")
    instance.emitOpen()
    instance.emitMessage('{"type":"assistant_message","payload":{"text":"ok"}}', "evt-1")

    expect(opened).toEqual(["open"])
    expect(events).toMatchObject([
      {
        id: "evt-1",
        type: "assistant_message",
        session_id: "session-1",
        payload: { text: "ok" },
      },
    ])

    handle.close()
    expect(instance.closed).toBe(true)
  })

  it("backs off and reconnects after EventSource errors", async () => {
    vi.useFakeTimers()
    MockEventSource.instances = []
    vi.stubGlobal("EventSource", MockEventSource)
    const errors: unknown[] = []

    const handle = openEventStream(
      "session-2",
      {
        onEvent: () => undefined,
        onError: (error) => errors.push(error),
      },
      {
        config: { baseUrl: "http://127.0.0.1:9099" },
        initialRetryMs: 25,
        maxRetryMs: 50,
      },
    )

    MockEventSource.instances[0].emitError()
    expect(MockEventSource.instances[0].closed).toBe(true)
    expect(errors).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(25)
    expect(MockEventSource.instances).toHaveLength(2)

    handle.close()
    vi.useRealTimers()
  })
})
