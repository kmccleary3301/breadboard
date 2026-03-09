import test from "node:test"
import assert from "node:assert/strict"

import { projectBackboneTurnToAiSdkFrames } from "../src/index.js"
import type { BackboneTurnResult } from "@breadboard/backbone"

const result: BackboneTurnResult = {
  supportClaim: {
    level: "supported",
    summary: "Supported",
    executionProfileId: "trusted_local",
    fallbackAvailable: false,
    unsupportedFields: [],
    evidenceMode: "replay_strict",
  },
  projectionProfile: { id: "ai_sdk_transport", summary: "AI SDK transport" },
  runContextId: "run-1",
  transcript: {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: "s-1",
    runId: "run-1",
    eventCursor: 3,
    items: [
      {
        kind: "tool_result",
        visibility: "model",
        content: { parts: [{ preview: "pwd => /tmp/project" }] },
        provenance: { source: "test" },
      },
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: "hello from backbone" },
        provenance: { source: "test" },
      },
    ],
  },
  events: [],
}

test("projectBackboneTurnToAiSdkFrames emits start, text, tool, and finish frames", () => {
  const frames = projectBackboneTurnToAiSdkFrames(result)
  assert.deepEqual(frames, [
    {
      type: "start",
      messageId: "run-1",
      supportLevel: "supported",
      projectionProfile: "ai_sdk_transport",
    },
    {
      type: "text-delta",
      messageId: "run-1",
      text: "hello from backbone",
    },
    {
      type: "tool",
      messageId: "run-1",
      preview: "pwd => /tmp/project",
    },
    {
      type: "finish",
      messageId: "run-1",
      stopReason: "stop",
    },
  ])
})
