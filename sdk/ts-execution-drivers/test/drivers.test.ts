import { createHash } from "node:crypto"
import { chmod, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises"
import { homedir, tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import assert from "node:assert/strict"
import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  SandboxRequestV1,
  SandboxResultV1,
  TerminalCleanupResultV1,
  TerminalSessionDescriptorV1,
} from "@breadboard/kernel-contracts"
import { makeTrustedLocalExecutionDriver } from "../../ts-execution-driver-local/src/index.js"
import { makeConfiguredOciExecutionDriver } from "../../ts-execution-driver-oci/src/index.js"

import {
  buildExecutionDriverEvidenceExpectation,
  buildAndPersistCanonicalSandboxEvidence,
  buildCanonicalSandboxSideEffectDigest,
  buildExecutionDriverSideEffectExpectation,
  buildExecutionDriverUnsupportedCase,
  buildPlannedExecution,
  createExecutionWorld,
  canonicalProcessExitCode,
  canonicalSandboxArtifactRoot,
  canonicalSandboxArtifactUri,
  persistCanonicalSandboxArtifact,
  isPlacementCompatible,
  selectTerminalSessionDriver,
  type ExecutionDriverV1,
  type ExecutionLivenessEvidenceV1,
  type TerminalSessionDriverV1,
} from "../src/index.js"

test("canonical process exit codes normalize POSIX signals", () => {
  assert.equal(canonicalProcessExitCode(0, 0), 0)
  assert.equal(canonicalProcessExitCode(0, 9), 137)
  assert.equal(canonicalProcessExitCode(null, 15), 143)
  assert.equal(canonicalProcessExitCode(null, null), null)
  assert.throws(
    () => canonicalProcessExitCode(null, -1),
    /non-negative safe integer/,
  )
})

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
          command: [command[0] ?? "", ...command.slice(1)],
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

test("planned execution rejects programs outside the capability allowlist", () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-program-policy",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
    allow_run_programs: ["python"],
  }
  assert.throws(
    () =>
      buildPlannedExecution({
        capability,
        placement: {
          schema_version: "bb.execution_placement.v1",
          placement_id: "place-program-policy",
          placement_class: "local_process",
          runtime_id: "local",
          capability_id: capability.capability_id,
        },
        drivers: [
          {
            driverId: "local",
            supportedPlacements: ["local_process"],
            supportsCapability: () => true,
          },
        ],
        requestId: "req-program-policy",
        command: ["node", "worker.js"],
      }),
    /program "node" is not allowed by capability cap-program-policy/,
  )
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

test("execution world selects one adapter and never falls back after execution starts", async () => {
  const calls: string[] = []
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  } as const
  const makeDriver = (driverId: string, status: "completed" | "failed"): TerminalSessionDriverV1 => ({
    driverId,
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async (request) => {
      calls.push(driverId)
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: request.request_id,
        status,
        placement_id: `${driverId}:${request.request_id}`,
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: null,
        evidence_refs: [],
        usage: null,
        error: status === "failed" ? { message: "first adapter failed" } : null,
      }
    },
  })
  const world = createExecutionWorld({
    drivers: [makeDriver("oci", "completed"), makeDriver("local-process", "failed")],
  })
  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-world-1",
    command: ["printf", "done"],
  })
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.deepEqual(calls, ["local-process"])
  assert.equal(result.livenessEvidence?.executionStarted, true)
})

test("execution world rejects duplicate active request IDs before a second launch", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-duplicate",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-duplicate",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  let executeCalls = 0
  let terminateCalls = 0
  let resolveExecution: ((result: SandboxResultV1) => void) | undefined
  const driver: TerminalSessionDriverV1 = {
    driverId: "local-process",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability: requestedCapability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: requestedCapability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: requestedCapability.evidence_mode,
      metadata: {},
    }),
    execute: (request) => {
      executeCalls += 1
      return new Promise<SandboxResultV1>((resolve) => {
        resolveExecution = resolve
      })
    },
    terminate: async () => {
      terminateCalls += 1
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const operation = {
    kind: "sandbox" as const,
    capability,
    placement,
    requestId: "req-world-duplicate",
    command: ["printf", "done"],
  }

  const first = world.execute(operation)
  await assert.rejects(() => world.execute(operation), /already active/)
  assert.equal(executeCalls, 1)
  assert.equal(terminateCalls, 0)
  resolveExecution?.({
    schema_version: "bb.sandbox_result.v1",
    request_id: operation.requestId,
    status: "completed",
    placement_id: placement.placement_id,
    stdout_ref: null,
    stderr_ref: null,
    artifact_refs: [],
    side_effect_digest: null,
    evidence_refs: [],
    usage: null,
    error: null,
  })
  const completed = await first
  assert.equal(completed.kind, "sandbox")
  assert.equal(completed.sandboxResult?.status, "completed")
})

test("execution world retains timed-out request ID until termination settles", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-unobserved-termination",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-unobserved-termination",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  let resolveTermination: (() => void) | undefined
  const driver: TerminalSessionDriverV1 = {
    driverId: "local-process",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability: requestedCapability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: requestedCapability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: requestedCapability.evidence_mode,
      metadata: {},
    }),
    execute: () => new Promise<SandboxResultV1>(() => {}),
    terminate: () =>
      new Promise<void>((resolve) => {
        resolveTermination = resolve
      }),
  }
  const world = createExecutionWorld({
    drivers: [driver],
    terminationGraceMs: 1,
  })
  const operation = {
    kind: "sandbox" as const,
    capability,
    placement,
    requestId: "req-world-unobserved-termination",
    command: ["sleep", "60"],
    deadlineMs: 1,
  }

  const timedOut = await world.execute(operation)
  assert.equal(timedOut.kind, "sandbox")
  assert.equal(timedOut.livenessEvidence.terminationObserved, false)
  await assert.rejects(() => world.execute(operation), /already active/)

  resolveTermination?.()
  await new Promise((resolve) => setTimeout(resolve, 0))
  const preAborted = new AbortController()
  preAborted.abort("cancelled")
  const retry = await world.execute({
    ...operation,
    signal: preAborted.signal,
  })
  assert.equal(retry.kind, "sandbox")
  assert.equal(retry.livenessEvidence.executionStarted, false)
})

test("execution world retains a timed-out request until an unterminated execution settles", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-no-termination",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-no-termination",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  let resolveExecution: ((result: SandboxResultV1) => void) | undefined
  const driver: ExecutionDriverV1 = {
    driverId: "unterminated",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability: requestedCapability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: requestedCapability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: null,
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: requestedCapability.evidence_mode,
      metadata: {},
    }),
    execute: () =>
      new Promise<SandboxResultV1>((resolve) => {
        resolveExecution = resolve
      }),
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const operation = {
    kind: "sandbox" as const,
    capability,
    placement,
    requestId: "req-world-no-termination",
    command: ["sleep", "60"],
    deadlineMs: 1,
  }

  const timedOut = await world.execute(operation)
  assert.equal(timedOut.kind, "sandbox")
  if (timedOut.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(timedOut.sandboxResult?.status, "timed_out")
  assert.equal(timedOut.livenessEvidence.terminationObserved, false)
  await assert.rejects(() => world.execute(operation), /already active/)

  resolveExecution?.({
    schema_version: "bb.sandbox_result.v1",
    request_id: operation.requestId,
    status: "completed",
    placement_id: placement.placement_id,
    stdout_ref: null,
    stderr_ref: null,
    artifact_refs: [],
    side_effect_digest: null,
    evidence_refs: [],
    usage: null,
    error: null,
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  const preAborted = new AbortController()
  preAborted.abort("cancelled")
  const retry = await world.execute({ ...operation, signal: preAborted.signal })
  assert.equal(retry.kind, "sandbox")
  if (retry.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(retry.livenessEvidence.executionStarted, false)
})


test("execution world enforces timeout notification and bounded adapter termination", async () => {
  let timeoutNotified = false
  let terminateCalled = false
  let executeAborted = false
  let timeoutCallbackLiveness: ExecutionLivenessEvidenceV1 | undefined
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-timeout-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-timeout-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "local-process",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: (request, context) =>
      new Promise<SandboxResultV1>((resolve) => {
        context?.signal.addEventListener("abort", () => {
          executeAborted = true
        })
      }),
    terminate: (request, context) => {
      terminateCalled = true
      assert.equal(context.reason, "deadline")
    },
  }

  const world = createExecutionWorld({
    drivers: [driver],
    terminationGraceMs: 50,
  })

  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-timeout-1",
    command: ["sleep", "60"],
    deadlineMs: 20,
    onTimeout: ({ driverId, liveness }) => {
      timeoutNotified = true
      timeoutCallbackLiveness = liveness
      assert.equal(driverId, "local-process")
      assert.equal(liveness.state, "timed_out")
      assert.equal(liveness.terminationRequested, true)
      assert.equal(liveness.terminationObserved, true)
    },
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(timeoutNotified, true)
  assert.equal(terminateCalled, true)
  assert.equal(executeAborted, true)
  assert.equal(Boolean(timeoutCallbackLiveness), true)
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
  assert.equal(result.livenessEvidence.terminationObserved, (timeoutCallbackLiveness as ExecutionLivenessEvidenceV1 | undefined)?.terminationObserved)
  assert.equal(result.livenessEvidence.state, "timed_out")
})
test("execution world notifies timeout hook for adapter TimeoutError", async () => {
  let timeoutNotifications = 0
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-adapter-timeout",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-adapter-timeout",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "local-process",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability: inputCapability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: inputCapability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: inputCapability.evidence_mode,
      metadata: {},
    }),
    execute: async () => {
      const error = new Error("adapter timed out")
      error.name = "TimeoutError"
      throw error
    },
    terminate: async () => {},
  }
  const world = createExecutionWorld({ drivers: [driver] })

  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-adapter-timeout",
    command: ["sleep", "1"],
    onTimeout: ({ liveness }) => {
      timeoutNotifications++
      assert.equal(liveness.state, "timed_out")
      assert.equal(liveness.terminationRequested, true)
    },
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(timeoutNotifications, 1)
})
test("execution world exercises real local and OCI adapter paths with provider-neutral equality under mask", async () => {
  const localDriver = makeTrustedLocalExecutionDriver(async ({ command, cwd }) => ({
    exitCode: 0,
    stdout: "verification command passed\n",
    stderr: "",
  }))

  const ociDriver = makeConfiguredOciExecutionDriver({
    commandExecutor: async ({ runtimeCommand, runtimeArgs }) => ({
      exitCode: 0,
      stdout: "verification command passed\n",
      stderr: "",
    }),
    workspaceMountTarget: "/workspace",
  })

  const world = createExecutionWorld({ drivers: [localDriver, ociDriver] })

  const localCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-local-fixture",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const localPlacement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-local-fixture",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: localCap.capability_id,
  }

  const ociCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-fixture",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const ociPlacement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-fixture",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: ociCap.capability_id,
  }

  const localResult = await world.execute({
    kind: "sandbox",
    capability: localCap,
    placement: localPlacement,
    requestId: "w5-neutral-req",
    command: ["printf", "verification command passed\n"],
    workspaceRef: "/workspace",
  })

  const ociResult = await world.execute({
    kind: "sandbox",
    capability: ociCap,
    placement: ociPlacement,
    requestId: "w5-neutral-req",
    command: ["printf", "verification command passed\n"],
    workspaceRef: "/workspace",
    imageRef: "docker://breadboard/base:latest",
    driverIdHint: "oci",
  })

  assert.equal(localResult.kind, "sandbox")
  assert.equal(ociResult.kind, "sandbox")

  // Function to apply timestamp-only mask for provider-neutral event/liveness comparison
  const maskVolatileTimestamps = (liveness: typeof localResult.livenessEvidence) => {
    const { observedAtUtc, ...rest } = liveness
    return rest
  }

  assert.deepEqual(localResult.sandboxResult, ociResult.sandboxResult)

  // External liveness evidence is captured but separate from product payload
  assert.deepEqual(maskVolatileTimestamps(localResult.livenessEvidence), maskVolatileTimestamps(ociResult.livenessEvidence))
  assert.ok(localResult.livenessEvidence.observedAtUtc)
  assert.ok(ociResult.livenessEvidence.observedAtUtc)
  assert.equal(localResult.livenessEvidence.executionStarted, true)
  assert.equal(ociResult.livenessEvidence.executionStarted, true)
})

