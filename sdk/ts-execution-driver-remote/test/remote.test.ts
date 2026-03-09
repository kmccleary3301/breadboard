import test from "node:test"
import assert from "node:assert/strict"

import type { ExecutionCapabilityV1 } from "@breadboard/kernel-contracts"
import { buildExecutionDriverUnsupportedCase } from "@breadboard/execution-drivers"
import { buildRemoteSandboxRequest, chooseRemotePlacement, makeRemoteExecutionDriver } from "../src/index.js"

const remoteCapability: ExecutionCapabilityV1 = {
  schema_version: "bb.execution_capability.v1",
  capability_id: "cap:remote:1",
  security_tier: "multi_tenant",
  isolation_class: "remote_service",
  allow_read_paths: [],
  allow_write_paths: [],
  allow_net_hosts: ["api.openai.com"],
  allow_run_programs: ["python"],
  allow_env_keys: [],
  secret_mode: "ref_only",
  tty_mode: "optional",
  resource_budget: null,
  evidence_mode: "audit_full",
}

test("remote driver chooses delegated placement from capability", () => {
  assert.equal(chooseRemotePlacement(remoteCapability), "remote_worker")
})

test("remote driver builds a remote sandbox request", () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:1",
    capability: remoteCapability,
    command: ["python", "worker.py"],
    workspaceRef: "workspace://session/1",
    imageRef: "breadboard/worker:latest",
  })
  assert.equal(request.placement_class, "remote_worker")
  assert.equal(request.image_ref, "breadboard/worker:latest")
  assert.deepEqual(request.command, ["python", "worker.py"])
})

test("remote driver can execute through an injected adapter", async () => {
  const driver = makeRemoteExecutionDriver(async (request) => ({
    schema_version: "bb.sandbox_result.v1",
    request_id: request.request_id,
    status: "completed",
    placement_id: "remote:place:1",
    stdout_ref: "artifact://remote/stdout/1",
    stderr_ref: "artifact://remote/stderr/1",
    artifact_refs: ["artifact://remote/report/1"],
    side_effect_digest: "sha256:remote1",
    usage: { wall_ms: 15 },
    evidence_refs: ["evidence://remote/1"],
  }))
  const request = driver.buildSandboxRequest!({
    requestId: "req:remote:2",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:2",
      placement_class: "remote_worker",
      runtime_id: "breadboard.remote",
      capability_id: remoteCapability.capability_id,
      satisfied_security_tier: remoteCapability.security_tier,
      downgrade_reason: null,
      metadata: {},
    },
    command: ["python", "worker.py"],
    workspaceRef: "workspace://session/2",
    imageRef: "breadboard/worker:latest",
  })
  const result = await driver.execute!(request)
  assert.equal(result.status, "completed")
  assert.equal(result.placement_id, "remote:place:1")
})

test("unsupported case helper still models delegated gaps honestly", () => {
  const unsupported = buildExecutionDriverUnsupportedCase({
    capability: remoteCapability,
    placementClass: "delegated_microvm",
    fallbackAllowed: true,
    fallbackTaken: false,
  })
  assert.equal(unsupported.reason_code, "unsupported_execution_driver")
  assert.equal(unsupported.unavailable_placement, "delegated_microvm")
})
