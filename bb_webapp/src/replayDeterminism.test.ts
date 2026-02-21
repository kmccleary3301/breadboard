import type { SessionEvent } from "@breadboard/sdk"
import { describe, expect, it } from "vitest"
import { applyEventToProjection, initialProjectionState } from "./projection"
import { computeProjectionHash } from "./projectionHash"
import { buildReplayPackage, parseReplayPackage, serializeReplayPackage } from "./replayPackage"

const evt = (id: string, seq: number, type: SessionEvent["type"], payload: SessionEvent["payload"]): SessionEvent => ({
  id,
  seq,
  type,
  session_id: "session-1",
  turn: 1,
  timestamp: 1_000 + seq,
  payload,
})

describe("replay determinism", () => {
  it("produces stable ordering and hash across export/import round-trips", async () => {
    const source: SessionEvent[] = [
      evt("evt-3", 3, "assistant.message.end", {}),
      evt("evt-1", 1, "assistant.message.delta", { delta: "hello " }),
      evt("evt-2", 2, "assistant.message.delta", { delta: "world" }),
      evt("evt-2", 2, "assistant.message.delta", { delta: "world" }),
    ]

    const pkgA = buildReplayPackage({ sessionId: "session-1", events: source })
    const pkgB = buildReplayPackage({ sessionId: "session-1", events: [...source].reverse() })
    expect(pkgA.events.map((event) => event.id)).toEqual(["evt-1", "evt-2", "evt-3"])
    expect(pkgA.events.map((event) => event.id)).toEqual(pkgB.events.map((event) => event.id))

    const parsed = parseReplayPackage(serializeReplayPackage(pkgA))
    const projection = parsed.events.reduce(applyEventToProjection, initialProjectionState)
    const eventHash = await computeProjectionHash(parsed.events)
    const projectionHash = await computeProjectionHash(projection.events)

    expect(parsed.event_count).toBe(3)
    expect(eventHash).toBe(projectionHash)
  })
})
