import test from "node:test"
import assert from "node:assert/strict"

import { createHostKit } from "../src/index.js"

test("createHostKit returns a stable classify/invoke surface", async () => {
  const hostKit = createHostKit<{ id: string }, { ok: boolean }, { source: string }>({
    id: "test.hostkit",
    classify(request) {
      return {
        mode: "supported",
        request,
        unsupportedFields: [],
        supportClaim: {
          level: "supported",
          summary: "Supported",
          executionProfileId: "trusted_local",
          fallbackAvailable: false,
          unsupportedFields: [],
          evidenceMode: "replay_strict",
        },
      }
    },
    async invoke(request) {
      return {
        mode: "supported",
        result: { ok: true },
        invocation: { source: request.id },
        supportClaim: {
          level: "supported",
          summary: "Supported",
          executionProfileId: "trusted_local",
          fallbackAvailable: false,
          unsupportedFields: [],
          evidenceMode: "replay_strict",
        },
      }
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
      fallbackAvailable: false,
      unsupportedFields: [],
      evidenceMode: "replay_strict",
    },
  })
})
