import { parentPort, workerData } from "node:worker_threads"

type BootstrapData = {
  readonly bundleUrl?: string
}

const port = parentPort
if (!port) {
  throw new Error("[breadboard] Markdown worker bootstrap requires parentPort")
}

const globalAny = globalThis as unknown as {
  self?: unknown
  postMessage?: (value: unknown) => void
  onmessage?: ((event: { data: unknown }) => void) | null
  addEventListener?: (type: string, listener: (event: { data: unknown }) => void) => void
  removeEventListener?: (type: string, listener: (event: { data: unknown }) => void) => void
}

if (!globalAny.self) {
  globalAny.self = globalThis
}

globalAny.postMessage = (value: unknown) => {
  port.postMessage(value)
}

const messageListeners = new Set<(event: { data: unknown }) => void>()
globalAny.addEventListener = (type: string, listener: (event: { data: unknown }) => void) => {
  if (type !== "message") return
  messageListeners.add(listener)
}
globalAny.removeEventListener = (type: string, listener: (event: { data: unknown }) => void) => {
  if (type !== "message") return
  messageListeners.delete(listener)
}

let ready = false
const buffered: unknown[] = []

const dispatchMessage = (data: unknown): void => {
  const event = { data }
  const hostListener = globalAny.onmessage
  if (typeof hostListener === "function") {
    try {
      hostListener(event)
    } catch (error) {
      console.error("[breadboard] Markdown worker onmessage threw:", error)
    }
  }
  if (messageListeners.size > 0) {
    for (const listener of messageListeners) {
      try {
        listener(event)
      } catch (error) {
        console.error("[breadboard] Markdown worker addEventListener callback threw:", error)
      }
    }
  }
}

port.on("message", (data: unknown) => {
  if (!ready) {
    buffered.push(data)
    return
  }
  dispatchMessage(data)
})

const bootstrap = (workerData ?? {}) as BootstrapData
const bundleUrl = bootstrap.bundleUrl
if (!bundleUrl) {
  throw new Error("[breadboard] Markdown worker bundle URL missing")
}

void (async () => {
  await import(bundleUrl)
  ready = true
  if (buffered.length > 0) {
    for (const data of buffered.splice(0, buffered.length)) {
      dispatchMessage(data)
    }
  }
})().catch((error) => {
  console.error("[breadboard] Failed to load markdown worker bundle:", error)
  throw error
})
