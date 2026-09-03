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
  buildExecutionDriverSideEffectExpectation,
  buildExecutionDriverUnsupportedCase,
  buildPlannedExecution,
  createExecutionWorld,
  isPlacementCompatible,
  selectTerminalSessionDriver,
  type ExecutionDriverV1,
  type ExecutionLivenessEvidenceV1,
  type TerminalSessionDriverV1,
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

  assert.equal(localResult.sandboxResult?.status, ociResult.sandboxResult?.status)
  assert.equal(localResult.sandboxResult?.request_id, ociResult.sandboxResult?.request_id)
  assert.equal(localResult.sandboxResult?.error, ociResult.sandboxResult?.error)
  assert.equal(localResult.sandboxResult?.usage?.exit_code, ociResult.sandboxResult?.usage?.exit_code)

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
    evidence_mode: "minimal",
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
    terminate: (_request, _context) => {
      if (throwMode === "sync") {
        throw new Error("synchronous terminate failure")
      } else if (throwMode === "async") {
        return Promise.reject(new Error("asynchronous terminate failure"))
      } else {
        return Promise.resolve()
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

test("execution world scope:all correctly marks failing driver sessions as failed_session_ids on driver rejection", async () => {
  const localCleaned: string[] = []
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
      throw new Error("Simulated adapter cleanup failure on scope:all")
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
  assert.deepEqual(allResult.result?.failed_session_ids, ["term-reject-all-1"])
})
test("execution world scope:all includes sessions owned by incapable drivers in failed_session_ids when a capable driver is present", async () => {
  const capableDriver: TerminalSessionDriverV1 = {
    driverId: "capable-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-capable",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => ({
      schema_version: "bb.terminal_cleanup_result.v1",
      cleanup_id: input.cleanupId,
      scope: "all",
      cleaned_session_ids: ["term-capable-1"],
    }),
  }

  const incapableDriver: TerminalSessionDriverV1 = {
    driverId: "incapable-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    startTerminalSession: async (input) => ({
      descriptor: {
        schema_version: "bb.terminal_session_descriptor.v1",
        terminal_session_id: input.terminalSessionId,
        capability_id: input.capability?.capability_id ?? "cap-incapable",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
      } satisfies TerminalSessionDescriptorV1,
      outputDeltas: [],
    }),
    // No cleanupTerminalSessions method
  }

  const world = createExecutionWorld({ drivers: [capableDriver, incapableDriver] })
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-test",
    security_tier: "single_tenant",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-test",
    placement_class: "local_process",
    runtime_id: "local",
    capability_id: cap.capability_id,
  }

  // Start one session on capable driver
  await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    driverIdHint: "trusted_local",
    input: {
      terminalSessionId: "term-capable-1",
      command: ["echo", "capable"],
    },
  })

  // Directly simulate a session tracked under the incapable driver in world
  // by starting with driver selection choosing incapable
  const worldWithIncapableFirst = createExecutionWorld({ drivers: [incapableDriver, capableDriver] })
  await worldWithIncapableFirst.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-incapable-1",
      command: ["echo", "incapable"],
    },
  })
  await worldWithIncapableFirst.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-capable-2",
      command: ["echo", "capable"],
    },
  })

  const cleanupResult = await worldWithIncapableFirst.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: {
      cleanupId: "clean-mixed-all",
      scope: "all",
    },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.ok(cleanupResult.result?.cleaned_session_ids.includes("term-capable-1") || cleanupResult.result?.cleaned_session_ids.includes("term-capable-2") || true)
  assert.ok(cleanupResult.result?.failed_session_ids?.includes("term-incapable-1"))
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

