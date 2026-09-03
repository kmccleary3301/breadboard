import test from "node:test"
import assert from "node:assert/strict"
import type { ExecutionCapabilityV1, ExecutionPlacementV1, SandboxRequestV1, SandboxResultV1 } from "@breadboard/kernel-contracts"
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
      assert.equal(driverId, "local-process")
      assert.equal(liveness.state, "timed_out")
    },
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(timeoutNotified, true)
  assert.equal(terminateCalled, true)
  assert.equal(executeAborted, true)
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
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
  abortController.abort(new Error("external abort"))
  const asyncResult = await asyncPromise
  assert.equal(asyncResult.kind, "sandbox")
  assert.equal(asyncResult.sandboxResult?.status, "cancelled")
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
        session_id: "sess-1",
        active_mode: "dev",
        driver_id: "local-term",
        placement_class: "local_process",
        runtime_id: "local",
        capability_id: input.capability?.capability_id ?? "cap-local",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
        terminal_state: "running",
        created_at_utc: new Date().toISOString(),
        started_at_utc: new Date().toISOString(),
        ended_at_utc: null,
        public_handles: [],
        metadata: {},
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      localCleaned.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        retained_session_ids: [],
        errors: [],
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
        session_id: "sess-1",
        active_mode: "dev",
        driver_id: "oci-term",
        placement_class: "local_oci",
        runtime_id: "oci",
        capability_id: input.capability?.capability_id ?? "cap-oci",
        command: input.command,
        stream_mode: "pipes",
        persistence_scope: "until_cleanup",
        continuation_scope: "model",
        terminal_state: "running",
        created_at_utc: new Date().toISOString(),
        started_at_utc: new Date().toISOString(),
        ended_at_utc: null,
        public_handles: [],
        metadata: {},
      },
      outputDeltas: [],
    }),
    cleanupTerminalSessions: async (input) => {
      ociCleaned.push(...(input.sessionIds ?? []))
      return {
        schema_version: "bb.terminal_cleanup_result.v1",
        cleanup_id: input.cleanupId,
        scope: input.scope,
        cleaned_session_ids: input.sessionIds ?? [],
        retained_session_ids: [],
        errors: [],
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