test("execution world handles synchronously and asynchronously throwing terminate without hanging", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-throw-term-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-throw-term-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const makeThrowingDriver = (driverId: string, throwMode: "sync" | "async" | "success"): TerminalSessionDriverV1 => ({
    driverId,
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async (_request, context) => {
      return new Promise((_resolve, reject) => {
        context?.signal.addEventListener("abort", () => {
          reject(context.signal.reason)
        })
      })
    },
    terminate: (request, _context) => {
      if (throwMode === "sync") {
        throw new Error("synchronous terminate failure")
      } else if (throwMode === "async") {
        return Promise.reject(new Error("asynchronous terminate failure"))
      } else {
        const stdoutRef = `sha256:${"0".repeat(64)}`
        const stderrRef = `sha256:${"1".repeat(64)}`
        const sideEffectDigest = buildCanonicalSandboxSideEffectDigest({
          command: request.command,
          status: "cancelled",
          exitCode: 143,
          stdoutRef,
          stderrRef,
        })
        return Promise.resolve({
          schema_version: "bb.sandbox_result.v1",
          request_id: request.request_id,
          status: "cancelled",
          stdout_ref: stdoutRef,
          stderr_ref: stderrRef,
          artifact_refs: [stdoutRef, stderrRef],
          side_effect_digest: sideEffectDigest,
          usage: { exit_code: 143 },
          evidence_refs: [sideEffectDigest],
          error: { reason: "execution_cancelled" },
        })
      }
    },
  })

  const worldSync = createExecutionWorld({ drivers: [makeThrowingDriver("local_sync", "sync")] })
  const syncResult = await worldSync.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-throw-sync",
    command: ["sleep", "10"],
    deadlineMs: 10,
    terminationGraceMs: 50,
  })
  assert.equal(syncResult.kind, "sandbox")
  assert.equal(syncResult.sandboxResult?.status, "timed_out")
  assert.equal(syncResult.livenessEvidence.state, "timed_out")
  assert.equal(syncResult.livenessEvidence.terminationRequested, true)
  assert.equal(syncResult.livenessEvidence.terminationObserved, false)
  const worldAsync = createExecutionWorld({ drivers: [makeThrowingDriver("local_async", "async")] })
  const abortController = new AbortController()
  const asyncPromise = worldAsync.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-throw-async",
    command: ["sleep", "10"],
    signal: abortController.signal,
    terminationGraceMs: 50,
  })
  await new Promise((r) => setTimeout(r, 10))
  abortController.abort(new Error("external abort"))
  const asyncResult = await asyncPromise
  assert.equal(asyncResult.kind, "sandbox")
  if (asyncResult.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(asyncResult.livenessEvidence.state, "cancelled")
  assert.equal(asyncResult.livenessEvidence.terminationRequested, true)
  assert.equal(asyncResult.livenessEvidence.terminationObserved, false)

  const worldSuccess = createExecutionWorld({ drivers: [makeThrowingDriver("local_success", "success")] })
  const successResult = await worldSuccess.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-throw-success",
    command: ["sleep", "10"],
    deadlineMs: 10,
    terminationGraceMs: 50,
  })
  assert.equal(successResult.kind, "sandbox")
  assert.equal(successResult.sandboxResult?.status, "timed_out")
  assert.equal(successResult.livenessEvidence.state, "timed_out")
  assert.equal(successResult.livenessEvidence.terminationRequested, true)
  assert.equal(successResult.livenessEvidence.terminationObserved, true)
  const translated = successResult.sandboxResult
  assert.ok(translated?.stdout_ref)
  assert.ok(translated?.stderr_ref)
  const translatedDigest = buildCanonicalSandboxSideEffectDigest({
    command: ["sleep", "10"],
    status: "timed_out",
    exitCode: 143,
    stdoutRef: translated.stdout_ref,
    stderrRef: translated.stderr_ref,
  })
  assert.equal(translated.side_effect_digest, translatedDigest)
  assert.deepEqual(translated.evidence_refs, [translatedDigest])
})

test("execution world correctly normalizes cancelled status with distinct execution_cancelled reason", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-cancel-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-cancel-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const driver = makeTrustedLocalExecutionDriver(async ({ signal }) => {
    return new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => {
        reject(new Error("aborted"))
      })
    })
  })
  const world = createExecutionWorld({ drivers: [driver] })
  const abortController = new AbortController()
  const promise = world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-cancel-norm",
    command: ["sleep", "60"],
    signal: abortController.signal,
  })
  await new Promise((r) => setTimeout(r, 10))
  abortController.abort(new Error("user cancelled"))
  const result = await promise
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "cancelled")
  assert.equal(result.sandboxResult?.error?.reason, "execution_cancelled")
  assert.equal(result.sandboxResult?.error?.message, "Execution was cancelled")
  assert.equal(result.livenessEvidence.state, "cancelled")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
})

test("execution world treats AbortSignal.timeout as a deadline", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-timeout-signal-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-timeout-signal-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const driver = makeTrustedLocalExecutionDriver(async ({ signal }) => {
    return new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new Error("aborted")))
    })
  })
  const world = createExecutionWorld({ drivers: [driver] })
  let timeoutNotifications = 0

  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-timeout-signal",
    command: ["sleep", "60"],
    signal: AbortSignal.timeout(10),
    onTimeout: () => {
      timeoutNotifications++
    },
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.sandboxResult?.error?.reason, "deadline_exceeded")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(timeoutNotifications, 1)
})


test("execution world with missing terminate function marks terminationObserved false", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-no-term-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-no-term-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const driverWithoutTerminate: TerminalSessionDriverV1 = {
    driverId: "no-terminate",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async (_req, context) =>
      new Promise((_res, rej) => {
        context?.signal.addEventListener("abort", () => rej(new Error("aborted")))
      }),
  }
  const world = createExecutionWorld({ drivers: [driverWithoutTerminate] })
  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-no-term",
    command: ["sleep", "10"],
    deadlineMs: 10,
  })
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, false)
})

test("execution world with stubborn adapter marks terminationObserved false when cleanup exceeds grace", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-stubborn-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-stubborn-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const stubbornDriver: TerminalSessionDriverV1 = {
    driverId: "stubborn",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => new Promise(() => {}),
    terminate: async () => new Promise(() => {}),
  }

  const world = createExecutionWorld({ drivers: [stubbornDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement,
    requestId: "req-stubborn-1",
    command: ["stubborn", "process"],
    deadlineMs: 10,
    terminationGraceMs: 30,
  })
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, false)
})

test("execution world groups mixed-owner terminal sessions and cleans up each owner exactly once", async () => {
  const localCleaned: string[] = []
  const ociCleaned: string[] = []

  const localDriver: TerminalSessionDriverV1 = {
    driverId: "local-term",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-local",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input): Promise<TerminalCleanupResultV1> => {
      localCleaned.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }

  const ociDriver: TerminalSessionDriverV1 = {
    driverId: "oci-term",
    supportedPlacements: ["local_oci"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-oci",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input): Promise<TerminalCleanupResultV1> => {
      ociCleaned.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [localDriver, ociDriver] })
  const localCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-local",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const localPlace: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-local",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: localCap.capability_id,
  }
  const ociCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const ociPlace: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: ociCap.capability_id,
  }

  await world.execute({
    kind: "terminal_start",
    capability: localCap,
    placement: localPlace,
    input: {
      terminalSessionId: "term-local-1",
      command: ["bash"],
      capability: localCap,
      placement: localPlace,
    },
  })
  await world.execute({
    kind: "terminal_start",
    capability: ociCap,
    placement: ociPlace,
    input: {
      terminalSessionId: "term-oci-1",
      command: ["bash"],
      capability: ociCap,
      placement: ociPlace,
    },
  })

  // Mixed cleanup by sessionIds:
  const mixedResult = await world.execute({
    kind: "terminal_cleanup",
    capability: localCap,
    placement: localPlace,
    input: {
      cleanupId: "clean-mixed-1",
      scope: "filtered",
      sessionIds: ["term-local-1", "term-oci-1"],
    },
  })
  assert.equal(mixedResult.kind, "terminal_cleanup")
  assert.deepEqual(mixedResult.result?.cleaned_session_ids.sort(), ["term-local-1", "term-oci-1"])
  assert.deepEqual(localCleaned, ["term-local-1"])
  assert.deepEqual(ociCleaned, ["term-oci-1"])
})

test("execution world scope:all cleans only the placement-selected driver's sessions", async () => {
  const localCleaned: string[] = []
  let rejectingDriverCalled = false
  const localDriver: TerminalSessionDriverV1 = {
    driverId: "local-term-all",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-local",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      localCleaned.push("term-local-all-1")
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: ["term-local-all-1"],
        failed_session_ids: [],
      }
    },
  }

  const rejectingDriver: TerminalSessionDriverV1 = {
    driverId: "reject-term-all",
    supportedPlacements: ["local_oci"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-oci",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async () => {
      rejectingDriverCalled = true
      throw new Error("wrong-profile cleanup must not run")
    },
  }

  const world = createExecutionWorld({ drivers: [localDriver, rejectingDriver] })
  const localCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-local",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const localPlace: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-local",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: localCap.capability_id,
  }
  const ociCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const ociPlace: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: ociCap.capability_id,
  }

  await world.execute({
    kind: "terminal_start",
    capability: localCap,
    placement: localPlace,
    input: {
      terminalSessionId: "term-local-all-1",
      command: ["bash"],
      capability: localCap,
      placement: localPlace,
    },
  })
  await world.execute({
    kind: "terminal_start",
    capability: ociCap,
    placement: ociPlace,
    input: {
      terminalSessionId: "term-reject-all-1",
      command: ["bash"],
      capability: ociCap,
      placement: ociPlace,
    },
  })

  const allResult = await world.execute({
    kind: "terminal_cleanup",
    capability: localCap,
    placement: localPlace,
    input: {
      cleanupId: "clean-all-1",
      scope: "all",
    },
  })

  assert.equal(allResult.kind, "terminal_cleanup")
  assert.deepEqual(allResult.result?.cleaned_session_ids, ["term-local-all-1"])
  assert.deepEqual(allResult.result?.failed_session_ids, [])
  assert.deepEqual(localCleaned, ["term-local-all-1"])
  assert.equal(rejectingDriverCalled, false)
})
test("execution world scope:all omits sessions owned by a nonselected driver", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-profile-cleanup",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-profile-cleanup",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const selectedCleanupInputs: string[][] = []
  const selectedDriver: TerminalSessionDriverV1 = {
    driverId: "selected-cleanup-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    cleanupTerminalSessions: async (input) => {
      selectedCleanupInputs.push([...(input.sessionIds ?? [])])
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }
  const foreignOwner: TerminalSessionDriverV1 = {
    driverId: "foreign-owner",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: capability.capability_id,
        placement_id: placement.placement_id,
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      },
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [selectedDriver, foreignOwner] })
  await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    driverId: foreignOwner.driverId,
    input: { terminalSessionId: "term-foreign-owner", command: ["bash"] },
  })
  const result = await world.execute({
    kind: "terminal_cleanup",
    capability,
    placement,
    input: { cleanupId: "cleanup-selected-profile", scope: "all" },
  })
  assert.equal(result.kind, "terminal_cleanup")
  assert.equal(result.driverId, selectedDriver.driverId)
  assert.deepEqual(selectedCleanupInputs, [[]])
  assert.deepEqual(result.result?.cleaned_session_ids, [])
  assert.deepEqual(result.result?.failed_session_ids, [])
})