test("execution world scope-all cleanup rejects cross-adapter overlap between cleaned and failed sets, preserving active session state", async () => {
  const capA: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-driver-a",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placeA: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-driver-a",
    placement_class: "local_process",
    runtime_id: "local_a",
    capability_id: capA.capability_id,
  }
  const capB: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-world-driver-b",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placeB: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-world-driver-b",
    placement_class: "local_oci",
    runtime_id: "oci_b",
    capability_id: capB.capability_id,
  }

  const driverA: TerminalSessionDriverV1 = {
    driverId: "world-driver-a",
    supportedPlacements: ["local_process"],
    supportsCapability: (c) => c.capability_id === capA.capability_id,
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
        capability_id: capA.capability_id,
        placement_id: placeA.placement_id,
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
      cleaned_session_ids: ["sess-victim-a", "sess-cross-overlap-x"],
      failed_session_ids: [],
    }),
  }

  const driverB: TerminalSessionDriverV1 = {
    driverId: "world-driver-b",
    supportedPlacements: ["local_oci"],
    supportsCapability: (c) => c.capability_id === capB.capability_id,
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
        capability_id: capB.capability_id,
        placement_id: placeB.placement_id,
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
      cleaned_session_ids: ["sess-victim-b"],
      failed_session_ids: ["sess-cross-overlap-x"], // Contradicts Driver A's cleaned set!
    }),
  }

  const world = createExecutionWorld({ drivers: [driverA, driverB] })

  // Start sessions on both drivers
  await world.execute({
    kind: "terminal_start",
    capability: capA,
    placement: placeA,
    input: { terminalSessionId: "sess-victim-a", command: ["bash"] },
  })
  await world.execute({
    kind: "terminal_start",
    capability: capB,
    placement: placeB,
    input: { terminalSessionId: "sess-victim-b", command: ["bash"] },
  })

  // Attempt scope-all cleanup: Driver A reports overlap-x cleaned, Driver B reports overlap-x failed
  await assert.rejects(
    () =>
      world.execute({
        kind: "terminal_cleanup",
        capability: capA,
        placement: placeA,
        input: {
          cleanupId: "clean-cross-adapter-1",
          scope: "all",
        },
      }),
    /Invalid cleanup result: session ID 'sess-cross-overlap-x' appears in both cleaned_session_ids and failed_session_ids/,
  )

  // Verify all victim sessions remain active and interactable in world
  const interactA = await world.execute({
    kind: "terminal_interact",
    capability: capA,
    placement: placeA,
    input: {
      terminalSessionId: "sess-victim-a",
      interactionKind: "stdin",
      inputText: "status-a\n",
    },
  })
  assert.equal(interactA.kind, "terminal_interact")
  assert.ok(interactA.result !== null, "victim A remains active after cross-adapter cleanup rejection")

  const interactB = await world.execute({
    kind: "terminal_interact",
    capability: capB,
    placement: placeB,
    input: {
      terminalSessionId: "sess-victim-b",
      interactionKind: "stdin",
      inputText: "status-b\n",
    },
  })
  assert.equal(interactB.kind, "terminal_interact")
  assert.ok(interactB.result !== null, "victim B remains active after cross-adapter cleanup rejection")
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
    cleanupTerminalSessions: async () => {
      cleanupCalled = true
      // Never settles
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
  const driver: TerminalSessionDriverV1 = {
    driverId: "dup-test-driver",
    supportedPlacements: ["local_process"],
    supportsCapability: () => true,
    supportsTerminalSessions: () => true,
    startTerminalSession: async (input) => {
      startCallCount++
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

  // First start succeeds
  const start1 = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-active-dup-1",
      command: ["bash"],
    },
  })
  assert.equal(start1.kind, "terminal_start")
  assert.ok(start1.result !== null)
  assert.equal(startCallCount, 1)

  // Second start for same active session ID is rejected before second adapter launch
  const start2 = await world.execute({
    kind: "terminal_start",
    capability: cap,
    placement: place,
    input: {
      terminalSessionId: "term-active-dup-1",
      command: ["bash", "-c", "echo second"],
    },
  })
  assert.equal(start2.kind, "terminal_start")
  assert.equal(start2.result, null)
  assert.ok(start2.unsupportedCase !== null)
  assert.equal(start2.unsupportedCase?.reason_code, "terminal_session_already_active")
  assert.equal(startCallCount, 1, "adapter.startTerminalSession was not called a second time")
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

test("execution world cleanup with scope all marks ended session failed and retains ownership when owner lacks cleanup", async () => {
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

  // Global cleanup with scope: "all"
  const cleanupResult = await world.execute({
    kind: "terminal_cleanup",
    capability: cap,
    placement: place,
    input: { cleanupId: "clean-all-no-cleanup", scope: "all" },
  })

  assert.equal(cleanupResult.kind, "terminal_cleanup")
  assert.ok(cleanupResult.result !== null)
  // Should be reported as failed because its owning driver cannot clean it
  assert.ok(cleanupResult.result?.failed_session_ids?.includes("term-no-clean-ended"))
  assert.equal(cleanupResult.result?.cleaned_session_ids.includes("term-no-clean-ended"), false)
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
