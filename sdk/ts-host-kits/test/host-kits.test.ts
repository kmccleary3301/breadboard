import test from "node:test"
import assert from "node:assert/strict"

import {
  buildHostCoordinationProjection,
  buildHostProjectionEnvelope,
  buildHostResultMeta,
  buildHostTranscriptProjection,
  buildProviderHostTurnView,
  resolveProviderHostTurnView,
  buildFallbackHostKitInvocation,
  buildSupportedHostKitInvocation,
  createProviderHostSession,
  createHostKit,
  emitHostProjectionCallbacks,
  normalizeHostManagedTranscript,
  normalizeHostKitSupportClaim,
} from "../src/index.js"

const supportedClaim = {
  level: "supported" as const,
  summary: "Supported",
  executionProfileId: "trusted_local" as const,
  executionProfile: {
    id: "trusted_local" as const,
    summary: "Trusted local",
    placementHint: "local_process" as const,
    securityTierHint: "trusted_dev" as const,
    recommendedFor: ["local developer workflows"],
    backendHint: "inline" as const,
  },
  fallbackAvailable: false,
  unsupportedFields: [],
  evidenceMode: "replay_strict",
  recommendedHostMode: "inline" as const,
  confidence: "high" as const,
}

test("createHostKit returns a stable classify/invoke surface", async () => {
  const hostKit = createHostKit<{ id: string }, { ok: boolean }, { source: string }>({
    id: "test.hostkit",
    classify(request) {
      return {
        mode: "supported",
        request,
        unsupportedFields: [],
        supportClaim: supportedClaim,
      }
    },
    async invoke(request) {
      return buildSupportedHostKitInvocation({
        result: { ok: true },
        invocation: { source: request.id },
        supportClaim: supportedClaim,
      })
    },
  })

  assert.equal(hostKit.id, "test.hostkit")
  assert.equal(hostKit.classify({ id: "r-1" }).mode, "supported")
  assert.deepEqual(await hostKit.invoke({ id: "r-1" }), {
    mode: "supported",
    result: { ok: true },
    invocation: { source: "r-1" },
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
  })
})

test("normalizeHostKitSupportClaim preserves the base claim while applying explicit overrides", () => {
  const claim = normalizeHostKitSupportClaim(supportedClaim, {
    level: "fallback",
    summary: "Fallback required",
    unsupportedFields: ["images"],
  })
  assert.equal(claim.level, "fallback")
  assert.equal(claim.summary, "Fallback required")
  assert.deepEqual(claim.unsupportedFields, ["images"])
  assert.equal(claim.executionProfile.id, "trusted_local")
})

test("buildFallbackHostKitInvocation emits a fallback-mode invocation with normalized support metadata", () => {
  const invocation = buildFallbackHostKitInvocation({
    result: { ok: false },
    invocation: { source: "native-fallback" },
    supportClaim: normalizeHostKitSupportClaim(supportedClaim, {
      summary: "Fallback required",
      unsupportedFields: ["images"],
    }),
  })
  assert.equal(invocation.mode, "fallback")
  assert.equal(invocation.supportClaim.level, "fallback")
  assert.equal(invocation.supportClaim.fallbackAvailable, true)
  assert.deepEqual(invocation.supportClaim.unsupportedFields, ["images"])
})