test("execution world scope:single with multiple sessionIds cleans only the first session ID and leaves others active", async () => {
  const cleaned: string[] = []
  const localDriver: TerminalSessionDriverV1 = {
    driverId: "local-term-single",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-local",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      assert.equal(input.scope, "single")
      assert.deepEqual(input.sessionIds, ["term-first"])
      cleaned.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: "single",
        cleaned_session_ids: input.sessionIds ?? [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [localDriver] })
  const localCap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-local",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const localPlace: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-local",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: localCap.capability_id,
  }

  await world.execute({
    kind: "terminal_start",
    capability: localCap,
    placement: localPlace,
    input: {
      terminalSessionId: "term-first",
      command: ["bash"],
      capability: localCap,
      placement: localPlace,
    },
  })
  await world.execute({
    kind: "terminal_start",
    capability: localCap,
    placement: localPlace,
    input: {
      terminalSessionId: "term-second",
      command: ["bash"],
      capability: localCap,
      placement: localPlace,
    },
  })

  const singleResult = await world.execute({
    kind: "terminal_cleanup",
    capability: localCap,
    placement: localPlace,
    input: {
      cleanupId: "clean-single-1",
      scope: "single",
      sessionIds: ["term-first", "term-second"],
    },
  })

  assert.equal(singleResult.kind, "terminal_cleanup")
  assert.equal(singleResult.result?.scope, "single")
  assert.deepEqual(singleResult.result?.cleaned_session_ids, ["term-first"])
  assert.deepEqual(cleaned, ["term-first"])
})

test("buildExecutionDriverUnsupportedCase preserves caller summary precedence while keeping typed reasonCode", () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-summary",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const customSummary = "Custom secret-safe adapter summary with detailed diagnostics"
  const unsupported = buildExecutionDriverUnsupportedCase({
    capability,
    placementClass: "local_process",
    summary: customSummary,
    reasonCode: "custom_adapter_error",
  })
  assert.equal(unsupported.summary, customSummary)
  assert.equal(unsupported.reason_code, "custom_adapter_error")
  assert.equal(unsupported.unavailable_placement, "local_process")
})

test("pre-aborted operation returns cancelled with executionStarted:false and does not invoke driver.terminate", async () => {
  let terminateCalled = false
  let executeCalled = false
  const driver: ExecutionDriverV1 = {
    driverId: "pre-abort-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => {
      executeCalled = true
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: "req-pre-abort",
        status: "completed",
        placement_id: "place-1",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: "sha256:preabort",
        usage: null,
        evidence_refs: [],
        error: null,
      }
    },
    terminate: async () => {
      terminateCalled = true
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pre-abort",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const abortController = new AbortController()
  abortController.abort(new Error("caller cancelled prior to execution"))
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-pre-abort",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: cap.capability_id,
    },
    requestId: "req-pre-abort-1",
    command: ["echo", "unreachable"],
    signal: abortController.signal,
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.ok(result.sandboxResult)
  assert.equal(result.sandboxResult.status, "cancelled")
  assert.equal(result.livenessEvidence.executionStarted, false)
  assert.equal(result.livenessEvidence.terminationRequested, false)
  assert.equal(result.livenessEvidence.terminationObserved, false)
  assert.equal(executeCalled, false)
  assert.equal(terminateCalled, false)
})
test("pre-expired deadlineMs=0 returns timed_out with executionStarted:false, invokes onTimeout, and does not invoke driver.execute", async () => {
  let executeCalled = false
  let timeoutNotified = false
  const driver: ExecutionDriverV1 = {
    driverId: "zero-deadline-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => {
      executeCalled = true
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: "req-zero-deadline",
        status: "completed",
        placement_id: "place-zero",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: "sha256:zero",
        usage: null,
        evidence_refs: [],
        error: null,
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-zero-deadline",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-zero-deadline",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: cap.capability_id,
    },
    requestId: "req-zero-deadline-1",
    command: ["echo", "zero"],
    deadlineMs: 0,
    onTimeout: () => {
      timeoutNotified = true
    },
  })
  assert.equal(executeCalled, false)
  assert.equal(timeoutNotified, true)
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.ok(result.sandboxResult)
  assert.equal(result.sandboxResult.status, "timed_out")
  assert.equal(result.sandboxResult.error?.reason, "deadline_exceeded")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.livenessEvidence.executionStarted, false)
  assert.equal(result.livenessEvidence.terminationRequested, false)
  assert.equal(result.livenessEvidence.terminationObserved, false)
})

test("undefined adapter result without deadline does not hang and resolves with normalized failure", async () => {
  const undefinedDriver: ExecutionDriverV1 = {
    driverId: "undefined-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => undefined as any,
  }
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-undef",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const world = createExecutionWorld({ drivers: [undefinedDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-undef",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: cap.capability_id,
    },
    requestId: "req-undef-1",
    command: ["echo", "undef"],
    deadlineMs: null,
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.ok(result.sandboxResult)
  assert.equal(result.sandboxResult.status, "failed")
  assert.equal(result.sandboxResult.schema_version, "bb.sandbox_result.v1")
  assert.ok(String(result.sandboxResult.error?.message).includes("null or undefined"))
})

test("execution world catches malformed injected adapter sandbox result and returns normalized failed result", async () => {
  const malformedDriver: ExecutionDriverV1 = {
    driverId: "malformed-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => ({
      schema_version: "invalid.schema" as any,
      status: "invalid_status" as any,
    } as any),
  }
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-malformed",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const world = createExecutionWorld({ drivers: [malformedDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-malformed",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: capability.capability_id,
    },
    requestId: "req-malformed-1",
    command: ["echo", "test"],
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.ok(result.sandboxResult)
  assert.equal(result.sandboxResult.status, "failed")
  assert.equal(result.sandboxResult.schema_version, "bb.sandbox_result.v1")
  assert.ok(String(result.sandboxResult.error?.message).includes("malformed"))
})

test("execution world catches malformed terminal start result and returns unsupportedCase", async () => {
  const malformedTermDriver: TerminalSessionDriverV1 = {
    driverId: "malformed-term-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async () => ({
      descriptor: { invalid: "descriptor" } as unknown as TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [malformedTermDriver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-term-malformed",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-term-malformed",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const result = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-malformed-1",
      command: ["bash"],
      capability: cap,
      placement: place,
    },
  })
  assert.equal(result.kind, "terminal_start")
  assert.equal(result.result, null)
  assert.equal(result.unsupportedCase?.reason_code, "terminal_start_failed")
})

test("execution world catches malformed sandbox driver result, normalizes sandboxResult to failed, and ensures livenessEvidence state is failed", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-malformed-status",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-malformed-status",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const malformedDriver: TerminalSessionDriverV1 = {
    driverId: "malformed-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async (request) => {
      // Return malformed object with invalid status and missing required fields
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: request.request_id,
        status: "bogus_status_string" as unknown as SandboxResultV1["status"],
        placement_id: "malformed-place",
      } as unknown as SandboxResultV1
    },
  }
  const world = createExecutionWorld({ drivers: [malformedDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-malformed-1",
    command: ["echo", "test"],
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(result.livenessEvidence.executionStarted, true)
  assert.match(String(result.sandboxResult?.error?.message ?? ""), /malformed/i)
})

test("execution world rejects sandbox result with mismatched request_id correlation", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-mismatch",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-mismatch",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const mismatchDriver: TerminalSessionDriverV1 = {
    driverId: "mismatch-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId, capability, command }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: capability.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: [command[0] ?? "", ...command.slice(1)],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: capability.evidence_mode,
      metadata: {},
    }),
    execute: async (_request) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: "req-DIFFERENT-id",
      status: "completed",
      placement_id: "place-mismatch",
      stdout_ref: null,
      stderr_ref: null,
      artifact_refs: [],
      side_effect_digest: null,
      evidence_refs: [],
      usage: null,
      error: null,
    }),
  }
  const world = createExecutionWorld({ drivers: [mismatchDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-expected-1",
    command: ["echo", "test"],
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.match(String(result.sandboxResult?.error?.message ?? ""), /does not match request_id/i)
})

test("execution world handles immediate-end terminal start and routes scope-all cleanup to driver", async () => {
  const cleanedIds: string[] = []
  const immediateEndDriver: TerminalSessionDriverV1 = {
    driverId: "immediate-end-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        command: input.command,
        cwd: null,
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        capability_id: null,
        placement_id: null,
        persistence_scope: "thread",
        continuation_scope: "both",
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        terminal_state: "completed",
        exit_code: 0,
        duration_ms: 10,
        artifact_refs: [],
        evidence_refs: [],
      },
    }),
    cleanupTerminalSessions: async (input) => {
      cleanedIds.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.scope === "all" ? ["term-immediate-1"] : (input.sessionIds ?? []),
        failed_session_ids: [],
      }
    },
  }
  const world = createExecutionWorld({ drivers: [immediateEndDriver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-immediate",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-immediate",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const startResult = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-immediate-1",
      command: ["echo", "instant"],
      capability: cap,
      placement: place,
    },
  })
  assert.equal(startResult.kind, "terminal_start")
  assert.ok(startResult.result?.end)
  assert.equal(startResult.result?.end?.terminal_state, "completed")

  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-all-1",
      scope: "all",
    },
  })
  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.deepEqual(cleanupResult.result?.cleaned_session_ids, ["term-immediate-1"])
  assert.deepEqual(cleanupResult.result?.failed_session_ids, [])
})

test("execution world cleanup derives failed session IDs when adapter omits requested IDs from both cleaned and failed", async () => {
  const omittingDriver: TerminalSessionDriverV1 = {
    driverId: "omitting-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    cleanupTerminalSessions: async (input) => {
      // Driver only returns session-1 as cleaned, and completely omits session-2 and session-3 from both cleaned and failed
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: ["session-1"],
        failed_session_ids: [],
      }
    },
  }
  const world = createExecutionWorld({ drivers: [omittingDriver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-omit",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-omit",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-omit-1",
      scope: "filtered",
      sessionIds: ["session-1", "session-2", "session-3"],
    },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.deepEqual(cleanupResult.result?.cleaned_session_ids, ["session-1"])
  // session-2 and session-3 were omitted by driver, so world derives them as failed
  assert.ok(cleanupResult.result?.failed_session_ids?.includes("session-2"))
  assert.ok(cleanupResult.result?.failed_session_ids?.includes("session-3"))
})

test("execution world terminal_start rejects mismatched descriptor terminal_session_id", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-start-mismatch",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-start-mismatch",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const badDriver: TerminalSessionDriverV1 = {
    driverId: "bad-start-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async () => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: "wrong-session-id",
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [badDriver] })
  const res = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "correct-session-id",
      command: ["bash"],
    },
  })
  assert.equal(res.kind, "terminal_start")
  assert.equal(res.result, null)
  assert.equal(res.unsupportedCase?.reason_code, "terminal_start_failed")
  assert.match(res.unsupportedCase?.summary ?? "", /wrong-session-id/)
})

test("execution world terminal_start rejects mismatched outputDelta terminal_session_id", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-start-delta-mismatch",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-start-delta-mismatch",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const badDriver: TerminalSessionDriverV1 = {
    driverId: "bad-start-delta-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [
        {
          schema_version: "bb.terminal_output_delta.v1",
          terminal_session_id: "other-session-id",
          stream: "stdout",
          chunk_seq: 1,
          chunk_b64: Buffer.from("hello").toString("base64"),
        },
      ],
    }),
  }
  const world = createExecutionWorld({ drivers: [badDriver] })
  const res = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "correct-session-id",
      command: ["bash"],
    },
  })
  assert.equal(res.kind, "terminal_start")
  assert.equal(res.result, null)
  assert.equal(res.unsupportedCase?.reason_code, "terminal_start_failed")
  assert.match(res.unsupportedCase?.summary ?? "", /other-session-id/)
})

test("execution world terminal_interact rejects mismatched interaction and outputDelta and end terminal_session_ids", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-interact-mismatch",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-interact-mismatch",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const badDriver: TerminalSessionDriverV1 = {
    driverId: "bad-interact-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    interactTerminalSession: async () => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: "wrong-session-interact",
        interaction_kind: "stdin",
      },
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [badDriver] })
  const res = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "expected-session",
      interactionKind: "stdin",
    },
  })
  assert.equal(res.kind, "terminal_interact")
  assert.equal(res.result, null)
  assert.equal(res.unsupportedCase?.reason_code, "terminal_interaction_failed")
  assert.match(res.unsupportedCase?.summary ?? "", /wrong-session-interact/)
})


