import { describe, expect, it } from "vitest"
import { formatActivityTransitionTimeline, type ActivityTransitionTrace } from "../controllerTransitionTimeline.js"

describe("controllerTransitionTimeline", () => {
  it("formats transitions as from -> to lines with legality flags", () => {
    const traces: ActivityTransitionTrace[] = [
      {
        at: Date.UTC(2026, 1, 12, 12, 0, 0, 0),
        from: "idle",
        to: "thinking",
        eventType: "run.start",
        source: "event",
        reason: "applied",
        applied: true,
      },
      {
        at: Date.UTC(2026, 1, 12, 12, 0, 0, 100),
        from: "thinking",
        to: "session",
        eventType: "conversation.compaction.end",
        source: "event",
        reason: "hysteresis",
        applied: false,
      },
    ]
    const text = formatActivityTransitionTimeline(traces, 10)
    expect(text).toContain("idle -> thinking [applied]")
    expect(text).toContain("thinking -> session [blocked:hysteresis]")
    expect(text).toContain("event=run.start")
  })

  it("returns explicit message when no transitions exist", () => {
    const text = formatActivityTransitionTimeline([], 10)
    expect(text).toBe("(no transitions captured)")
  })
})
