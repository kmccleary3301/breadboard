import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { applyEventToProjection, initialProjectionState } from "./projection"

const event = (partial: Partial<SessionEvent> & Pick<SessionEvent, "id" | "type">): SessionEvent => ({
  id: partial.id,
  type: partial.type,
  session_id: partial.session_id ?? "session-1",
  turn: partial.turn ?? null,
  timestamp: partial.timestamp ?? Date.now(),
  payload: partial.payload ?? {},
})

describe("checkpoint flow smoke", () => {
  it("simulates list -> restore -> resumed assistant stream progression", () => {
    let state = initialProjectionState
    state = applyEventToProjection(
      state,
      event({
        id: "list",
        type: "checkpoint_list",
        payload: { checkpoints: [{ checkpoint_id: "cp-1", created_at_ms: 1000 }] },
      }),
    )
    expect(state.activeCheckpointId).toBe("cp-1")

    state = applyEventToProjection(
      state,
      event({
        id: "restored",
        type: "checkpoint_restored",
        payload: { checkpoint_id: "cp-1", mode: "code", prune: true },
      }),
    )
    expect(state.lastCheckpointRestore?.status).toBe("ok")

    state = applyEventToProjection(
      state,
      event({ id: "d1", type: "assistant.message.delta", payload: { delta: "hello " } }),
    )
    state = applyEventToProjection(
      state,
      event({ id: "d2", type: "assistant.message.delta", payload: { delta: "again" } }),
    )
    state = applyEventToProjection(state, event({ id: "d3", type: "assistant.message.end" }))
    expect(state.transcript[state.transcript.length - 1]?.text).toContain("hello again")
  })
})
