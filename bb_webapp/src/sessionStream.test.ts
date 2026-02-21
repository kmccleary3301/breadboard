import { describe, expect, it } from "vitest"
import { ApiError, type SessionEvent } from "@breadboard/sdk"
import { runSessionStreamLoop, type SessionStreamReader } from "./sessionStream"

const event = (id: string): SessionEvent => ({
  id,
  type: "assistant_delta",
  session_id: "session-1",
  turn: null,
  timestamp: Date.now(),
  payload: { delta: id },
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
})
