import { createParser, ParsedEvent, ReconnectInterval, EventSourceParseCallback } from "eventsource-parser"
import { loadAppConfig, type AppConfig } from "../config/appConfig.js"
import type { SessionEvent } from "./types.js"
import { ApiError } from "./client.js"

export interface EventStreamOptions {
  readonly signal?: AbortSignal
  readonly query?: Record<string, string | number | boolean>
  readonly config?: AppConfig
}

export const streamSessionEvents = async function* (
  sessionId: string,
  options: EventStreamOptions = {},
): AsyncGenerator<SessionEvent, void, void> {
  const config = options.config ?? loadAppConfig()
  const url = new URL(`/sessions/${sessionId}/events`, config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`)
  if (options.query) {
    for (const [key, value] of Object.entries(options.query)) {
      url.searchParams.set(key, String(value))
    }
  }
  const controller = new AbortController()
  const signal = options.signal
  const abortHandler = signal ? () => controller.abort() : undefined
  if (signal) {
    signal.addEventListener("abort", abortHandler as EventListener, { once: true })
  }
  const response = await fetch(url, {
    method: "GET",
    headers: config.authToken ? { Authorization: `Bearer ${config.authToken}` } : undefined,
    signal: controller.signal,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new ApiError(`Streaming request failed with status ${response.status}`, response.status, text)
  }
  if (!response.body) {
    throw new Error("Streaming response provided no body")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const buffer: SessionEvent[] = []
  const parser = createParser(((event: ParsedEvent | ReconnectInterval) => {
    if (event.type === "event" && "data" in event && event.data) {
      try {
        const payload = JSON.parse(event.data) as SessionEvent
        buffer.push(payload)
      } catch {
        // ignore malformed event
      }
    }
  }) as EventSourceParseCallback)

  const readWithAbort = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
    if (!signal) {
      return reader.read()
    }
    if (signal.aborted) {
      return { done: true, value: undefined }
    }
    return await new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
      const onAbort = () => resolve({ done: true, value: undefined })
      signal.addEventListener("abort", onAbort, { once: true })
      reader
        .read()
        .then((result) => resolve(result))
        .catch((error) => reject(error))
        .finally(() => {
          signal.removeEventListener("abort", onAbort)
        })
    })
  }

  try {
    while (true) {
      const { value, done } = await readWithAbort()
      if (done || signal?.aborted) {
        parser.reset()
        break
      }
      if (value) {
        parser.feed(decoder.decode(value, { stream: true }))
        while (buffer.length > 0) {
          const next = buffer.shift()
          if (next) {
            yield next
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
    if (response.body) {
      await response.body.cancel().catch(() => undefined)
    }
    if (signal) {
      signal.removeEventListener("abort", abortHandler as EventListener)
    }
  }
}
