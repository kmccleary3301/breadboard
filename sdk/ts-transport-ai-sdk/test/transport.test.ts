import test from "node:test"
import assert from "node:assert/strict"

import {
  createAiSdkTransportSession,
  deriveAiSdkTransportState,
  projectBackboneTurnToAiSdkFrames,
  projectBackboneTurnToAiSdkTransport,
} from "../src/index.js"
import type { BackboneTurnResult } from "@breadboard/backbone"
import type { TranscriptContinuationPatchV1 } from "@breadboard/kernel-contracts"

const continuationPatch: TranscriptContinuationPatchV1 = {
  schema_version: "bb.transcript_continuation_patch.v1",
  patch_id: "patch-1",
  pre_state_ref: "digest:pre",
  appended_messages: [{ kind: "assistant_message" }],
  appended_tool_events: [{ kind: "tool_result" }],
  lineage_updates: [],
  compaction_markers: [],
  post_state_digest: "digest:post",
  lossiness_flags: [],
}

const result: BackboneTurnResult = {
  supportClaim: {
    level: "supported",
    summary: "Supported",
    executionProfileId: "trusted_local",
    executionProfile: {
      id: "trusted_local",
      summary: "Trusted local",
      placementHint: "local_process",
      securityTierHint: "trusted_dev",
      recommendedFor: ["local developer workflows"],
      backendHint: "inline",
    },
    fallbackAvailable: false,
    unsupportedFields: [],
    evidenceMode: "replay_strict",
    recommendedHostMode: "inline",
    confidence: "high",
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
  providerTurn: {
    providerExchange: {
      schema_version: "bb.provider_exchange.v1",
      exchange_id: "exchange-1",
      request: {
        provider_family: "openai",
        runtime_id: "responses",
        route_id: "default",
        model: "openai/gpt-5.4-mini",
        stream: true,
        message_count: 1,
        tool_count: 0,
        metadata: {},
      },
      response: {
        message_count: 1,
        finish_reasons: ["stop"],
        usage: null,
        metadata: {},
      },
    },
    runContext: {
      schema_version: "bb.run_context.v1",
      request_id: "run-1",
      session_id: "s-1",
      engine_family: "breadboard_ts",
      engine_ref: null,
      resolved_model: "openai/gpt-5.4-mini",
      resolved_provider_route: "default",
      execution_mode: "interactive",
      active_mode: null,
      metadata: {},
    },
    events: [],
    transcript: {
      schemaVersion: "bb.session_transcript.v1",
      sessionId: "s-1",
      runId: "run-1",
      eventCursor: 3,
      items: [],
    },
    transcriptContinuationPatch: continuationPatch,
  },
}

test("projectBackboneTurnToAiSdkFrames emits start, text, tool, and finish frames", () => {
  const frames = projectBackboneTurnToAiSdkFrames(result)
  assert.deepEqual(frames, [
    {
      type: "start",
      messageId: "run-1",
      supportLevel: "supported",
      projectionProfile: "ai_sdk_transport",
      executionProfileId: "trusted_local",
    },
    {
      type: "continuation-patch",
      messageId: "run-1",
      patchId: "patch-1",
      appendedMessageCount: 1,
      appendedToolEventCount: 1,
      postStateDigest: "digest:post",
      lossinessFlags: [],
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

test("deriveAiSdkTransportState captures transcript continuation progress", () => {
  const state = deriveAiSdkTransportState(result)
  assert.deepEqual(state, {
    lastMessageId: "run-1",
    transcriptDigest: "digest:post",
    turnCount: 1,
  })
})

test("projectBackboneTurnToAiSdkTransport emits a resume frame when prior state exists", () => {
  const projection = projectBackboneTurnToAiSdkTransport(result, {
    previousState: {
      lastMessageId: "run-0",
      transcriptDigest: "digest:pre",
      turnCount: 1,
    },
  })
  assert.deepEqual(projection.frames[0], {
    type: "resume",
    messageId: "run-1",
    patchId: "patch-1",
    postStateDigest: "digest:pre",
  })
  assert.deepEqual(projection.state, {
    lastMessageId: "run-1",
    transcriptDigest: "digest:post",
    turnCount: 2,
  })
})

test("createAiSdkTransportSession projects a first turn and then a resumed turn", () => {
  const session = createAiSdkTransportSession()
  const first = session.projectTurn(result, { messageId: "msg-1" })
  assert.deepEqual(first.frames.map((frame) => frame.type), [
    "start",
    "continuation-patch",
    "text-delta",
    "tool",
    "finish",
  ])
  assert.deepEqual(session.state, {
    lastMessageId: "msg-1",
    transcriptDigest: "digest:post",
    turnCount: 1,
  })

  const resumed = session.projectResumedTurn(result, { messageId: "msg-2" })
  assert.equal(resumed.frames[0]?.type, "resume")
  assert.deepEqual(session.state, {
    lastMessageId: "msg-2",
    transcriptDigest: "digest:post",
    turnCount: 2,
  })
})

test("createAiSdkTransportSession can auto-append turns and detect resumed projection", () => {
  const session = createAiSdkTransportSession()
  const first = session.appendTurn(result, { messageId: "auto-1" })
  assert.equal(first.resumed, false)
  assert.deepEqual(first.frames.map((frame) => frame.type), [
    "start",
    "continuation-patch",
    "text-delta",
    "tool",
    "finish",
  ])

  const second = session.appendTurn(result, { messageId: "auto-2" })
  assert.equal(second.resumed, true)
  assert.equal(second.frames[0]?.type, "resume")
  assert.deepEqual(session.state, {
    lastMessageId: "auto-2",
    transcriptDigest: "digest:post",
    turnCount: 2,
  })
})

test("createAiSdkTransportSession can emit a richer host-facing stream chunk", () => {
  const session = createAiSdkTransportSession()
  const chunk = session.createStreamChunk(result, { messageId: "chunk-1" })

  assert.equal(chunk.resumed, false)
  assert.equal(chunk.turnCount, 1)
  assert.equal(chunk.frames[0]?.type, "start")
  assert.deepEqual(chunk.state, {
    lastMessageId: "chunk-1",
    transcriptDigest: "digest:post",
    turnCount: 1,
  })
})
