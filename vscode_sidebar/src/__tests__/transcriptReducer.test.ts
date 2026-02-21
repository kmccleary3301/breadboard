import test from "node:test"
import assert from "node:assert/strict"
import { reduceTranscriptEvents, type TranscriptRenderState } from "../transcriptReducer"
import type { StreamEventEnvelope } from "../hostController"

const mkEvent = (
  id: string,
  type: string,
  payload: Record<string, unknown>,
): StreamEventEnvelope => ({
  id,
  type,
  session_id: "s-1",
  turn: 1,
  timestamp: 1700000000,
  payload,
})

test("reduceTranscriptEvents normalizes event names and summarizes payload", () => {
  const initial: TranscriptRenderState = {
    totalEvents: 0,
    lastEventType: null,
    lines: [],
    entries: [],
  }
  const next = reduceTranscriptEvents(initial, [
    mkEvent("1", "assistant.message.delta", { delta: { text: "building patch" } }),
    mkEvent("2", "tool.result", { tool_name: "apply_patch", message: "ok" }),
  ])
  assert.equal(next.totalEvents, 2)
  assert.equal(next.lastEventType, "tool_result")
  assert.deepEqual(next.lines, [
    "[assistant_delta] building patch",
    "[tool_result] ok",
  ])
  assert.equal(next.entries.length, 2)
  assert.equal(next.entries[0].kind, "assistant_delta")
  assert.equal(next.entries[1].kind, "tool_result")
})

test("reduceTranscriptEvents enforces maxLines tail retention", () => {
  const initial: TranscriptRenderState = {
    totalEvents: 0,
    lastEventType: null,
    lines: [],
    entries: [],
  }
  const events: StreamEventEnvelope[] = []
  for (let i = 0; i < 50; i += 1) {
    events.push(mkEvent(String(i), "assistant.message.delta", { text: `line-${i}` }))
  }
  const next = reduceTranscriptEvents(initial, events, { maxLines: 20, maxEntries: 20 })
  assert.equal(next.totalEvents, 50)
  assert.equal(next.lines.length, 20)
  assert.equal(next.entries.length, 20)
  assert.equal(next.lines[0], "[assistant_delta] line-30")
  assert.equal(next.lines[19], "[assistant_delta] line-49")
})

test("reduceTranscriptEvents does not mutate input state", () => {
  const initial: TranscriptRenderState = {
    totalEvents: 1,
    lastEventType: "assistant_delta",
    lines: ["[assistant_delta] prior"],
    entries: [
      {
        id: "1",
        type: "assistant_delta",
        kind: "assistant_delta",
        summary: "prior",
      },
    ],
  }
  const snapshot = {
    totalEvents: initial.totalEvents,
    lastEventType: initial.lastEventType,
    lines: initial.lines.slice(),
    entries: initial.entries.slice(),
  }
  const next = reduceTranscriptEvents(initial, [mkEvent("2", "assistant.message.end", {})], { maxLines: 200 })
  assert.equal(next.totalEvents, 2)
  assert.equal(initial.totalEvents, snapshot.totalEvents)
  assert.equal(initial.lastEventType, snapshot.lastEventType)
  assert.deepEqual(initial.lines, snapshot.lines)
  assert.deepEqual(initial.entries, snapshot.entries)
})
