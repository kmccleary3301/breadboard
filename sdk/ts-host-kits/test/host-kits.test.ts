import test from "node:test"
import assert from "node:assert/strict"

import {
  buildFallbackHostKitInvocation,
  buildSupportedHostKitInvocation,
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
