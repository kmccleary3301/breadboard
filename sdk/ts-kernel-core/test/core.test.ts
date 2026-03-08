import test from "node:test"
import assert from "node:assert/strict"

import { buildKernelEventId, cloneTranscriptItem, eventBelongsToSession } from "../src/index.js"

test("kernel core helpers remain deterministic", () => {
  assert.equal(buildKernelEventId("run-1", 7), "run-1:evt:7")

  const original = {
    kind: "assistant_message",
    visibility: "model",
    content: { text: "hello" },
  } as const
  const cloned = cloneTranscriptItem(original)
  assert.deepEqual(cloned, original)
  assert.notEqual(cloned, original)

  const event = {
    schemaVersion: "bb.kernel_event.v1",
    eventId: "evt-1",
    runId: "run-1",
    sessionId: "sess-1",
    seq: 1,
    ts: "2026-03-08T00:00:00Z",
    actor: "engine",
    visibility: "model",
    kind: "assistant_message",
    payload: {},
  } as const
  assert.equal(eventBelongsToSession(event, "sess-1"), true)
  assert.equal(eventBelongsToSession(event, "sess-2"), false)
})