test("createProviderHostSession preserves transcript continuity and projection state", async () => {
  let transcriptCounter = 0
  const providerSession = createProviderHostSession<
    { assistantText: string },
    { count: number },
    { summary: string }
  >({
    backboneSession: {
      descriptor: {
        sessionId: "session-1",
      },
      workspace: {} as never,
      projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
      inspectCoordination() {
        return {
          signals: [],
          review_verdicts: [],
          directives: [],
          latest_signal_by_code: {},
          unresolved_interventions: [],
          resolved_interventions: [],
        }
      },
      classifyProviderTurn() {
        return supportedClaim
      },
      classifyToolTurn() {
        return supportedClaim
      },
      async runProviderTurn(input) {
        transcriptCounter += 1
        return {
          supportClaim: supportedClaim,
          projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
          runContextId: `run-${transcriptCounter}`,
          transcript: {
            schemaVersion: "bb.session_transcript.v1",
            sessionId: "session-1",
            metadata: {
              preserved_prefix_items: transcriptCounter - 1,
            },
            items: [
              {
                kind: "assistant_message",
                visibility: "model",
                content: { text: input.assistantText },
              },
            ],
          },
          events: [],
          coordinationInspection: {
            signals: [],
            review_verdicts: [],
            directives: [],
            latest_signal_by_code: {},
            unresolved_interventions: [],
            resolved_interventions: [],
          },
          providerTurn: undefined,
          driverTurn: undefined,
          unsupportedCase: undefined,
        }
      },
      async runToolTurn() {
        throw new Error("not used in this test")
      },
    },
    buildInput(input, transcript) {
      return {
        request: {
          schema_version: "bb.run_request.v1",
          request_id: `request-${transcriptCounter + 1}`,
          entry_mode: "test",
          task: input.assistantText,
          workspace_root: null,
          requested_model: null,
          requested_features: {},
          metadata: {
            prior_items: transcript?.items.length ?? 0,
          },
        },
        providerExchange: {
          schema_version: "bb.provider_exchange.v1",
          exchange_id: `exchange-${transcriptCounter + 1}`,
          request: {
            provider_family: "openai",
            runtime_id: "responses",
            route_id: null,
            model: "mock",
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
        assistantText: input.assistantText,
        existingTranscript: transcript ?? [],
      }
    },
    projectTurn(turn, context) {
      const nextCount = (context.previousState?.count ?? 0) + 1
      return {
        state: { count: nextCount },
        output: {
          summary: `${context.resumed ? "resume" : "turn"}:${turn.runContextId}`,
        },
      }
    },
  })

  const first = await providerSession.runProviderTurn({ assistantText: "hello" })
  assert.equal(first.supportClaim.level, "supported")
  assert.equal(first.projectionOutput?.summary, "turn:run-1")
  assert.equal(providerSession.transcript?.metadata?.preserved_prefix_items, 0)
  assert.equal(providerSession.projectionState?.count, 1)

  const second = await providerSession.continueProviderTurn({ assistantText: "continue" })
  assert.equal(second.projectionOutput?.summary, "resume:run-2")
  assert.equal(providerSession.transcript?.metadata?.preserved_prefix_items, 1)
  assert.equal(providerSession.projectionState?.count, 2)

  const view = buildProviderHostTurnView(second)
  assert.equal(view.supportClaim.level, "supported")
  assert.equal(view.projectionOutput?.summary, "resume:run-2")
  assert.equal(view.projectionState?.count, 2)
})

test("buildHostCoordinationProjection reduces the read-only inspection shape for host use", () => {
  const projection = buildHostCoordinationProjection({
    signals: [],
    review_verdicts: [],
    directives: [],
    latest_signal_by_code: {
      human_required: {
        schema_version: "bb.signal.v1",
        signal_id: "signal-human-1",
        code: "human_required",
        task_id: "task_worker_1",
        authority_scope: "task",
        status: "accepted",
        source: { kind: "runtime", emitter_role: "runtime" },
        evidence_refs: [],
        payload: {},
      },
    },
    unresolved_interventions: [],
    resolved_interventions: [
      {
        intervention_id: "intervention_review-1",
        status: "resolved",
        review_verdict_id: "review-1",
        signal_id: "signal-human-1",
        source_task_id: "task_worker_1",
        mission_task_id: "task_supervisor_1",
        required_input: "Approve guarded retry",
        blocking_reason: "Operator approval required",
        allowed_host_actions: ["continue", "checkpoint", "terminate"],
        review_verdict: {
          schema_version: "bb.review_verdict.v1",
          verdict_id: "review-1",
          reviewer_task_id: "task_supervisor_1",
          reviewer_role: "supervisor",
          subject: {
            kind: "signal",
            signal_id: "signal-human-1",
            signal_code: "human_required",
            source_task_id: "task_worker_1",
          },
          verdict_code: "human_required",
          mission_completed: false,
          required_deliverable_refs: [],
          deliverable_refs: [],
          missing_deliverable_refs: [],
          signal_evidence_refs: [],
          metadata: {},
        },
        signal: {
          schema_version: "bb.signal.v1",
          signal_id: "signal-human-1",
          code: "human_required",
          task_id: "task_worker_1",
          authority_scope: "task",
          status: "accepted",
          source: { kind: "runtime", emitter_role: "runtime" },
          evidence_refs: [],
          payload: {},
        },
        directives: [
          {
            schema_version: "bb.directive.v1",
            directive_id: "directive-host-1",
            directive_code: "continue",
            issuer_task_id: "host::task_supervisor_1",
            issuer_role: "host",
            target_task_id: "task_worker_1",
            based_on_verdict_id: "review-1",
            based_on_signal_id: "signal-human-1",
            payload: { wake_target: true },
            evidence_refs: [],
            metadata: {},
          },
        ],
        host_responses: [
          {
            schema_version: "bb.directive.v1",
            directive_id: "directive-host-1",
            directive_code: "continue",
            issuer_task_id: "host::task_supervisor_1",
            issuer_role: "host",
            target_task_id: "task_worker_1",
            based_on_verdict_id: "review-1",
            based_on_signal_id: "signal-human-1",
            payload: { wake_target: true },
            evidence_refs: [],
            metadata: {},
          },
        ],
      },
    ],
  })

  assert.deepEqual(projection.latestSignalCodes, ["human_required"])
  assert.equal(projection.pendingInterventionCount, 0)
  assert.equal(projection.resolvedInterventionCount, 1)
  assert.deepEqual(projection.resolvedInterventions[0], {
    interventionId: "intervention_review-1",
    status: "resolved",
    sourceTaskId: "task_worker_1",
    missionTaskId: "task_supervisor_1",
    requiredInput: "Approve guarded retry",
    blockingReason: "Operator approval required",
    allowedHostActions: ["continue", "checkpoint", "terminate"],
    latestHostDirectiveCode: "continue",
    signalCode: "human_required",
    reviewVerdictCode: "human_required",
  })
})

test("buildHostTranscriptProjection extracts assistant text and tool previews", () => {
  const projection = buildHostTranscriptProjection({
    transcript: {
      schemaVersion: "bb.session_transcript.v1",
      sessionId: "session-1",
      items: [
        {
          kind: "assistant_message",
          visibility: "model",
          content: { text: "hello" },
        },
        {
          kind: "tool_result",
          visibility: "model",
          content: { parts: [{ preview: "pwd => /tmp/project" }] },
        },
      ],
    },
  })

  assert.deepEqual(projection, {
    entries: [
      { kind: "assistant_text", text: "hello" },
      { kind: "tool_preview", text: "pwd => /tmp/project" },
    ],
    assistantTexts: ["hello"],
    toolPreviews: ["pwd => /tmp/project"],
  })
})

test("emitHostProjectionCallbacks replays projected assistant text, tool previews, and agent events", async () => {
  const seen: string[] = []
  await emitHostProjectionCallbacks(
    {
      async onAssistantMessageStart() {
        seen.push("assistant:start")
      },
      async onPartialReply(payload) {
        seen.push(`assistant:${payload.text}`)
      },
      async onToolResult(payload) {
        seen.push(`tool:${payload.text}`)
      },
      async onAgentEvent(payload) {
        seen.push(`event:${payload.stream}`)
      },
    },
    {
      entries: [
        { kind: "assistant_text", text: "hello" },
        { kind: "tool_preview", text: "pwd => /tmp/project" },
      ],
      assistantTexts: ["hello"],
      toolPreviews: ["pwd => /tmp/project"],
    },
    [{ stream: "provider_response", data: { ok: true } }],
  )

  assert.deepEqual(seen, [
    "assistant:start",
    "assistant:hello",
    "tool:pwd => /tmp/project",
    "event:provider_response",
  ])
})

test("buildHostProjectionEnvelope pairs transcript projection with a host-shaped result", () => {
  const envelope = buildHostProjectionEnvelope({
    transcriptSource: {
      transcript: {
        schemaVersion: "bb.session_transcript.v1",
        sessionId: "session-1",
        items: [{ kind: "assistant_message", visibility: "model", content: { text: "hello" } }],
      },
    },
    result: { ok: true },
  })

  assert.deepEqual(envelope, {
    transcript: {
      entries: [{ kind: "assistant_text", text: "hello" }],
      assistantTexts: ["hello"],
      toolPreviews: [],
    },
    result: { ok: true },
  })
})

test("buildHostResultMeta produces reusable host-facing result metadata", () => {
  assert.deepEqual(
    buildHostResultMeta({
      sessionId: "session-1",
      provider: "openai",
      model: "openai/gpt-5.4-mini",
      stopReason: "completed",
      usage: { output_tokens: 32 },
    }),
    {
      durationMs: 0,
      agentMeta: {
        sessionId: "session-1",
        provider: "openai",
        model: "openai/gpt-5.4-mini",
        usage: { output_tokens: 32 },
      },
      stopReason: "completed",
    },
  )
})

test("resolveProviderHostTurnView fills missing projection output and state", () => {
  const resolved = resolveProviderHostTurnView({
    result: {
      supportClaim: supportedClaim,
      turn: {
        supportClaim: supportedClaim,
        projectionProfile: { id: "host_callbacks", summary: "Host callbacks" },
        runContextId: "run-1",
        transcript: {
          schemaVersion: "bb.session_transcript.v1",
          sessionId: "session-1",
          items: [],
        },
        events: [],
        coordinationInspection: {
          signals: [],
          review_verdicts: [],
          directives: [],
          latest_signal_by_code: {},
          unresolved_interventions: [],
          resolved_interventions: [],
        },
        providerTurn: undefined,
        driverTurn: undefined,
        unsupportedCase: undefined,
      },
      projectionOutput: null,
      projectionState: null,
    },
    fallbackProjectionOutput: [],
    fallbackProjectionState: {
      lastMessageId: "run-1",
      transcriptDigest: null,
      turnCount: 0,
    },
  })

  assert.deepEqual(resolved.projectionOutput, [])
  assert.deepEqual(resolved.projectionState, {
    lastMessageId: "run-1",
    transcriptDigest: null,
    turnCount: 0,
  })
})

test("normalizeHostManagedTranscript wraps host-owned transcript items in the canonical envelope", () => {
  const transcript = normalizeHostManagedTranscript("session-1", [
    {
      kind: "assistant_message",
      visibility: "model",
      content: { text: "hello" },
    },
  ])

  assert.deepEqual(transcript, {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: "session-1",
    items: [
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: "hello" },
      },
    ],
  })
})
