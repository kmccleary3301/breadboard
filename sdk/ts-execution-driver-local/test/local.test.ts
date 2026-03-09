import test from "node:test"
import assert from "node:assert/strict"

import { buildLocalProcessSandboxRequest, chooseTrustedLocalPlacement, trustedLocalExecutionDriver } from "../src/index.js"

test("trusted local driver chooses inline vs local process cleanly", () => {
  assert.equal(
    chooseTrustedLocalPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-1",
      security_tier: "trusted_dev",
      isolation_class: "none",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    }),
    "inline_ts",
  )
  assert.equal(
    chooseTrustedLocalPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-2",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    }),
    "local_process",
  )
})

test("trusted local driver can build a local-process sandbox request", () => {
  const request = buildLocalProcessSandboxRequest({
    requestId: "sandbox-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-3",
      security_tier: "trusted_dev",
      isolation_class: "process",
      allow_net_hosts: ["api.openai.com"],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    command: ["bash", "-lc", "echo hi"],
    workspaceRef: "workspace://repo/main",
  })
  assert.equal(request.placement_class, "local_process")
  assert.equal(request.command[0], "bash")
  assert.equal(trustedLocalExecutionDriver.supportsCapability({
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-3",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }, "local_process"), true)
})
