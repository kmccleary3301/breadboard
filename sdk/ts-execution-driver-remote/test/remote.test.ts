import test from "node:test"
import assert from "node:assert/strict"

import type { ExecutionCapabilityV1 } from "@breadboard/kernel-contracts"
import { buildExecutionDriverUnsupportedCase } from "@breadboard/execution-drivers"
import {
  buildRemoteExecutionRequestEnvelope,
  buildRemoteSandboxRequest,
  chooseRemotePlacement,
  executeRemoteSandboxRequest,
  makeRemoteExecutionDriver,
} from "../src/index.js"

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

test("remote driver builds an explicit remote execution envelope", () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:env",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const envelope = buildRemoteExecutionRequestEnvelope({
    request,
    metadata: { route: "primary-remote" },
  })
  assert.equal(envelope.schema_version, "bb.remote_execution_request.v1")
  assert.equal(envelope.request.request_id, "req:remote:env")
  assert.equal(envelope.metadata?.route, "primary-remote")
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

test("remote driver can execute through a fetch-backed HTTP adapter", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:http",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  let seenUrl = ""
  let seenHeaders: Record<string, string> = {}
  let seenBody = ""
  const result = await executeRemoteSandboxRequest(request, {
    endpointUrl: "https://example.test/remote-exec",
    headers: { authorization: "Bearer test-token" },
    fetchImpl: async (input, init) => {
      seenUrl = String(input)
      seenHeaders = (init?.headers as Record<string, string>) ?? {}
      seenBody = String(init?.body ?? "")
      return {
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: "bb.remote_execution_response.v1",
          result: {
            schema_version: "bb.sandbox_result.v1",
            request_id: request.request_id,
            status: "completed",
            placement_id: "remote:http:1",
            stdout_ref: "artifact://remote/stdout/http",
            stderr_ref: "artifact://remote/stderr/http",
            artifact_refs: [],
            side_effect_digest: "sha256:remotehttp",
            usage: { wall_ms: 21 },
            evidence_refs: ["evidence://remote/http/1"],
            error: null,
          },
        }),
      } as Response
    },
  })
  assert.equal(seenUrl, "https://example.test/remote-exec")
  assert.equal(seenHeaders.authorization, "Bearer test-token")
  assert.ok(seenBody.includes("\"schema_version\":\"bb.remote_execution_request.v1\""))
  assert.equal(result.placement_id, "remote:http:1")
})

test("remote driver can be constructed from HTTP options alone", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:http:driver",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/driver-http",
    fetchImpl: async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: "bb.sandbox_result.v1",
          request_id: request.request_id,
          status: "completed",
          placement_id: "remote:http:driver",
          stdout_ref: null,
          stderr_ref: null,
          artifact_refs: [],
          side_effect_digest: "sha256:remotehttpdriver",
          usage: null,
          evidence_refs: [],
          error: null,
        }),
      }) as Response,
  })
  const result = await driver.execute!(request)
  assert.equal(result.status, "completed")
  assert.equal(result.placement_id, "remote:http:driver")
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
