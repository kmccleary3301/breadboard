import test from "node:test"
import assert from "node:assert/strict"

import { buildExecutionDriverUnsupportedCase, isPlacementCompatible } from "../src/index.js"

test("execution driver helpers classify placement compatibility", () => {
  assert.equal(
    isPlacementCompatible(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-1",
        security_tier: "single_tenant",
        isolation_class: "oci",
        secret_mode: "ref_only",
        evidence_mode: "replay_strict",
      },
      "local_oci",
    ),
    true,
  )
  assert.equal(
    isPlacementCompatible(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-2",
        security_tier: "trusted_dev",
        isolation_class: "none",
        secret_mode: "ref_only",
        evidence_mode: "minimal",
      },
      "local_oci",
    ),
    false,
  )
})

test("execution driver helpers build unsupported-case records", () => {
  const unsupported = buildExecutionDriverUnsupportedCase({
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-3",
      security_tier: "multi_tenant",
      isolation_class: "microvm",
      secret_mode: "ref_only",
      evidence_mode: "audit_full",
    },
    placementClass: "local_microvm",
    fallbackAllowed: true,
  })
  assert.equal(unsupported.schema_version, "bb.unsupported_case.v1")
  assert.equal(unsupported.required_capability_id, "cap-3")
})
