import test from "node:test"
import assert from "node:assert/strict"

import {
  buildOciRuntimeInvocation,
  buildOciSandboxRequest,
  chooseOciPlacement,
  executeOciSandboxRequest,
  makeOciExecutionDriver,
  ociExecutionDriver,
} from "../src/index.js"

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
  const built = ociExecutionDriver.buildSandboxRequest?.({
    requestId: "oci-2",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-4",
      security_tier: "single_tenant",
      isolation_class: "oci",
      allow_net_hosts: [],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-oci-1",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-oci-4",
    },
    command: ["ruff", "check", "."],
    workspaceRef: "workspace://repo/main",
    imageRef: "docker://breadboard/base:latest",
  })
  assert.equal(built?.placement_class, "local_oci")
})

test("oci driver can build a concrete runtime invocation", () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-runtime-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-runtime-1",
      security_tier: "single_tenant",
      isolation_class: "gvisor",
      allow_net_hosts: [],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    command: ["npm", "run", "lint"],
    workspaceRef: "/tmp/workspace",
    imageRef: "ghcr.io/example/lint:latest",
  })
  const invocation = buildOciRuntimeInvocation(request)
  assert.equal(invocation.runtimeCommand, "docker")
  assert.ok(invocation.runtimeArgs.includes("--runtime=runsc"))
  assert.ok(invocation.runtimeArgs.includes("ghcr.io/example/lint:latest"))
})

test("oci driver can execute through an injected runtime adapter", async () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-exec-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-exec-1",
      security_tier: "single_tenant",
      isolation_class: "oci",
      allow_net_hosts: [],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    command: ["ruff", "check", "."],
    workspaceRef: "/tmp/workspace",
    imageRef: "ghcr.io/example/ruff:latest",
  })
  const result = await executeOciSandboxRequest(request, {
    commandExecutor: async ({ runtimeCommand, runtimeArgs }) => ({
      exitCode: runtimeCommand === "docker" && runtimeArgs.includes("ghcr.io/example/ruff:latest") ? 0 : 1,
      stdout: "oci ok",
      stderr: "",
    }),
  })
  assert.equal(result.status, "completed")
  assert.ok(result.stdout_ref?.startsWith("file://"))
  assert.ok(result.side_effect_digest?.startsWith("sha256:"))
})

test("oci driver can manage terminal sessions through an injected adapter", async () => {
  const driver = makeOciExecutionDriver({
    async startSession({ descriptor }) {
      return {
        outputDeltas: [
          {
            schema_version: "bb.terminal_output_delta.v1",
            terminal_session_id: descriptor.terminal_session_id,
            startup_call_id: descriptor.startup_call_id ?? null,
            causing_call_id: null,
            stream: "stdout",
            chunk_b64: Buffer.from("oci ready\n", "utf8").toString("base64"),
            chunk_seq: 0,
          },
        ],
      }
    },
    async interactSession({ descriptor, interaction }) {
      return {
        outputDeltas: [],
        end: {
          schema_version: "bb.terminal_session_end.v1",
          terminal_session_id: descriptor.terminal_session_id,
          startup_call_id: descriptor.startup_call_id ?? null,
          causing_call_id: interaction.causing_call_id ?? null,
          terminal_state: "completed",
          exit_code: 0,
          duration_ms: 10,
          artifact_refs: [],
          evidence_refs: [],
        },
      }
    },
  })
  const started = await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-1",
    command: ["bash", "-lc", "sleep 1"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-oci-1",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-oci-1",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-term-oci-1",
    },
  })
  assert.equal(started?.outputDeltas.length, 1)
  const interacted = await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-1",
    interactionKind: "poll",
    causingCallId: "call-1",
  })
  assert.equal(interacted?.end?.terminal_state, "completed")
})
