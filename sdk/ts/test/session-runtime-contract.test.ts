import assert from "node:assert/strict"
import test from "node:test"

import {
  computeSessionReplayDigest,
  createCanonicalE4Client,
  decodeLoggedSessionEvent,
  REPLAY_RETENTION_MAX_AGE_MS,
  REPLAY_RETENTION_MAX_EVENTS,
  replayConfigurationDigest,
} from "../dist/session-runtime.js"

test("canonical session create omits config_path for the packaged default profile", async () => {
  const replayFacts = {
    replayRetention: {
      maxEvents: REPLAY_RETENTION_MAX_EVENTS,
      maxAgeMs: REPLAY_RETENTION_MAX_AGE_MS,
      configurationDigest: replayConfigurationDigest,
    },
    earliestRetainedSequence: null,
    earliestRetainedEventId: null,
    headSequence: 0,
    headEventId: null,
    retainedHistory: "complete" as const,
  }
  const sessionReplayContractDigest = await computeSessionReplayDigest(replayFacts)
  let createBody: Record<string, unknown> | null = null
  const fetchImplementation: typeof fetch = async (input, init) => {
    const url = new URL(String(input))
    if (url.pathname === "/v1/internal/sessions" && init?.method === "POST") {
      createBody = JSON.parse(String(init.body)) as Record<string, unknown>
      return new Response(JSON.stringify({ session_id: "default-profile-session" }), {
        headers: { "content-type": "application/json" },
      })
    }
    assert.equal(url.pathname, "/v1/internal/sessions/default-profile-session")
    return new Response(JSON.stringify({
      session_id: "default-profile-session",
      status: "running",
      created_at: "2026-08-28T00:00:00Z",
      last_activity_at: "2026-08-28T00:00:00Z",
      model: null,
      mode: null,
      turn_admission: "idle",
      active_turn_id: null,
      queued_turn_count: 0,
      terminalTurns: [],
      ...replayFacts,
      sessionReplayContractDigest,
    }), { headers: { "content-type": "application/json" } })
  }
  const client = createCanonicalE4Client({
    baseUrl: "http://breadboard.test:9099",
    fetch: fetchImplementation,
  })

  await client.create({ task: "Use the packaged profile" })

  assert.deepEqual(createBody, { task: "Use the packaged profile" })
})

test("canonical event decoder accepts stop_requested session terminalization", () => {
  const event = decodeLoggedSessionEvent({
    stable_cursor: true,
    id: "event-1",
    seq: 1,
    session_id: "session-1",
    input_id: "input-1",
    turn_id: "turn-1",
    timestamp_ms: 1,
    type: "turn_cancelled",
    payload: { reason: "stop_requested" },
  })

  assert.equal(event.kind, "turn_cancelled")
  assert.equal(event.payload.reason, "stop_requested")
})
