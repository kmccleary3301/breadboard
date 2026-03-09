import test from "node:test"
import assert from "node:assert/strict"

import { buildOciSandboxRequest, chooseOciPlacement, ociExecutionDriver } from "../src/index.js"

test("oci driver chooses placement from capability isolation class", () => {
  assert.equal(
    chooseOciPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-1",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    }),
    "local_oci",
  )
  assert.equal(
    chooseOciPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-2",
      security_tier: "shared_host",
      isolation_class: "gvisor",
      secret_mode: "ref_only",
      evidence_mode: "audit_full",
    }),
    "local_oci_gvisor",
  )
})

test("oci driver can build an OCI sandbox request", () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-3",
      security_tier: "shared_host",
      isolation_class: "gvisor",
      allow_net_hosts: ["registry.example.com"],
      secret_mode: "ref_only",
      evidence_mode: "audit_full",
    },
    command: ["node", "script.mjs"],
    workspaceRef: "workspace://repo/main",
    imageRef: "docker://breadboard/base:latest",
  })
  assert.equal(request.placement_class, "local_oci_gvisor")
  assert.equal(request.image_ref, "docker://breadboard/base:latest")
  assert.equal(
    ociExecutionDriver.supportsCapability(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-oci-3",
        security_tier: "shared_host",
        isolation_class: "gvisor",
        secret_mode: "ref_only",
        evidence_mode: "audit_full",
      },
      "local_oci_gvisor",
    ),
    true,
  )
})