test("execution world terminal_interact rejects mismatched interaction kind", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-interact-kind-mismatch",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-interact-kind-mismatch",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "bad-interaction-kind",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        interaction_kind: "poll",
      },
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [driver] })

  const result = await world.execute({
    kind: "terminal_interact",
    capability,
    placement,
    input: {
      terminalSessionId: "session-kind-mismatch",
      interactionKind: "signal",
      signal: "SIGTERM",
    },
  })

  assert.equal(result.kind, "terminal_interact")
  assert.equal(result.result, null)
  assert.equal(result.unsupportedCase?.reason_code, "terminal_interaction_failed")
  assert.match(result.unsupportedCase?.summary ?? "", /does not match requested/)
})

test("execution world returns cancelled for pre-aborted signal even with invalid/empty command or failing builder", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-preabort",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-preabort",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  let builderCalled = false
  const driver: TerminalSessionDriverV1 = {
    driverId: "builder-check-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: () => {
      builderCalled = true
      throw new Error("Builder should not be called for pre-aborted run")
    },
    execute: async () => {
      throw new Error("Execute should not be called")
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const controller = new AbortController()
  controller.abort(new Error("pre-aborted"))

  const res = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-preabort-1",
    command: [],
    signal: controller.signal,
  })

  assert.equal(builderCalled, false)
  assert.equal(res.kind, "sandbox")
  assert.equal(res.sandboxResult?.status, "cancelled")
  assert.equal(res.livenessEvidence.state, "cancelled")
  assert.equal(res.livenessEvidence.executionStarted, false)
})

test("execution world preserves cancellation over deadline<=0 precedence on prelaunch", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-precedence",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-precedence",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "precedence-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    execute: async () => {
      throw new Error("Execute should not be called")
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const controller = new AbortController()
  controller.abort(new Error("pre-aborted"))

  const res = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-precedence-1",
    command: ["echo", "test"],
    signal: controller.signal,
    deadlineMs: 0,
  })

  assert.equal(res.kind, "sandbox")
  assert.equal(res.sandboxResult?.status, "cancelled")
  assert.equal(res.livenessEvidence.state, "cancelled")
})

test("execution world cleans up started backend session when terminal_start validation fails", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-start-cleanup-leak",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-start-cleanup-leak",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  let cleanupCalledWith: string[] = []
  const driver: TerminalSessionDriverV1 = {
    driverId: "cleanup-leak-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [
        // Invalid: mismatched session ID triggers world validation error
        {
          schema_version: "bb.terminal_output_delta.v1",
          terminal_session_id: "mismatched-session-id",
          stream: "stdout",
          chunk_seq: 1,
          chunk_b64: Buffer.from("output").toString("base64"),
        },
      ],
    }),
    cleanupTerminalSessions: async (input) => {
      cleanupCalledWith = [...(input.sessionIds ?? [])]
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const res = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "session-leak-test",
      command: ["bash"],
    },
  })

  assert.equal(res.kind, "terminal_start")
  assert.equal(res.result, null)
  assert.equal(res.unsupportedCase?.reason_code, "terminal_start_failed")
  // Cleanup was invoked to terminate started backend session
  assert.deepEqual(cleanupCalledWith, ["session-leak-test"])
})

test("execution world cleanup scope:all handles immediate-ended sessions idempotently without false failure", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-immediate-end",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-immediate-end",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "immediate-end-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed",
        exit_code: 0,
      },
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
      failed_session_ids: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [driver] })
  // Start session that immediately ends
  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "session-immediate-ended",
      command: ["bash"],
    },
  })
  assert.equal(startRes.kind, "terminal_start")
  assert.ok(startRes.result?.end != null)

  // Execute scope:all cleanup
  const cleanupRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-all-1",
      scope: "all",
    },
  })
  assert.equal(cleanupRes.kind, "terminal_cleanup")
  // Immediate-ended session should be in cleaned_session_ids and NOT in failed_session_ids
  assert.deepEqual(cleanupRes.result?.cleaned_session_ids, ["session-immediate-ended"])
  assert.deepEqual(cleanupRes.result?.failed_session_ids, [])
})

test("execution world retains all ended session owners without arbitrary eviction until cleanup confirmed", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-fifo-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-fifo-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "fifo-test-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed",
        exit_code: 0,
      },
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
      failed_session_ids: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [driver] })

  // Start 40 immediately-ending sessions
  for (let i = 1; i <= 40; i++) {
    await world.execute({
      kind: "terminal_start",
      capability: cap,
      placement: place,
      input: {
        terminalSessionId: `session-fifo-${i}`,
        command: ["bash"],
      },
    })
  }

  // Scope all cleanup cleans all 40 remembered ended sessions without arbitrary eviction
  const cleanupRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-fifo-all",
      scope: "all",
    },
  })
  assert.equal(cleanupRes.kind, "terminal_cleanup")
  assert.equal(cleanupRes.result?.cleaned_session_ids.length, 40)
  for (let i = 1; i <= 40; i++) {
    assert.ok(cleanupRes.result?.cleaned_session_ids.includes(`session-fifo-${i}`))
  }
})

test("execution world >32 ended sessions reconciliation handles filtered and all scopes without cap omission", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-reconcile-50",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-reconcile-50",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const endedInDriver = new Set<string>()
  const driver: TerminalSessionDriverV1 = {
    driverId: "reconcile-50-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => {
      endedInDriver.add(input.terminalSessionId)
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1",
          terminal_session_id: input.terminalSessionId,
          public_handles: [],
          command: ["bash"],
          cwd: null,
          startup_call_id: null,
          owner_task_id: null,
          stream_mode: "pipes",
          stream_split: "stdout_stderr",
          capability_id: cap.capability_id,
          placement_id: place.placement_id,
          persistence_scope: "thread",
          continuation_scope: "both",
        },
        outputDeltas: [],
        end: {
          schema_version: "bb.terminal_session_end.v1",
          terminal_session_id: input.terminalSessionId,
          startup_call_id: null,
          causing_call_id: null,
          terminal_state: "completed",
          exit_code: 0,
          duration_ms: 5,
          artifact_refs: [],
          evidence_refs: [],
        },
      }
    },
    cleanupTerminalSessions: async (input) => {
      const targets = input.scope === "all" ? [...endedInDriver] : input.sessionIds ?? []
      for (const id of targets) {
        endedInDriver.delete(id)
      }
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: targets,
        failed_session_ids: [],
        metadata: {},
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })

  // Start 50 immediately-ending sessions
  for (let i = 1; i <= 50; i++) {
    await world.execute({
      kind: "terminal_start",
      capability: cap,
      placement: place,
      input: {
        terminalSessionId: `session-rec-${i}`,
        command: ["bash"],
      },
    })
  }

  // Clean up subset: early IDs (1..10) and late IDs (40..50)
  const subset = ["session-rec-1", "session-rec-5", "session-rec-10", "session-rec-45", "session-rec-50"]
  const filteredCleanup = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-rec-subset",
      scope: "filtered",
      sessionIds: subset,
    },
  })
  assert.equal(filteredCleanup.kind, "terminal_cleanup")
  assert.deepEqual(filteredCleanup.result?.cleaned_session_ids.sort(), subset.sort())
  assert.deepEqual(filteredCleanup.result?.failed_session_ids, [])

  // Clean up remaining sessions via scope: all
  const allCleanup = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-rec-remaining",
      scope: "all",
    },
  })
  assert.equal(allCleanup.kind, "terminal_cleanup")
  assert.equal(allCleanup.result?.cleaned_session_ids.length, 45)
  assert.deepEqual(allCleanup.result?.failed_session_ids, [])
})

test("execution world classifies arbitrary adapter error message containing 'abort' as failed, not cancelled", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-abort-msg-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-abort-msg-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "failing-remote-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: (input) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: input.requestId,
      capability_id: input.capability.capability_id,
      placement_class: input.placement.placement_class,
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: ["python", "app.py"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: 30,
      evidence_mode: input.capability.evidence_mode,
      metadata: {},
    }),
    execute: async () => {
      throw new Error("HTTP 500: remote worker aborted unexpectedly")
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-abort-msg-1",
    command: ["python", "app.py"],
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(result.livenessEvidence.terminationRequested, false)
})

test("execution world failure normalization preserves world reason over hostile adapter error payload", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-hostile-error-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-hostile-error-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const driver: TerminalSessionDriverV1 = {
    driverId: "hostile-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: (input) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: input.requestId,
      capability_id: input.capability.capability_id,
      placement_class: input.placement.placement_class,
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: ["python", "app.py"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: 30,
      evidence_mode: input.capability.evidence_mode,
      metadata: {},
    }),
    execute: async (req) => {
      // Return a result that already has a hostile error object claiming success or custom reason
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: req.request_id,
        status: "completed",
        placement_id: "place-hostile",
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        side_effect_digest: null,
        usage: null,
        evidence_refs: [],
        error: {
          message: "hostile adapter claimed message",
          reason: "hostile_adapter_claimed_reason",
        },
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })
  // Exercise timeout
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-hostile-1",
    command: ["python", "app.py"],
    deadlineMs: 0,
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.sandboxResult?.error?.reason, "deadline_exceeded")
  assert.equal(result.sandboxResult?.error?.message, "Execution exceeded deadline before starting")
})

test("execution world filtered cleanup ignores extra session IDs returned by malformed adapter and preserves unrequested session ownership", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-malformed-cleanup-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-malformed-cleanup-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "malformed-cleanup-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        task_id: "task-1",
        owner_task_id: null,
        startup_call_id: null,
        command: input.command,
        cwd: "/tmp",
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
        started_at_utc: new Date().toISOString(),
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        interaction_kind: "stdin",
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: [...(input.sessionIds ?? []), "session-victim-unrequested", "session-phantom"],
      failed_session_ids: ["session-rogue-failed"],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "session-target", command: ["bash"] },
  })
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "session-victim-unrequested", command: ["bash"] },
  })

  const cleanupRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-single-target",
      scope: "single",
      sessionIds: ["session-target"],
    },
  })

  assert.equal(cleanupRes.kind, "terminal_cleanup")
  assert.deepEqual(cleanupRes.result?.cleaned_session_ids, ["session-target"])
  assert.deepEqual(cleanupRes.result?.failed_session_ids, [])

  const interactRes = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "session-victim-unrequested",
      interactionKind: "stdin",
      inputText: "test\n",
    },
  })
  assert.equal(interactRes.kind, "terminal_interact")
  assert.ok(interactRes.result !== null, "victim session remains active and interactive")
})

test("execution world filtered cleanup rejects overlap in cleaned_session_ids and failed_session_ids, marks as failed and preserves ownership", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-overlap-filtered-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-overlap-filtered-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "overlap-filtered-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        command: input.command,
        cwd: null,
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        capability_id: null,
        placement_id: null,
        persistence_scope: "thread",
        continuation_scope: "both",
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        interaction_kind: "stdin",
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: ["session-contradiction-1"],
      failed_session_ids: ["session-contradiction-1"],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "session-contradiction-1", command: ["bash"] },
  })
  assert.equal(startRes.kind, "terminal_start")
  assert.ok(startRes.result, "terminal start succeeded")
  await assert.rejects(
    () =>
      world.execute({
        kind: "terminal_cleanup",
        capability: cap,
        placement: place,
        input: {
          cleanupId: "clean-contradiction-1",
          scope: "single",
          sessionIds: ["session-contradiction-1"],
        },
      }),
    /Invalid cleanup result: session ID 'session-contradiction-1' appears in both cleaned_session_ids and failed_session_ids/,
  )
  const interactRes = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "session-contradiction-1",
      interactionKind: "stdin",
      inputText: "test\n",
    },
  })
  assert.equal(interactRes.kind, "terminal_interact")
  assert.ok(interactRes.result !== null, "session remains active and interactive")
})

