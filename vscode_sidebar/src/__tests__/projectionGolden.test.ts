import test from "node:test"
import assert from "node:assert/strict"
import { reduceTranscriptEvents, type TranscriptRenderState } from "../transcriptReducer"
import type { StreamEventEnvelope } from "../hostController"

const mk = (id: string, type: string, payload: Record<string, unknown>): StreamEventEnvelope => ({
  id,
  type,
  session_id: "s1",
  turn: 1,
  timestamp: 1700000000,
  payload,
})

test("projection golden: mixed event stream yields stable entries", () => {
  const initial: TranscriptRenderState = {
    totalEvents: 0,
    lastEventType: null,
    lines: [],
    entries: [],
  }
  const events: StreamEventEnvelope[] = [
    mk("1", "user_message", { text: "implement x" }),
    mk("2", "assistant.message.delta", { delta: { text: "working..." } }),
    mk("3", "tool_call", { tool: "apply_patch", action: "write", call_id: "c1" }),
    mk("4", "tool.result", { tool: "apply_patch", status: "ok", artifact_ref: { path: "patches/p1.diff" } }),
    mk("5", "permission_request", { request_id: "p1", tool: "bash", summary: "run shell" }),
    mk("6", "permission_response", { request_id: "p1", decision: "allow_once" }),
    mk("7", "task_event", { status: "running", description: "worker started" }),
  ]

  const next = reduceTranscriptEvents(initial, events, { maxLines: 200, maxEntries: 200 })
  assert.equal(next.totalEvents, 7)
  assert.equal(next.lastEventType, "task_event")
  assert.deepEqual(
    next.entries.map((entry) => entry.kind),
    ["user", "assistant_delta", "tool_call", "tool_result", "permission_request", "permission_response", "task_event"],
  )
  assert.equal(next.entries[3].artifactPath, "patches/p1.diff")
  assert.equal(next.entries[4].requestId, "p1")
  assert.equal(next.entries[5].decision, "allow_once")
})

test("projection golden: bounded tail clipping is deterministic", () => {
  const initial: TranscriptRenderState = {
    totalEvents: 0,
    lastEventType: null,
    lines: [],
    entries: [],
  }
  const events: StreamEventEnvelope[] = []
  for (let i = 0; i < 30; i += 1) {
    events.push(mk(String(i), "assistant.message.delta", { text: `step-${i}` }))
  }
  const next = reduceTranscriptEvents(initial, events, { maxLines: 20, maxEntries: 10 })
  assert.equal(next.lines.length, 20)
  assert.equal(next.entries.length, 20)
  assert.equal(next.lines[0], "[assistant_delta] step-10")
  assert.equal(next.lines[19], "[assistant_delta] step-29")
  assert.equal(next.entries[0].id, "10")
  assert.equal(next.entries[9].id, "19")
  assert.equal(next.entries[19].id, "29")
})
