import test from "node:test"
import assert from "node:assert/strict"
import type { ExecutionCapabilityV1 } from "@breadboard/kernel-contracts"

import {
  buildExecutionDriverEvidenceExpectation,
  buildExecutionDriverSideEffectExpectation,
  buildExecutionDriverUnsupportedCase,
  buildPlannedExecution,
  isPlacementCompatible,
  selectTerminalSessionDriver,
} from "../src/index.js"

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

test("execution driver helpers derive side-effect and evidence expectations", () => {
  const sideEffects = buildExecutionDriverSideEffectExpectation("local_oci_gvisor")
  assert.deepEqual(sideEffects, {
    filesystem_scope: "container_scoped",
    network_scope: "backend_policy",
    process_scope: "sandbox_process",
    persistence_scope: "backend_artifacts",
  })

  const evidence = buildExecutionDriverEvidenceExpectation({
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-4",
      security_tier: "single_tenant",
      isolation_class: "gvisor",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placementClass: "local_oci_gvisor",
  })
  assert.equal(evidence.require_stdout_ref, true)
  assert.equal(evidence.require_side_effect_digest, true)
  assert.equal(evidence.require_evidence_refs, true)
})

test("execution driver helpers can build a planned execution record", () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-5",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
    allow_net_hosts: [] as string[],
  }
  const placement = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: "cap-5",
  } as const
  const plan = buildPlannedExecution({
    capability,
    placement,
    requestId: "req-1",
    command: ["python", "-V"],
    workspaceRef: "/tmp/workspace",
    drivers: [
      {
        driverId: "local",
        supportedPlacements: ["local_process"],
        supportsCapability: () => true,
        buildSandboxRequest: ({ requestId, capability, command, workspaceRef }) => ({
          schema_version: "bb.sandbox_request.v1",
          request_id: requestId,
          capability_id: capability.capability_id,
          placement_class: "local_process",
          workspace_ref: workspaceRef ?? null,
          rootfs_ref: null,
          image_ref: null,
          snapshot_ref: null,
          command,
          network_policy: { allow: [] },
          secret_refs: [],
          timeout_seconds: null,
          evidence_mode: capability.evidence_mode,
          metadata: { driver: "local" },
        }),
      },
    ],
  })
  assert.ok(plan)
  assert.equal(plan?.driver.driverId, "local")
  assert.equal(plan?.sandboxRequest?.placement_class, "local_process")
  assert.equal(plan?.evidenceExpectation.require_evidence_refs, true)
})

test("execution driver helpers can select a terminal-capable driver", () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-term-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const driver = selectTerminalSessionDriver({
    capability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: capability.capability_id,
    },
    drivers: [
      {
        driverId: "local-term",
        supportedPlacements: ["local_process"],
        supportsCapability: () => true,
        supportsTerminalSessions: () => true,
      },
    ],
  })
  assert.equal(driver?.driverId, "local-term")
})