test("execution world all cleanup rejects overlap in cleaned_session_ids and failed_session_ids, marks as failed and preserves ownership", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-overlap-all-test",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-overlap-all-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "overlap-all-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        command: input.command,
        cwd: null,
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        capability_id: null,
        placement_id: null,
        persistence_scope: "thread",
        continuation_scope: "both",
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        interaction_kind: "stdin",
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: ["session-all-contradiction-1"],
      failed_session_ids: ["session-all-contradiction-1"],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "session-all-contradiction-1", command: ["bash"] },
  })
  assert.equal(startRes.kind, "terminal_start")
  await assert.rejects(
    () =>
      world.execute({
        kind: "terminal_cleanup",
        capability: cap,
        placement: place,
        input: {
          cleanupId: "clean-all-contradiction-1",
          scope: "all",
        },
      }),
    /Invalid cleanup result: session ID 'session-all-contradiction-1' appears in both cleaned_session_ids and failed_session_ids/,
  )
  const interactRes = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "session-all-contradiction-1",
      interactionKind: "stdin",
      inputText: "test\n",
    },
  })
  assert.equal(interactRes.kind, "terminal_interact")
  assert.ok(interactRes.result !== null, "session remains active and interactive")
})


test("execution world cleanup treats adapter error with 'Invalid cleanup result:' prefix as adapter failure and marks session failed", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-cleanup-err-prefix-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-cleanup-err-prefix-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "err-prefix-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        created_at_utc: new Date().toISOString(),
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async () => {
      // Malicious or accidental error text matching validation error prefix
      throw new Error("Invalid cleanup result: adapter internal failure during kill")
    },
  }

  const world = createExecutionWorld({ drivers: [driver] })

  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "sess-err-prefix-1", command: ["bash"] },
  })

  // Cleanup should not throw ExecutionDriverResultValidationError, but return failed session IDs
  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-err-prefix-1",
      scope: "single",
      sessionIds: ["sess-err-prefix-1"],
    },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.ok(cleanupResult.result !== null)
  assert.deepEqual(cleanupResult.result?.cleaned_session_ids, [])
  assert.deepEqual(cleanupResult.result?.failed_session_ids, ["sess-err-prefix-1"])
})

test("execution world pinned driver with wrong placement returns unsupported with no fallback", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-wrong-placement-1",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-wrong-placement-1",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  let ociDriverExecuted = false
  const localDriver: TerminalSessionDriverV1 = {
    driverId: "local-process",
    supportedPlacements: ["local_process"], // Does NOT support local_oci
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: cap.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: ["echo", "local"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: "minimal",
      metadata: {},
    }),
    execute: async () => {
      throw new Error("local driver should not execute for local_oci")
    },
  }

  const ociDriver: TerminalSessionDriverV1 = {
    driverId: "oci",
    supportedPlacements: ["local_oci"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: cap.capability_id,
      placement_class: "local_oci",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: "docker://test:latest",
      snapshot_ref: null,
      command: ["echo", "oci"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: "minimal",
      metadata: {},
    }),
    execute: async (req) => {
      ociDriverExecuted = true
      return {
        schema_version: "bb.sandbox_result.v1",
        request_id: req.request_id,
        status: "completed",
        placement_id: place.placement_id,
        stdout_ref: null,
        stderr_ref: null,
        artifact_refs: [],
        evidence_refs: [],
        side_effect_digest: "sha256:oci",
        usage: { exit_code: 0 },
        error: null,
      }
    },
  }

  const world = createExecutionWorld({ drivers: [localDriver, ociDriver] })

  // Pinning local-process for local_oci placement must return unsupported, not execute OCI driver fallback
  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-wrong-placement-1",
    command: ["echo", "test"],
    driverId: "local-process",
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.driverId, null)
  assert.ok(result.unsupportedCase !== undefined)
  assert.equal(ociDriverExecuted, false, "must not fall back to OCI driver when local-process is pinned")
})

test("execution world pinned driver with missing capability returns unsupported with no fallback", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-missing-capability-1",
    security_tier: "single_tenant",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-missing-capability-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const pickyDriver: TerminalSessionDriverV1 = {
    driverId: "picky-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => false, // Rejects capability
    buildSandboxRequest: ({ requestId }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: cap.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: ["echo", "picky"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: "minimal",
      metadata: {},
    }),
    execute: async () => {
      throw new Error("picky driver should not execute when supportsCapability is false")
    },
  }

  const fallbackDriver: TerminalSessionDriverV1 = {
    driverId: "fallback-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    buildSandboxRequest: ({ requestId }) => ({
      schema_version: "bb.sandbox_request.v1",
      request_id: requestId,
      capability_id: cap.capability_id,
      placement_class: "local_process",
      workspace_ref: null,
      rootfs_ref: null,
      image_ref: null,
      snapshot_ref: null,
      command: ["echo", "fallback"],
      network_policy: { allow: [] },
      secret_refs: [],
      timeout_seconds: null,
      evidence_mode: "minimal",
      metadata: {},
    }),
    execute: async (req) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: req.request_id,
      status: "completed",
      placement_id: place.placement_id,
      stdout_ref: null,
      stderr_ref: null,
      artifact_refs: [],
      evidence_refs: [],
      side_effect_digest: "sha256:fallback",
      usage: { exit_code: 0 },
      error: null,
    }),
  }

  const world = createExecutionWorld({ drivers: [pickyDriver, fallbackDriver] })

  const result = await world.execute({
    kind: "sandbox",
    capability: cap,
    placement: place,
    requestId: "req-missing-cap-1",
    command: ["echo", "test"],
    driverId: "picky-driver",
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.driverId, null)
  assert.ok(result.unsupportedCase !== undefined)
})

test("execution world terminal_start validation failure bounds never-settling cleanup and returns typed unsupportedCase", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-never-settle-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-never-settle-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let cleanupCalled = false
  const driver: TerminalSessionDriverV1 = {
    driverId: "never-settle-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        // Mismatched session ID triggers validation failure
        terminal_session_id: "wrong-session-id",
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        created_at_utc: new Date().toISOString(),
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      cleanupCalled = true
      if (input.cleanupId === "clean-all-retry") {
        return {
          schema_version: "bb.terminal_cleanup_result.v1",
          cleanup_id: input.cleanupId,
          scope: input.scope,
          cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : ["term-req-1"],
          failed_session_ids: [],
        }
      }
      // Never settles during initial validation failure cleanup
      return new Promise(() => {})
    },
  }

  const world = createExecutionWorld({
    drivers: [driver],
    terminationGraceMs: 30,
  })

  const result = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-req-1",
      command: ["bash"],
    },
  })

  assert.equal(result.kind, "terminal_start")
  assert.equal(result.result, null)
  assert.ok(result.unsupportedCase !== null)
  assert.equal(result.unsupportedCase?.reason_code, "terminal_start_failed")
  assert.equal(cleanupCalled, true)

  // Subsequent cleanup with scope all MUST retry cleaning the unconfirmed started session
  const retryClean = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-all-retry",
      scope: "all",
    },
  })
  assert.equal(retryClean.kind, "terminal_cleanup")
  assert.deepEqual(retryClean.result?.cleaned_session_ids, ["term-req-1"])
})
test("execution world rejects start for already active terminal session without second adapter launch", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-dup-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-dup-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let startCallCount = 0
  let releaseStart: (() => void) | undefined
  const startGate = new Promise<void>((resolve) => {
    releaseStart = resolve
  })
  const driver: TerminalSessionDriverV1 = {
    driverId: "dup-test-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => {
      startCallCount++
      await startGate
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1",
          terminal_session_id: input.terminalSessionId,
          command: input.command,
          startup_call_id: null,
          owner_task_id: null,
          public_handles: [],
          capability_id: cap.capability_id,
          placement_id: place.placement_id,
          persistence_scope: "thread",
          continuation_scope: "both",
          stream_mode: "pipes",
          stream_split: "stdout_stderr",
        },
        outputDeltas: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver] })

  const firstStartPromise = world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-active-dup-1",
      command: ["bash"],
    },
  })
  assert.equal(startCallCount, 1)

  const overlappingStart = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-active-dup-1",
      command: ["bash", "-c", "echo overlapping"],
    },
  })
  assert.equal(overlappingStart.kind, "terminal_start")
  assert.equal(overlappingStart.result, null)
  assert.equal(overlappingStart.unsupportedCase?.reason_code, "terminal_session_already_active")
  assert.equal(startCallCount, 1)

  releaseStart?.()
  const firstStart = await firstStartPromise
  assert.equal(firstStart.kind, "terminal_start")
  assert.ok(firstStart.result !== null)

  const activeDuplicate = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-active-dup-1",
      command: ["bash", "-c", "echo second"],
    },
  })
  assert.equal(activeDuplicate.kind, "terminal_start")
  assert.equal(activeDuplicate.result, null)
  assert.equal(activeDuplicate.unsupportedCase?.reason_code, "terminal_session_already_active")
  assert.equal(startCallCount, 1, "adapter start ran only once")
})

test("execution world raw pinned driverId rejects sandbox-only driver for terminal start", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pin-sandbox-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-pin-sandbox-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  // Sandbox-only driver has no supportsTerminalSessions and no terminal methods
  const sandboxOnlyDriver: ExecutionDriverV1 = {
    driverId: "sandbox-only-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    execute: async (req) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: req.request_id,
      status: "completed",
      exit_code: 0,
    }),
  }

  const world = createExecutionWorld({ drivers: [sandboxOnlyDriver] })
  const result = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: "sandbox-only-driver",
    input: {
      terminalSessionId: "term-pin-sandbox-1",
      command: ["bash"],
    },
  })

  assert.equal(result.kind, "terminal_start")
  assert.equal(result.driverId, null)
  assert.equal(result.result, null)
  assert.ok(result.unsupportedCase !== null)
})

test("execution world raw pinned driverId rejects remote driver lacking terminal adapter", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pin-remote-1",
    security_tier: "trusted_dev",
    isolation_class: "remote_service",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-pin-remote-1",
    placement_class: "remote_worker",
    runtime_id: "remote",
    capability_id: cap.capability_id,
  }

  // Remote driver without HTTP terminal adapter has supportsTerminalSessions() === false
  const remoteWithoutTerminal: TerminalSessionDriverV1 = {
    driverId: "remote",
    supportedPlacements: ["remote_worker"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => false,
  }

  const world = createExecutionWorld({ drivers: [remoteWithoutTerminal] })
  const result = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: "remote",
    input: {
      terminalSessionId: "term-pin-remote-1",
      command: ["bash"],
    },
  })

  assert.equal(result.kind, "terminal_start")
  assert.equal(result.driverId, null)
  assert.equal(result.result, null)
  assert.ok(result.unsupportedCase !== null)
})

test("execution world cleanup with scope all cleans ended session owners as well as active sessions", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-scope-all-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-scope-all-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const cleanedInDriver: string[] = []
  const driver: TerminalSessionDriverV1 = {
    driverId: "scope-all-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        interaction_id: "interact-1",
        terminal_session_id: input.terminalSessionId,
        interaction_kind: input.interactionKind,
        causing_call_id: null,
        input_text: null,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed",
        exit_code: 0,
        signal: null,
        duration_ms: 10,
      },
    }),
    cleanupTerminalSessions: async (input) => {
      const ids = input.sessionIds ?? []
      cleanedInDriver.push(...ids)
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: ids,
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver] })

  // Start session 1 and end it via interaction (moves to endedSessionOwners)
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-ended-1", command: ["bash"] },
  })
  await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-ended-1", interactionKind: "poll" },
  })

  // Start session 2 (remains active in sessions)
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-active-2", command: ["bash"] },
  })

  // Global cleanup with scope: "all"
  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-all-1", scope: "all" },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.ok(cleanupResult.result !== null)
  assert.ok(cleanupResult.result?.cleaned_session_ids.includes("term-ended-1"))
  assert.ok(cleanupResult.result?.cleaned_session_ids.includes("term-active-2"))
  assert.ok(cleanedInDriver.includes("term-ended-1"))
  assert.ok(cleanedInDriver.includes("term-active-2"))
})

