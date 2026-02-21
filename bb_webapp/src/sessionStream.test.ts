import { describe, expect, it } from "vitest"
import { ApiError, type SessionEvent } from "@breadboard/sdk"
import { runSessionStreamLoop, StaleStreamTimeoutError, type SessionStreamReader } from "./sessionStream"

const event = (id: string): SessionEvent => ({
  id,
  type: "assistant_delta",
  session_id: "session-1",
  turn: null,
  timestamp: Date.now(),
  payload: { delta: id },
})

const eventWithSeq = (id: string, seq: number): SessionEvent => ({
  ...event(id),
  seq,
})

describe("runSessionStreamLoop", () => {
  it("retries after a generic stream failure and reconnects", async () => {
    const abort = new AbortController()
    let calls = 0
    const waits: number[] = []
    const connectionMeta: Array<{ lastEventId?: string; retryCount: number }> = []
    const seenEvents: string[] = []
    let lastEventId = "evt-0"

    const stream: SessionStreamReader = ({ lastEventId: cursor }) => ({
      async *[Symbol.asyncIterator]() {
        calls += 1
        if (calls === 1) {
          throw new Error("transient")
        }
        yield event("evt-1")
        return
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => lastEventId,
      onConnecting: (meta) => connectionMeta.push({ retryCount: meta.retryCount, lastEventId: meta.lastEventId }),
      onEvent: (row) => {
        seenEvents.push(row.id)
        lastEventId = row.id
        abort.abort()
      },
      onRetryError: () => {
        // Simulate local state update before retry.
        lastEventId = "evt-0-retry"
      },
      sleep: async (ms) => {
        waits.push(ms)
      },
      retryBackoffMs: [250, 1000],
    })

    expect(calls).toBe(2)
    expect(waits).toEqual([250])
    expect(seenEvents).toEqual(["evt-1"])
    expect(connectionMeta).toEqual([
      { retryCount: 0, lastEventId: "evt-0" },
      { retryCount: 1, lastEventId: "evt-0-retry" },
    ])
  })

  it("stops immediately on HTTP 409 resume-window gap without retrying", async () => {
    const abort = new AbortController()
    const waits: number[] = []
    let retryErrors = 0
    let gapSignals = 0

    const stream: SessionStreamReader = () => ({
      async *[Symbol.asyncIterator]() {
        throw new ApiError("gap", 409, { message: "resume window exceeded" })
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-last",
      onEvent: () => undefined,
      onRetryError: () => {
        retryErrors += 1
      },
      onResumeWindowGap: () => {
        gapSignals += 1
      },
      sleep: async (ms) => {
        waits.push(ms)
      },
    })

    expect(gapSignals).toBe(1)
    expect(retryErrors).toBe(0)
    expect(waits).toEqual([])
  })

  it("attempts replay catch-up once and then stops on unrecoverable sequence gap", async () => {
    const abort = new AbortController()
    let sequenceGapCount = 0
    let replayCatchupCount = 0
    const seen: Array<{ seq?: number; id: string }> = []
    const stream: SessionStreamReader = ({ replay }) => ({
      async *[Symbol.asyncIterator]() {
        if (!replay) {
          yield eventWithSeq("evt-1", 1)
          yield eventWithSeq("evt-3", 3)
          return
        }
        // Replayed stream still does not contain the expected seq=2, so it is unrecoverable.
        yield eventWithSeq("evt-4", 4)
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-0",
      onEvent: (row) => {
        seen.push({ id: row.id, seq: row.seq })
      },
      onSequenceGap: ({ expectedSeq, actualSeq }) => {
        sequenceGapCount += 1
        expect(expectedSeq).toBe(2)
        expect(actualSeq).toBe(4)
      },
      onReplayCatchupStart: ({ fromId, expectedSeq, actualSeq }) => {
        replayCatchupCount += 1
        expect(fromId).toBe("evt-1")
        expect(expectedSeq).toBe(2)
        expect(actualSeq).toBe(3)
      },
      onRetryError: () => {
        throw new Error("should not retry on sequence gap")
      },
    })

    expect(sequenceGapCount).toBe(1)
    expect(replayCatchupCount).toBe(1)
    expect(seen).toEqual([{ id: "evt-1", seq: 1 }])
  })

  it("recovers via replay catch-up when missing sequence is available", async () => {
    const abort = new AbortController()
    const seen: number[] = []
    let replayCatchupCount = 0

    const stream: SessionStreamReader = ({ replay }) => ({
      async *[Symbol.asyncIterator]() {
        if (!replay) {
          yield eventWithSeq("evt-1", 1)
          yield eventWithSeq("evt-3", 3)
          return
        }
        yield eventWithSeq("evt-2", 2)
        yield eventWithSeq("evt-3b", 3)
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-0",
      onReplayCatchupStart: () => {
        replayCatchupCount += 1
      },
      onEvent: (row) => {
        if (typeof row.seq === "number") {
          seen.push(row.seq)
          if (row.seq === 3) {
            abort.abort()
          }
        }
      },
    })

    expect(replayCatchupCount).toBe(1)
    expect(seen).toEqual([1, 2, 3])
  })

  it("retries when stream heartbeat stalls without new events", async () => {
    const abort = new AbortController()
    let retryCount = 0
    const waits: number[] = []

    const stream: SessionStreamReader = () => ({
      [Symbol.asyncIterator](): AsyncIterator<SessionEvent> {
        return {
          next: async () => await new Promise<IteratorResult<SessionEvent>>(() => undefined),
          return: async () => ({ done: true, value: undefined }),
        }
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-last",
      heartbeatTimeoutMs: 5,
      onEvent: () => undefined,
      onRetryError: (error) => {
        expect(error).toBeInstanceOf(StaleStreamTimeoutError)
        retryCount += 1
        abort.abort()
      },
      sleep: async (ms) => {
        waits.push(ms)
      },
      retryBackoffMs: [250],
    })

    expect(retryCount).toBe(1)
    // Aborted in onRetryError, so no backoff sleep should be applied.
    expect(waits).toEqual([])
  })

  it("recovers from drop+jitter by replaying and preserving sequence order", async () => {
    const abort = new AbortController()
    const waits: number[] = []
    const seenSeq: number[] = []
    let replayCatchupCount = 0
    let retryErrors = 0
    let calls = 0

    const stream: SessionStreamReader = ({ replay }) => ({
      async *[Symbol.asyncIterator]() {
        calls += 1
        if (!replay) {
          yield eventWithSeq("evt-1", 1)
          await new Promise<void>((resolve) => setTimeout(resolve, 1))
          yield eventWithSeq("evt-3", 3)
          return
        }
        if (calls === 2) {
          throw new Error("transient replay disconnect")
        }
        await new Promise<void>((resolve) => setTimeout(resolve, 1))
        yield eventWithSeq("evt-2", 2)
        yield eventWithSeq("evt-3b", 3)
        yield eventWithSeq("evt-4", 4)
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-0",
      onReplayCatchupStart: () => {
        replayCatchupCount += 1
      },
      onRetryError: () => {
        retryErrors += 1
      },
      onEvent: (row) => {
        if (typeof row.seq === "number") {
          seenSeq.push(row.seq)
          if (row.seq === 4) {
            abort.abort()
          }
        }
      },
      sleep: async (ms) => {
        waits.push(ms)
      },
      retryBackoffMs: [25, 50],
    })

    expect(replayCatchupCount).toBe(1)
    expect(retryErrors).toBe(1)
    expect(waits).toEqual([25])
    expect(seenSeq).toEqual([1, 2, 3, 4])
  })

  it("applies configured backoff across repeated transient dropouts", async () => {
    const abort = new AbortController()
    const waits: number[] = []
    let attempts = 0
    let retryErrors = 0

    const stream: SessionStreamReader = () => ({
      async *[Symbol.asyncIterator]() {
        attempts += 1
        if (attempts <= 3) {
          throw new Error(`drop-${attempts}`)
        }
        yield event("evt-final")
      },
    })

    await runSessionStreamLoop({
      sessionId: "session-1",
      signal: abort.signal,
      stream,
      getLastEventId: () => "evt-0",
      onRetryError: () => {
        retryErrors += 1
      },
      onEvent: () => {
        abort.abort()
      },
      sleep: async (ms) => {
        waits.push(ms)
      },
      retryBackoffMs: [10, 20, 40],
    })

    expect(retryErrors).toBe(3)
    expect(waits).toEqual([10, 20, 40])
    expect(attempts).toBe(4)
  })
})
