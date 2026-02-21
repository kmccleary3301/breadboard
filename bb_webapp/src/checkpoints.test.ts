import { describe, expect, it } from "vitest"
import type { SessionEvent } from "@breadboard/sdk"
import { parseCheckpointListEvent, parseCheckpointRestoreEvent } from "./checkpoints"

const event = (partial: Partial<SessionEvent> & Pick<SessionEvent, "id" | "type">): SessionEvent => ({
  id: partial.id,
  type: partial.type,
  session_id: partial.session_id ?? "session-1",
  turn: partial.turn ?? null,
  timestamp: partial.timestamp ?? 1_000,
  payload: partial.payload ?? {},
})

describe("checkpoints", () => {
  it("normalizes and sorts checkpoint lists", () => {
    const rows = parseCheckpointListEvent(
      event({
        id: "c1",
        type: "checkpoint_list",
        payload: {
          checkpoints: [
            { checkpoint_id: "cp-1", created_at_ms: 1000, label: "first" },
            { checkpoint_id: "cp-2", created_at_ms: 2000, label: "second" },
          ],
        },
      }),
    )
    expect(rows.map((row) => row.id)).toEqual(["cp-2", "cp-1"])
  })

  it("normalizes restore success/failure payloads", () => {
    const ok = parseCheckpointRestoreEvent(
      event({ id: "r1", type: "checkpoint_restored", payload: { checkpoint_id: "cp-2", status: "ok" } }),
    )
    const bad = parseCheckpointRestoreEvent(
      event({ id: "r2", type: "checkpoint_restored", payload: { checkpoint_id: "cp-1", status: "error" } }),
    )
    expect(ok.status).toBe("ok")
    expect(bad.status).toBe("error")
  })
})