test("execution world scope all omits ended sessions owned by a nonselected driver", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-no-clean-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-no-clean-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  // Driver 1 supports start/interact but NOT cleanupTerminalSessions
  const driverWithoutCleanup: TerminalSessionDriverV1 = {
    driverId: "driver-no-cleanup",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed",
        exit_code: 0,
      },
    }),
  }

  // Driver 2 is a second driver that does support cleanup
  const driverWithCleanup: TerminalSessionDriverV1 = {
    driverId: "driver-with-cleanup",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ?? [],
      failed_session_ids: [],
    }),
  }

  const world = createExecutionWorld({ drivers: [driverWithoutCleanup, driverWithCleanup] })

  // Start session on driverWithoutCleanup that immediately ends
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: "driver-no-cleanup",
    input: { terminalSessionId: "term-no-clean-ended", command: ["bash"] },
  })

  // Profile cleanup selects only the cleanup-capable driver.
  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-all-no-cleanup", scope: "all" },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.ok(cleanupResult.result !== null)
  assert.deepEqual(cleanupResult.result?.failed_session_ids, [])
  assert.deepEqual(cleanupResult.result?.cleaned_session_ids, [])
})

test("execution world cleanup selects cleanup-capable driver when first ordered driver lacks cleanup", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-order-clean-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-order-clean-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  // Driver 0 has NO cleanupTerminalSessions
  const driver0: TerminalSessionDriverV1 = {
    driverId: "driver-no-clean-0",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
  }

  // Driver 1 has cleanupTerminalSessions
  let cleanedInDriver1: string[] = []
  const driver1: TerminalSessionDriverV1 = {
    driverId: "driver-clean-1",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    cleanupTerminalSessions: async (input) => {
      cleanedInDriver1 = [...(input.sessionIds ?? [])]
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver0, driver1] })

  // Cleanup unmapped session ID without explicit pin -> should route to cleanup-capable driver1
  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-unmapped-1", scope: "single", sessionIds: ["term-unmapped-1"] },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.equal(cleanupResult.driverId, "driver-clean-1")
  assert.ok(cleanupResult.result !== null)
  assert.deepEqual(cleanupResult.result?.cleaned_session_ids, ["term-unmapped-1"])
  assert.deepEqual(cleanedInDriver1, ["term-unmapped-1"])
})

test("execution world cleanup with explicit pin routes unmapped ID to pinned driver or fails if pin lacks cleanup", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pin-clean-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-pin-clean-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driverNoClean: TerminalSessionDriverV1 = {
    driverId: "pin-no-clean",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
  }

  let cleanedInDriver2: string[] = []
  const driverClean2: TerminalSessionDriverV1 = {
    driverId: "pin-clean-2",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    cleanupTerminalSessions: async (input) => {
      cleanedInDriver2 = [...(input.sessionIds ?? [])]
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driverNoClean, driverClean2] })

  // Pinned to pin-clean-2 -> succeeds on driver2
  const cleanPin2 = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    driverId: "pin-clean-2",
    input: { cleanupId: "clean-pin-2", scope: "single", sessionIds: ["term-pin-clean"] },
  })
  assert.equal(cleanPin2.kind, "terminal_cleanup")
  assert.equal(cleanPin2.driverId, "pin-clean-2")
  assert.deepEqual(cleanPin2.result?.cleaned_session_ids, ["term-pin-clean"])
  assert.deepEqual(cleanedInDriver2, ["term-pin-clean"])
  // Pinned to pin-no-clean -> fails unmapped ID because pinned driver cannot clean
  const cleanPinNoClean = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    driverId: "pin-no-clean",
    input: { cleanupId: "clean-pin-noclean", scope: "single", sessionIds: ["term-pin-noclean"] },
  })
  assert.equal(cleanPinNoClean.kind, "terminal_cleanup")
  assert.equal(cleanPinNoClean.driverId, "pin-no-clean")
  assert.deepEqual(cleanPinNoClean.result?.failed_session_ids, ["term-pin-noclean"])
  assert.deepEqual(cleanPinNoClean.result?.cleaned_session_ids, [])
})

test("execution world cleanup treats omitted requested ended session ID as failed and retains ownership for retry", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-omit-clean-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-omit-clean-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let cleanupCalls = 0
  const driver: TerminalSessionDriverV1 = {
    driverId: "omission-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        terminal_state: "completed",
        exit_code: 0,
      },
    }),
    cleanupTerminalSessions: async (input) => {
      cleanupCalls++
      // First cleanup call: omits session from both cleaned and failed
      if (cleanupCalls === 1) {
        return {
          schema_version: "bb.terminal_cleanup_result.v1",
          cleanup_id: input.cleanupId,
          scope: input.scope,
          cleaned_session_ids: [],
          failed_session_ids: [],
        }
      }
      // Second cleanup call: confirms cleaned
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver] })

  // Start session that immediately ends (retained in endedSessionOwners)
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-omitted-1", command: ["bash"] },
  })

  // First cleanup: driver returns empty lists
  const clean1 = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-omit-1", scope: "single", sessionIds: ["term-omitted-1"] },
  })
  assert.equal(clean1.kind, "terminal_cleanup")
  // Omitted ID must be reported in failed_session_ids (not cleaned)
  assert.deepEqual(clean1.result?.failed_session_ids, ["term-omitted-1"])
  assert.deepEqual(clean1.result?.cleaned_session_ids, [])

  // Second cleanup: retry succeeds because ownership was retained
  const clean2 = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-omit-2", scope: "single", sessionIds: ["term-omitted-1"] },
  })
  assert.equal(clean2.kind, "terminal_cleanup")
  assert.deepEqual(clean2.result?.cleaned_session_ids, ["term-omitted-1"])
  assert.deepEqual(clean2.result?.failed_session_ids, [])
})

test("execution world reserved pinned driver bypasses later mutable predicate flip while raw selection checks predicate", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-mutable-pred-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-mutable-pred-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let dynamicPredicate = true
  const driver: TerminalSessionDriverV1 = {
    driverId: "mutable-pred-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => dynamicPredicate,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        command: ["bash"],
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  // Raw start creates reservation when predicate is true
  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-mutable-1", command: ["bash"] },
  })
  assert.equal(startRes.kind, "terminal_start")
  assert.ok(startRes.result !== null)

  // Mutable predicate flips to false
  dynamicPredicate = false

  // Interaction on the reserved active session succeeds (bypasses mutable predicate)
  const interactRes = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-mutable-1", interactionKind: "poll" },
  })
  assert.equal(interactRes.kind, "terminal_interact")
  assert.ok(interactRes.result !== null)

  // Raw new start fails because predicate is false
  const startNew = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-mutable-2", command: ["bash"] },
  })
  assert.equal(startNew.kind, "terminal_start")
  assert.equal(startNew.result, null)
  assert.ok(startNew.unsupportedCase !== null)
  const pinnedStart = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: driver.driverId,
    input: { terminalSessionId: "term-mutable-pinned", command: ["bash"] },
  })
  assert.equal(pinnedStart.kind, "terminal_start")
  assert.equal(pinnedStart.result, null)
  assert.ok(pinnedStart.unsupportedCase !== null)
})

test("execution world cleanup with explicit pin and scope all rejects invalid pin without fan-out", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pin-all-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-pin-all-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let defaultCleanedCalled = false
  const driver1: TerminalSessionDriverV1 = {
    driverId: "valid-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    cleanupTerminalSessions: async (input) => {
      defaultCleanedCalled = true
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver1] })

  const cleanRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    driverId: "unknown-pin",
    input: { cleanupId: "clean-pin-all-invalid", scope: "all" },
  })

  assert.equal(cleanRes.kind, "terminal_cleanup")
  assert.equal(cleanRes.driverId, null)
  assert.equal(cleanRes.result, null)
  assert.ok(cleanRes.unsupportedCase !== null)
  assert.equal(defaultCleanedCalled, false, "cleanup did not fan out to other drivers on invalid pin")
})

test("execution world cleanup with explicit pin and scope all targets only pinned driver", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-pin-all-2",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-pin-all-2",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let driver1CleanedCalled = false
  let driver2CleanedCalled = false

  const driver1: TerminalSessionDriverV1 = {
    driverId: "driver-one",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      driver1CleanedCalled = true
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }

  const driver2: TerminalSessionDriverV1 = {
    driverId: "driver-two",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      driver2CleanedCalled = true
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({ drivers: [driver1, driver2] })

  // Start a session in driver1 and a session in driver2
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: "driver-one",
    input: { terminalSessionId: "term-one", command: ["bash"] },
  })
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverId: "driver-two",
    input: { terminalSessionId: "term-two", command: ["bash"] },
  })

  const cleanRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    driverId: "driver-two",
    input: { cleanupId: "clean-pin-all-target", scope: "all" },
  })

  assert.equal(cleanRes.kind, "terminal_cleanup")
  assert.equal(cleanRes.driverId, "driver-two")
  assert.deepEqual(cleanRes.result?.cleaned_session_ids, ["term-two"])
  assert.equal(driver1CleanedCalled, false, "driver-one was not called")
  assert.equal(driver2CleanedCalled, true, "driver-two was called")
})

test("execution world rejects start reuse for session ID with uncleaned prior ended session until confirmed clean", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-reuse-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-reuse-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  const driver: TerminalSessionDriverV1 = {
    driverId: "reuse-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        terminal_state: "completed",
        exit_code: 0,
        duration_ms: 10,
        artifact_refs: [],
        evidence_refs: [],
      },
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ? [...input.sessionIds] : [],
      failed_session_ids: [],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  // 1. Start session
  const start1 = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-reuse-ended-1", command: ["bash"] },
  })
  assert.equal(start1.kind, "terminal_start")
  assert.ok(start1.result)

  // 2. Poll interaction signals session end, moving it to endedSessionOwners
  const interact = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-reuse-ended-1", interactionKind: "poll" },
  })
  assert.equal(interact.kind, "terminal_interact")
  assert.equal(interact.result?.end?.terminal_state, "completed")
  // 3. Attempting start with the same ID before cleanup must fail with typed unsupportedCase
  const restartAttempt = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-reuse-ended-1", command: ["bash"] },
  })
  assert.equal(restartAttempt.kind, "terminal_start")
  assert.equal(restartAttempt.result, null)
  assert.ok(restartAttempt.unsupportedCase !== null)
  assert.equal(restartAttempt.unsupportedCase?.reason_code, "terminal_session_already_active")

  // 4. Cleanup the ended session
  const cleanRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-reuse-ended", scope: "single", sessionIds: ["term-reuse-ended-1"] },
  })
  assert.equal(cleanRes.kind, "terminal_cleanup")
  assert.deepEqual(cleanRes.result?.cleaned_session_ids, ["term-reuse-ended-1"])

  // 5. Now start with the same ID succeeds
  const restartSuccess = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: { terminalSessionId: "term-reuse-ended-1", command: ["bash"] },
  })
  assert.equal(restartSuccess.kind, "terminal_start")
  assert.ok(restartSuccess.result)
  assert.equal(restartSuccess.result?.descriptor.terminal_session_id, "term-reuse-ended-1")
})

test("execution world malformed-start cleanup contradictory result retains owner route for subsequent all cleanup", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-contradictory-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-contradictory-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let cleanupCalls = 0
  const driver: TerminalSessionDriverV1 = {
    driverId: "contradictory-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        // Mismatched session ID triggers validation failure
        terminal_session_id: "wrong-session-id",
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        created_at_utc: new Date().toISOString(),
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      cleanupCalls++
      if (cleanupCalls === 1) {
        // Contradictory result: appears in both cleaned and failed
        return {
          schema_version: "bb.terminal_cleanup_result.v1",
          cleanup_id: input.cleanupId,
          scope: input.scope,
          cleaned_session_ids: ["term-contradictory-1"],
          failed_session_ids: ["term-contradictory-1"],
        }
      }
      // Call 2: valid cleanup
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: ["term-contradictory-1"],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({
    drivers: [driver],
    terminationGraceMs: 50,
  })

  // Start fails validation, cleanup returns contradictory result (retains owner route)
  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-contradictory-1",
      command: ["bash"],
    },
  })
  assert.equal(startRes.kind, "terminal_start")
  assert.equal(startRes.result, null)
  assert.equal(cleanupCalls, 1)

  // Subsequent scope all cleanup retries contradictory-driver
  const cleanRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-all-retry-contradictory",
      scope: "all",
    },
  })
  assert.equal(cleanRes.kind, "terminal_cleanup")
  assert.deepEqual(cleanRes.result?.cleaned_session_ids, ["term-contradictory-1"])
  assert.equal(cleanupCalls, 2)
})

