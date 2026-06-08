import { describe, expect, it } from "vitest"
import { faultToReplaySteps } from "../tools/scenario-runtime/faultReplay"
import type { FaultEventStep } from "../tools/scenario-runtime/schema"

describe("scenario runtime fault replay", () => {
  it("turns disconnect fault steps into runtime error events", () => {
    const steps = faultToReplaySteps({ kind: "disconnect", reason: "test disconnect" })
    expect(steps).toHaveLength(1)
    expect(steps[0]?.event).toMatchObject({
      type: "error",
      turn: 1,
      payload: {
        message: "Synthetic lifecycle disconnect: test disconnect",
        syntheticFault: true,
        fault: { kind: "disconnect", reason: "test disconnect" },
      },
    })
  })

  it("keeps every supported fault kind injectable", () => {
    const faults: FaultEventStep[] = [
      { kind: "engine-death", phase: "assistant-delta" },
      { kind: "stream-stall", streamId: "s1", durationMs: 2500 },
      { kind: "replay-duplicate", eventIds: ["a", "b"] },
      { kind: "event-zero-replay", from: "checkpoint" },
      { kind: "stale-generation", streamId: "s2", staleEvents: [] },
    ]
    for (const fault of faults) {
      const [step] = faultToReplaySteps(fault)
      expect(step?.event.type).toBe("error")
      expect(step?.event.payload).toMatchObject({ syntheticFault: true, fault })
    }
  })
})
