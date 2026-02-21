import { ApiError, type SessionEvent } from "@breadboard/sdk"

export class StaleStreamTimeoutError extends Error {
  readonly timeoutMs: number

  constructor(timeoutMs: number) {
    super(`stream heartbeat timeout (${timeoutMs}ms)`)
    this.name = "StaleStreamTimeoutError"
    this.timeoutMs = timeoutMs
  }
}

export type SessionStreamConnection = {
  sessionId: string
  lastEventId?: string
  retryCount: number
  replay: boolean
}

export type SessionStreamReader = (args: {
  lastEventId?: string
  replay?: boolean
  fromId?: string
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
  onSequenceGap?: (meta: { expectedSeq: number; actualSeq: number; event: SessionEvent }) => void
  onReplayCatchupStart?: (meta: { fromId: string; expectedSeq: number; actualSeq: number }) => void
  retryBackoffMs?: readonly number[]
  heartbeatTimeoutMs?: number
  sleep?: (ms: number) => Promise<void>
}

const DEFAULT_BACKOFF_MS = [250, 1000, 2000, 5000]

const defaultSleep = async (ms: number): Promise<void> => {
  await new Promise<void>((resolve) => setTimeout(resolve, ms))
}

const nextWithHeartbeat = async <T>(
  iterator: AsyncIterator<T>,
  timeoutMs: number | undefined,
): Promise<IteratorResult<T>> => {
  if (!timeoutMs || timeoutMs <= 0) {
    return await iterator.next()
  }
  return await new Promise<IteratorResult<T>>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new StaleStreamTimeoutError(timeoutMs))
    }, timeoutMs)
    iterator
      .next()
      .then((result) => {
        clearTimeout(timer)
        resolve(result)
      })
      .catch((error) => {
        clearTimeout(timer)
        reject(error)
      })
  })
}

export const runSessionStreamLoop = async (options: SessionStreamLoopOptions): Promise<void> => {
  const backoff = options.retryBackoffMs?.length ? options.retryBackoffMs : DEFAULT_BACKOFF_MS
  const sleep = options.sleep ?? defaultSleep
  let retryCount = 0
  let lastSeq: number | null = null
  let lastEventIdSeen: string | undefined
  let replayFromId: string | null = null

  while (!options.signal.aborted) {
    try {
      const lastEventId: string | undefined = replayFromId ?? options.getLastEventId()
      const replay = replayFromId != null
      options.onConnecting?.({
        sessionId: options.sessionId,
        retryCount,
        lastEventId,
        replay,
      })
      options.onConnected?.({
        sessionId: options.sessionId,
        retryCount,
        lastEventId,
        replay,
      })
      let replayRequested = false
      const iterable = options.stream({
        lastEventId,
        replay,
        fromId: replayFromId ?? undefined,
        signal: options.signal,
      })
      const iterator = iterable[Symbol.asyncIterator]()
      try {
        while (!options.signal.aborted) {
          const result = await nextWithHeartbeat(iterator, options.heartbeatTimeoutMs)
          if (result.done) break
          const event = result.value
          if (typeof event.seq === "number") {
            if (lastSeq != null && event.seq !== lastSeq + 1) {
              const cursor: string | undefined = lastEventIdSeen ?? lastEventId
              if (!replay && cursor) {
                replayRequested = true
                replayFromId = cursor
                retryCount = 0
                options.onReplayCatchupStart?.({
                  fromId: cursor,
                  expectedSeq: lastSeq + 1,
                  actualSeq: event.seq,
                })
                break
              }
              options.onSequenceGap?.({
                expectedSeq: lastSeq + 1,
                actualSeq: event.seq,
                event,
              })
              return
            }
            lastSeq = event.seq
          }
          options.onEvent(event)
          lastEventIdSeen = event.id
        }
      } finally {
        if (typeof iterator.return === "function") {
          await iterator.return().catch(() => undefined)
        }
      }
      if (replayRequested) {
        continue
      }
      replayFromId = null
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
      if (options.signal.aborted) return
      await sleep(waitMs)
    }
  }
}