test("execution world malformed-start cleanup string cleaned_session_ids does not coerce to array and retains owner route", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-str-coerced-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-str-coerced-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  let cleanupCalls = 0
  const driver: TerminalSessionDriverV1 = {
    driverId: "str-coerced-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        // Mismatched session ID triggers validation failure
        terminal_session_id: "wrong-session-id",
        command: input.command,
        startup_call_id: null,
        owner_task_id: null,
        public_handles: [],
        capability_id: cap.capability_id,
        placement_id: place.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        created_at_utc: new Date().toISOString(),
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      cleanupCalls++
      if (cleanupCalls === 1) {
        // String field instead of array of strings
        return {
          schema_version: "bb.terminal_cleanup_result.v1",
          cleanup_id: input.cleanupId,
          scope: input.scope,
          cleaned_session_ids: "term-str-coerced-1" as unknown as string[],
          failed_session_ids: [],
        }
      }
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: ["term-str-coerced-1"],
        failed_session_ids: [],
      }
    },
  }

  const world = createExecutionWorld({
    drivers: [driver],
    terminationGraceMs: 50,
  })

  // Start fails validation, cleanup returns string field (retains owner route)
  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-str-coerced-1",
      command: ["bash"],
    },
  })
  assert.equal(startRes.kind, "terminal_start")
  assert.equal(startRes.result, null)
  assert.equal(cleanupCalls, 1)

  // Subsequent scope all cleanup retries str-coerced-driver
  const cleanRes = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-all-retry-str-coerced",
      scope: "all",
    },
  })
  assert.equal(cleanRes.kind, "terminal_cleanup")
  assert.deepEqual(cleanRes.result?.cleaned_session_ids, ["term-str-coerced-1"])
  assert.equal(cleanupCalls, 2)
})

test("execution world snapshot sanitizes merged ended session IDs against active sessions when backend snapshot overlaps", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-snap-overlap-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-snap-overlap-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  let dynamicSupport = true
  const driver: TerminalSessionDriverV1 = {
    driverId: "snap-overlap-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => dynamicSupport,
    snapshotTerminalRegistry: async () => ({
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: "snap-overlap-1",
      active_sessions: [
        {
          schema_version: "bb.terminal_session_descriptor.v1",
          terminal_session_id: "term-overlap-active",
          command: ["bash"],
          startup_call_id: null,
          owner_task_id: null,
          public_handles: [],
          capability_id: cap.capability_id,
          placement_id: place.placement_id,
          persistence_scope: "thread",
          continuation_scope: "both",
          stream_mode: "pipes",
          stream_split: "stdout_stderr",
        },
      ],
      // Driver snapshot includes overlapping ID in ended_session_ids
      ended_session_ids: ["term-overlap-active", "term-truly-ended"],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
    }),
  }

  const world = createExecutionWorld({ drivers: [driver] })

  const snapRes = await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  assert.equal(snapRes.kind, "terminal_snapshot")
  assert.ok(snapRes.result)
  assert.equal(snapRes.result?.active_sessions.length, 1)
  assert.equal(snapRes.result?.active_sessions[0]?.terminal_session_id, "term-overlap-active")
  // ended_session_ids must be sanitized to exclude term-overlap-active
  assert.deepEqual(snapRes.result?.ended_session_ids, ["term-truly-ended"])

  dynamicSupport = false
  const interactRes = await world.execute({
    kind: "terminal_interact",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-overlap-active",
      interactionKind: "poll",
    },
  })
  assert.equal(interactRes.kind, "terminal_interact")
  assert.ok(interactRes.result)
})

test("execution world ignores cleaned IDs in stale driver snapshots", async () => {
  const sessionId = "term-cleaned-stale-snapshot"
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-cleaned-stale-snapshot",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-cleaned-stale-snapshot",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const staleEndedIds = new Set<string>()
  let starts = 0
  let malformedStart = false
  const driver: TerminalSessionDriverV1 = {
    driverId: "cleaned-stale-snapshot-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => {
      starts++
      staleEndedIds.add(input.terminalSessionId)
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1",
          terminal_session_id: malformedStart ? "wrong-session-id" : input.terminalSessionId,
          public_handles: [],
          command: input.command,
          cwd: null,
          startup_call_id: null,
          owner_task_id: null,
          stream_mode: "pipes",
          stream_split: "stdout_stderr",
          capability_id: cap.capability_id,
          placement_id: place.placement_id,
          persistence_scope: "thread",
          continuation_scope: "both",
        },
        outputDeltas: [],
        end: {
          schema_version: "bb.terminal_session_end.v1",
          terminal_session_id: input.terminalSessionId,
          startup_call_id: null,
          causing_call_id: null,
          terminal_state: "completed",
          exit_code: 0,
          duration_ms: 1,
          artifact_refs: [],
          evidence_refs: [],
        },
      }
    },
    snapshotTerminalRegistry: async () => ({
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: "stale-cleaned-snapshot",
      active_sessions: [],
      ended_session_ids: [...staleEndedIds],
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: input.sessionIds ?? [],
      failed_session_ids: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [driver] })
  const startOperation = {
    kind: "terminal_start" as const,
    capability: cap,
    placement: place,
    input: { terminalSessionId: sessionId, command: ["bash"] },
  }

  const firstStart = await world.execute(startOperation)
  assert.equal(firstStart.kind, "terminal_start")
  if (firstStart.kind !== "terminal_start") throw new Error("expected terminal start")
  assert.ok(firstStart.result)
  const cleanup = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-stale-snapshot",
      scope: "single",
      sessionIds: [sessionId],
    },
  })
  assert.equal(cleanup.kind, "terminal_cleanup")
  if (cleanup.kind !== "terminal_cleanup") throw new Error("expected terminal cleanup")
  assert.deepEqual(cleanup.result?.cleaned_session_ids, [sessionId])
  const snapshot = await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  assert.equal(snapshot.kind, "terminal_snapshot")
  if (snapshot.kind !== "terminal_snapshot") throw new Error("expected terminal snapshot")
  assert.deepEqual(snapshot.result?.ended_session_ids, [])
  const secondStart = await world.execute(startOperation)
  assert.equal(secondStart.kind, "terminal_start")
  if (secondStart.kind !== "terminal_start") throw new Error("expected terminal start")
  assert.ok(secondStart.result)
  const freshEndedSnapshot = await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  assert.equal(freshEndedSnapshot.kind, "terminal_snapshot")
  if (freshEndedSnapshot.kind !== "terminal_snapshot") {
    throw new Error("expected terminal snapshot")
  }
  assert.deepEqual(freshEndedSnapshot.result?.ended_session_ids, [sessionId])
  const cleanupFreshEnd = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-fresh-end",
      scope: "single",
      sessionIds: [sessionId],
    },
  })
  assert.equal(cleanupFreshEnd.kind, "terminal_cleanup")
  malformedStart = true
  const malformedStartResult = await world.execute(startOperation)
  assert.equal(malformedStartResult.kind, "terminal_start")
  if (malformedStartResult.kind !== "terminal_start") {
    throw new Error("expected terminal start")
  }
  assert.equal(malformedStartResult.result, null)
  const staleAfterMalformedCleanup = await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  assert.equal(staleAfterMalformedCleanup.kind, "terminal_snapshot")
  if (staleAfterMalformedCleanup.kind !== "terminal_snapshot") {
    throw new Error("expected terminal snapshot")
  }
  assert.deepEqual(staleAfterMalformedCleanup.result?.ended_session_ids, [])
  staleEndedIds.clear()
  await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  staleEndedIds.add(sessionId)
  const reintroducedAfterAcknowledgement = await world.execute({
    kind: "terminal_snapshot",
    capability: cap,
    placement: place,
  })
  assert.equal(reintroducedAfterAcknowledgement.kind, "terminal_snapshot")
  if (reintroducedAfterAcknowledgement.kind !== "terminal_snapshot") {
    throw new Error("expected terminal snapshot")
  }
  assert.deepEqual(
    reintroducedAfterAcknowledgement.result?.ended_session_ids,
    [sessionId],
  )
  assert.equal(starts, 3)
})


test("execution world unpinned terminal select does not choose sandbox-only driver when supportsTerminalSessions returns true but no terminal operations are implemented", async () => {
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-no-methods-1",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-no-methods-1",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  // Driver claims to support terminals, but implements no terminal methods
  const sandboxOnlyDriver: TerminalSessionDriverV1 = {
    driverId: "sandbox-only-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    execute: async (req) => ({
      schema_version: "bb.sandbox_result.v1",
      request_id: req.request_id,
      status: "completed",
      exit_code: 0,
      duration_ms: 5,
      stdout_text: "done\n",
      stdout_truncated: false,
      stderr_text: "",
      stderr_truncated: false,
      output_files: [],
      evidence_refs: [],
      error: null,
    }),
  }

  const world = createExecutionWorld({ drivers: [sandboxOnlyDriver] })

  const startRes = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-no-methods-1",
      command: ["bash"],
    },
  })

  assert.equal(startRes.kind, "terminal_start")
  assert.equal(startRes.result, null)
  assert.ok(startRes.unsupportedCase !== null)
  assert.equal(startRes.unsupportedCase?.reason_code, "unsupported_terminal_driver")
})

test("execution world generic terminal selection requires a complete terminal driver", () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-partial-terminal",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-partial-terminal",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const partialDriver: TerminalSessionDriverV1 = {
    driverId: "partial-terminal",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: input.scope,
      cleaned_session_ids: [],
      failed_session_ids: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [partialDriver] })
  assert.equal(world.select({ capability, placement, terminal: true }).driverId, null)
})

test("execution world rejects explicit interaction and cleanup pins that differ from the session owner", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-owner-pin",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-owner-pin",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const cleanupCalls: string[] = []
  const interactionCalls: string[] = []
  const makeDriver = (driverId: string): TerminalSessionDriverV1 => ({
    driverId,
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        command: input.command,
        capability_id: capability.capability_id,
        placement_id: placement.placement_id,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => {
      interactionCalls.push(driverId)
      return {
        interaction: {
          schema_version: "bb.terminal_interaction.v1",
          terminal_session_id: input.terminalSessionId,
          interaction_kind: input.interactionKind,
        },
        outputDeltas: [],
      }
    },
    cleanupTerminalSessions: async (input) => {
      cleanupCalls.push(driverId)
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        failed_session_ids: [],
      }
    },
  })
  const world = createExecutionWorld({ drivers: [makeDriver("owner-driver"), makeDriver("pinned-driver")] })
  await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    driverId: "owner-driver",
    input: { terminalSessionId: "term-owner-pin", command: ["bash"] },
  })
  const interaction = await world.execute({
    kind: "terminal_interact",
    capability,
    placement,
    driverId: "pinned-driver",
    input: { terminalSessionId: "term-owner-pin", interactionKind: "poll" },
  })
  assert.equal(interaction.kind, "terminal_interact")
  assert.equal(interaction.result, null)
  assert.deepEqual(interactionCalls, [])
  const result = await world.execute({
    kind: "terminal_cleanup",
    capability,
    placement,
    driverId: "pinned-driver",
    input: { cleanupId: "cleanup-owner-pin", scope: "single", sessionIds: ["term-owner-pin"] },
  })
  assert.equal(result.kind, "terminal_cleanup")
  assert.deepEqual(result.result?.cleaned_session_ids, [])
  assert.deepEqual(result.result?.failed_session_ids, ["term-owner-pin"])
  assert.deepEqual(cleanupCalls, [])
})

