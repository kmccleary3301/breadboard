import test from "node:test"
import assert from "node:assert/strict"

import {
  buildFallbackHostKitInvocation,
  buildSupportedHostKitInvocation,
  createProviderHostSession,
  createHostKit,
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
})
