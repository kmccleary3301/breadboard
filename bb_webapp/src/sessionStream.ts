import { ApiError, type SessionEvent } from "@breadboard/sdk"

export type SessionStreamConnection = {
  sessionId: string
  lastEventId?: string
  retryCount: number
}

export type SessionStreamReader = (args: {
  lastEventId?: string
  signal: AbortSignal
}) => AsyncIterable<SessionEvent>

export type SessionStreamLoopOptions = {
  sessionId: string
  signal: AbortSignal
  stream: SessionStreamReader
  getLastEventId: () => string | undefined
  onConnecting?: (meta: SessionStreamConnection) => void
  onConnected?: (meta: SessionStreamConnection) => void
  onEvent: (event: SessionEvent) => void
  onRetryError?: (error: unknown, waitMs: number, retryCount: number) => void
  onResumeWindowGap?: (error: ApiError) => void
  retryBackoffMs?: readonly number[]
  sleep?: (ms: number) => Promise<void>
}

const DEFAULT_BACKOFF_MS = [250, 1000, 2000, 5000]

const defaultSleep = async (ms: number): Promise<void> => {
  await new Promise<void>((resolve) => setTimeout(resolve, ms))
}

export const runSessionStreamLoop = async (options: SessionStreamLoopOptions): Promise<void> => {
  const backoff = options.retryBackoffMs?.length ? options.retryBackoffMs : DEFAULT_BACKOFF_MS
  const sleep = options.sleep ?? defaultSleep
  let retryCount = 0

  while (!options.signal.aborted) {
    try {
      const lastEventId = options.getLastEventId()
      options.onConnecting?.({
        sessionId: options.sessionId,
        retryCount,
        lastEventId,
      })
      for await (const event of options.stream({ lastEventId, signal: options.signal })) {
        if (options.signal.aborted) break
        options.onEvent(event)
        options.onConnected?.({
          sessionId: options.sessionId,
          retryCount,
          lastEventId,
        })
      }
      if (options.signal.aborted) break
      retryCount = 0
    } catch (error) {
      if (options.signal.aborted) return
      if (error instanceof ApiError && error.status === 409) {
        options.onResumeWindowGap?.(error)
        return
      }
      const waitMs = backoff[Math.min(retryCount, backoff.length - 1)]
      retryCount += 1
      options.onRetryError?.(error, waitMs, retryCount)
      await sleep(waitMs)
    }
  }
}