test("execution world snapshot merges ended IDs only for the selected driver", async () => {
  const localCapability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-snapshot-local",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const localPlacement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-snapshot-local",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: localCapability.capability_id,
  }
  const ociCapability: ExecutionCapabilityV1 = {
    ...localCapability,
    capability_id: "cap-snapshot-oci",
    security_tier: "single_tenant",
    isolation_class: "oci",
  }
  const ociPlacement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-snapshot-oci",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: ociCapability.capability_id,
  }
  const makeFailingStartDriver = (
    driverId: string,
    supportedPlacement: ExecutionPlacementV1["placement_class"],
  ): TerminalSessionDriverV1 => ({
    driverId,
    supportedPlacements: [supportedPlacement],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: `${input.terminalSessionId}-invalid`,
        command: input.command,
        capability_id: input.capability?.capability_id ?? null,
        placement_id: input.placement?.placement_id ?? null,
        persistence_scope: "thread",
        continuation_scope: "both",
        stream_mode: "pipes",
        stream_split: "stdout_stderr",
        public_handles: [],
      },
      outputDeltas: [],
    }),
    snapshotTerminalRegistry: async () => ({
      schema_version: "bb.terminal_registry_snapshot.v1",
      snapshot_id: `snapshot-${driverId}`,
      active_sessions: [],
      ended_session_ids: [],
    }),
    cleanupTerminalSessions: async () => {
      throw new Error("retain ended owner for retry")
    },
  })
  const world = createExecutionWorld({
    drivers: [
      makeFailingStartDriver("snapshot-local", "local_process"),
      makeFailingStartDriver("snapshot-oci", "local_oci"),
    ],
  })
  await world.execute({
    kind: "terminal_start",
    capability: localCapability,
    placement: localPlacement,
    driverId: "snapshot-local",
    input: { terminalSessionId: "term-snapshot-local", command: ["bash"] },
  })
  await world.execute({
    kind: "terminal_start",
    capability: ociCapability,
    placement: ociPlacement,
    driverId: "snapshot-oci",
    input: { terminalSessionId: "term-snapshot-oci", command: ["bash"] },
  })
  const result = await world.execute({
    kind: "terminal_snapshot",
    capability: localCapability,
    placement: localPlacement,
  })
  assert.equal(result.kind, "terminal_snapshot")
  assert.deepEqual(result.result?.ended_session_ids, ["term-snapshot-local"])
})

test("execution world scope-all adopts selected-driver snapshot sessions and ignores foreign confirmations", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-scope-all-discovered",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-scope-all-discovered",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  const discoveredId = "term-discovered-by-snapshot"
  const discoveredEndedId = "term-ended-discovered-by-snapshot"
  const foreignId = "term-owned-by-foreign-driver"
  const cleanupInputs: string[][] = []
  let snapshotCalls = 0

  const descriptor = (sessionId: string): TerminalSessionDescriptorV1 => ({
    schema_version: "bb.terminal_session_descriptor.v1",
    terminal_session_id: sessionId,
    startup_call_id: null,
    owner_task_id: null,
    public_handles: [],
    command: ["bash"],
    cwd: null,
    stream_mode: "pipes",
    stream_split: "stdout_stderr",
    capability_id: capability.capability_id,
    placement_id: placement.placement_id,
    persistence_scope: "thread",
    continuation_scope: "both",
  })

  const selectedDriver: TerminalSessionDriverV1 = {
    driverId: "selected-snapshot-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: descriptor(input.terminalSessionId),
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
    }),
    snapshotTerminalRegistry: async () => {
      snapshotCalls++
      return {
        schema_version: "bb.terminal_registry_snapshot.v1",
        snapshot_id: `snapshot-${snapshotCalls}`,
        active_sessions: snapshotCalls === 1 ? [descriptor(discoveredId)] : [],
        ended_session_ids: snapshotCalls === 1 ? [discoveredEndedId, foreignId] : [],
      }
    },
    cleanupTerminalSessions: async (input) => {
      cleanupInputs.push([...(input.sessionIds ?? [])])
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: [...(input.sessionIds ?? []), foreignId],
        failed_session_ids: [foreignId],
      }
    },
  }
  const foreignDriver: TerminalSessionDriverV1 = {
    driverId: "foreign-snapshot-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => ({
      descriptor: descriptor(input.terminalSessionId),
      outputDeltas: [],
    }),
    interactTerminalSession: async (input) => ({
      interaction: {
        schema_version: "bb.terminal_interaction.v1",
        terminal_session_id: input.terminalSessionId,
        startup_call_id: null,
        causing_call_id: null,
        interaction_kind: input.interactionKind,
        input_b64: null,
        signal: null,
      },
      outputDeltas: [],
    }),
  }
  const world = createExecutionWorld({ drivers: [selectedDriver, foreignDriver] })

  await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    driverId: foreignDriver.driverId,
    input: { terminalSessionId: foreignId, command: ["bash"] },
  })

  const cleanup = await world.execute({
    kind: "terminal_cleanup",
    capability,
    placement,
    input: { cleanupId: "cleanup-discovered-snapshot", scope: "all" },
  })
  assert.equal(cleanup.kind, "terminal_cleanup")
  assert.deepEqual(cleanup.result?.cleaned_session_ids, [discoveredId, discoveredEndedId])
  assert.deepEqual(cleanup.result?.failed_session_ids, [])
  assert.deepEqual(cleanupInputs, [[discoveredId, discoveredEndedId]])

  const foreignInteraction = await world.execute({
    kind: "terminal_interact",
    capability,
    placement,
    input: { terminalSessionId: foreignId, interactionKind: "poll" },
  })
  assert.equal(foreignInteraction.kind, "terminal_interact")
  assert.equal(foreignInteraction.driverId, foreignDriver.driverId)
  assert.ok(foreignInteraction.result)
})

test("execution world does not retain a session ID after an empty-command start rejection", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-prelaunch-validation",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
    allow_run_programs: ["sh"],
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-prelaunch-validation",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: capability.capability_id,
  }
  let startCalls = 0
  const driver: TerminalSessionDriverV1 = {
    driverId: "prelaunch-validation",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => {
      startCalls++
      return {
        descriptor: {
          schema_version: "bb.terminal_session_descriptor.v1",
          terminal_session_id: input.terminalSessionId,
          capability_id: capability.capability_id,
          placement_id: placement.placement_id,
          command: input.command,
          stream_mode: "pipes",
          persistence_scope: "until_cleanup",
          continuation_scope: "model",
        },
        outputDeltas: [],
      }
    },
  }
  const world = createExecutionWorld({ drivers: [driver] })

  const rejected = await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    input: { terminalSessionId: "term-prelaunch-validation", command: [] },
  })
  const disallowed = await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    input: {
      terminalSessionId: "term-prelaunch-validation",
      command: ["node"],
    },
  })
  const accepted = await world.execute({
    kind: "terminal_start",
    capability,
    placement,
    input: {
      terminalSessionId: "term-prelaunch-validation",
      command: ["sh"],
    },
  })

  assert.equal(rejected.kind, "terminal_start")
  assert.equal(rejected.result, null)
  assert.equal(rejected.unsupportedCase?.reason_code, "terminal_start_failed")
  assert.equal(disallowed.kind, "terminal_start")
  assert.equal(disallowed.result, null)
  assert.match(
    disallowed.unsupportedCase?.summary ?? "",
    /program "node" is not allowed/,
  )
  assert.equal(accepted.kind, "terminal_start")
  assert.equal(accepted.result?.descriptor.terminal_session_id, "term-prelaunch-validation")
  assert.equal(startCalls, 1)
})

test("default sandbox artifact root is anchored in the user home", () => {
  assert.equal(
    canonicalSandboxArtifactRoot().startsWith(`${join(homedir(), ".breadboard")}/`),
    true,
  )
})


test("canonical sandbox artifact roots are user-specific children", () => {
  const parent = join(tmpdir(), "shared-parent")
  const root = canonicalSandboxArtifactRoot(parent)

  assert.equal(root.startsWith(`${parent}/`), true)
  assert.match(root, /breadboard-sandbox-artifacts-(?:uid-\d+|current-user)$/)
})

test("canonical sandbox artifact resolution preserves safe legacy content", async () => {
  const legacyRoot = join(tmpdir(), "breadboard-sandbox-artifacts")
  const content = `legacy sandbox output ${process.pid} ${Date.now()}\n`
  const ref = `sha256:${createHash("sha256").update(content).digest("hex")}`
  const artifactName = ref.slice("sha256:".length)
  try {
    await mkdir(legacyRoot, { recursive: true, mode: 0o700 })
    await chmod(legacyRoot, 0o700)
    await writeFile(join(legacyRoot, artifactName), content, { mode: 0o600 })

    const uri = canonicalSandboxArtifactUri(ref)

    assert.equal(uri, new URL(`file://${join(legacyRoot, artifactName)}`).href)
    assert.equal(await readFile(new URL(uri), "utf8"), content)
  } finally {
    await rm(join(legacyRoot, artifactName), { force: true })
  }
})

test("canonical sandbox artifact resolution rejects shared-parent candidates", async () => {
  const parent = await mkdtemp(join(tmpdir(), "breadboard-cas-parent-test-"))
  const content = "private sandbox output\n"
  const ref = "sha256:1b196a8697e865cf57eb23b35220bd2b0434d8396d25be4254fa96986451c91a"
  try {
    await chmod(parent, 0o755)
    await writeFile(join(parent, ref.slice("sha256:".length)), "attacker bytes", {
      mode: 0o600,
    })
    const nestedRoot = canonicalSandboxArtifactRoot(parent)
    await persistCanonicalSandboxArtifact(ref, content, nestedRoot)

    const uri = canonicalSandboxArtifactUri(ref, parent)

    assert.equal(await readFile(new URL(uri), "utf8"), content)
    assert.equal(new URL(uri).pathname.startsWith(`${nestedRoot}/`), true)
  } finally {
    await rm(parent, { recursive: true, force: true })
  }
})

test("canonical sandbox artifacts are owner-only and content-addressed", async () => {

  const artifactRoot = await mkdtemp(join(tmpdir(), "breadboard-cas-test-"))
  try {
    await chmod(artifactRoot, 0o755)
    const content = "private sandbox output\n"
    const ref = "sha256:1b196a8697e865cf57eb23b35220bd2b0434d8396d25be4254fa96986451c91a"

    const uri = await persistCanonicalSandboxArtifact(ref, content, artifactRoot)

    assert.equal(uri, canonicalSandboxArtifactUri(ref, artifactRoot))
    assert.equal(await readFile(new URL(uri), "utf8"), content)
    assert.equal((await stat(artifactRoot)).mode & 0o077, 0)
    assert.equal((await stat(new URL(uri))).mode & 0o077, 0)
  } finally {
    await rm(artifactRoot, { recursive: true, force: true })
  }
})

test("canonical sandbox evidence persists its advertised manifest", async () => {
  const artifactRoot = await mkdtemp(join(tmpdir(), "breadboard-evidence-test-"))
  try {
    const evidence = await buildAndPersistCanonicalSandboxEvidence({
      command: ["printf", "world"],
      status: "completed",
      exitCode: 0,
      stdout: "world",
      stderr: "",
      evidenceMode: "replay_strict",
    }, artifactRoot)

    assert.deepEqual(evidence.evidence_refs, [evidence.side_effect_digest])
    const manifest = JSON.parse(
      await readFile(
        new URL(canonicalSandboxArtifactUri(evidence.side_effect_digest, artifactRoot)),
        "utf8",
      ),
    )
    assert.deepEqual(manifest, {
      command: ["printf", "world"],
      exit_code: 0,
      status: "completed",
      stderr_ref: evidence.stderr_ref,
      stdout_ref: evidence.stdout_ref,
    })
  } finally {
    await rm(artifactRoot, { recursive: true, force: true })
  }
})
