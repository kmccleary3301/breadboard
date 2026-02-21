import test from "node:test"
import assert from "node:assert/strict"
import { sanitizeConnectionState, sanitizeEventsPayload, sanitizeStatePayload } from "../rpcEvents"

test("sanitizeConnectionState preserves known-safe fields", () => {
  assert.deepEqual(sanitizeConnectionState({ status: "connecting" }), { status: "connecting" })
  assert.deepEqual(
    sanitizeConnectionState({ status: "connected", sessionId: "s1", retryCount: 1 }),
    { status: "connected", sessionId: "s1", retryCount: 1 },
  )
  assert.deepEqual(
    sanitizeConnectionState({ status: "error", message: "boom", gapDetected: true }),
    { status: "error", message: "boom", gapDetected: true },
  )
})

test("sanitizeStatePayload strips malformed session rows", () => {
  const normalized = sanitizeStatePayload({
    sessions: [{ sessionId: "a", status: "running" }, { status: "bad" }, "x"],
    activeSessionId: "a",
  })
  assert.deepEqual(normalized, {
    sessions: [{ sessionId: "a", status: "running" }],
    activeSessionId: "a",
  })
})

test("sanitizeEventsPayload keeps ordered valid events and optional render", () => {
  const normalized = sanitizeEventsPayload({
    sessionId: "s1",
    events: [
      { id: "1", type: "assistant.message.delta", session_id: "s1", payload: { text: "a" } },
      { bad: true },
      { id: "2", type: "tool_call", session_id: "s1", payload: { tool: "apply_patch" } },
    ],
    render: {
      totalEvents: 2,
      lastEventType: "tool_call",
      lines: ["l1", "l2"],
      entries: [{ kind: "assistant_delta" }],
    },
  })
  assert.ok(normalized)
  assert.equal(normalized?.events.length, 2)
  assert.equal(normalized?.events[0].id, "1")
  assert.equal(normalized?.events[1].id, "2")
  assert.equal(normalized?.render?.totalEvents, 2)
  assert.equal(normalized?.render?.lines.length, 2)
})
