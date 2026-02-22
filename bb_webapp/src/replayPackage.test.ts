import type { SessionEvent } from "@breadboard/sdk"
import { describe, expect, it } from "vitest"
import { buildReplayPackage, parseReplayPackage, serializeReplayPackage } from "./replayPackage"

const baseEvent = (overrides: Partial<SessionEvent> = {}): SessionEvent => ({
  id: "evt-1",
  type: "assistant.message.delta",
  session_id: "session-1",
  turn: 1,
  timestamp: 1000,
  payload: { text: "hi" },
  ...overrides,
})

describe("replayPackage", () => {
  it("builds deterministic package order and dedupes", () => {
    const events: SessionEvent[] = [
      baseEvent({ id: "evt-2", seq: 2, timestamp: 1200 }),
      baseEvent({ id: "evt-1", seq: 1, timestamp: 1100 }),
      baseEvent({ id: "evt-2", seq: 2, timestamp: 1200 }),
      baseEvent({ id: "evt-other", session_id: "session-2", seq: 99 }),
    ]
    const pkg = buildReplayPackage({ sessionId: "session-1", events, projectionHash: "abc123" })
    expect(pkg.event_count).toBe(2)
    expect(pkg.events.map((event) => event.id)).toEqual(["evt-1", "evt-2"])
    expect(pkg.projection_hash).toBe("abc123")
  })

  it("redacts sensitive payload values during replay export", () => {
    const pkg = buildReplayPackage({
      sessionId: "session-1",
      events: [
        baseEvent({
          id: "evt-secure",
          payload: {
            token: "abc123",
            nested: { Authorization: "Bearer very-secret", safe: "ok" },
          },
        }),
      ],
    })
    const payload = pkg.events[0].payload as Record<string, unknown>
    expect(payload.token).toBe("[REDACTED]")
    expect((payload.nested as Record<string, unknown>).Authorization).toBe("[REDACTED]")
    expect((payload.nested as Record<string, unknown>).safe).toBe("ok")
  })

  it("parses serialized package and normalizes event_count", () => {
    const pkg = buildReplayPackage({
      sessionId: "session-1",
      events: [
        baseEvent({ id: "evt-2", seq: 2 }),
        baseEvent({ id: "evt-1", seq: 1 }),
      ],
    })
    const parsed = parseReplayPackage(serializeReplayPackage(pkg))
    expect(parsed.session_id).toBe("session-1")
    expect(parsed.event_count).toBe(2)
    expect(parsed.events.map((event) => event.id)).toEqual(["evt-1", "evt-2"])
  })

  it("rejects event session mismatch", () => {
    const raw = JSON.stringify({
      format: "breadboard.webapp.replay",
      version: 1,
      session_id: "session-1",
      exported_at: "2026-02-21T00:00:00.000Z",
      events: [baseEvent({ session_id: "session-2" })],
    })
    expect(() => parseReplayPackage(raw)).toThrow(/session_id mismatch/)
  })

  it("rejects malformed package shapes", () => {
    expect(() => parseReplayPackage("{}")).toThrow(/unsupported format/)
    expect(() => parseReplayPackage("{ bad json")).toThrow(/expected JSON/)
  })
})
