import fs from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"
import assert from "node:assert/strict"

import type { ExecutionCapabilityV1, ExecutionPlacementV1 } from "@breadboard/kernel-contracts"
import { canonicalSandboxArtifactUri } from "@breadboard/execution-drivers"
import {
  buildOciContainerName,
  buildOciRuntimeInvocation,
  buildOciSandboxRequest,
  chooseOciPlacement,
  defaultOciCommandExecutor,
  executeOciSandboxRequest,
  makeConfiguredOciExecutionDriver,
  makeOciExecutionDriver,
  makeOciTerminalSessionDriver,
  ociExecutionDriver,
  OciTerminalSessionManager,
  type OciCommandExecutor,
  type OciTerminalSessionAdapter,
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


test("oci direct execution rejects a pre-aborted request before invoking the runtime", async () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-pre-aborted",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-pre-aborted",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["touch", "/tmp/should-not-exist"],
    imageRef: "docker://alpine:latest",
  })
  const controller = new AbortController()
  controller.abort("deadline")
  let executorCalls = 0

  const result = await executeOciSandboxRequest(request, {
    signal: controller.signal,
    commandExecutor: async () => {
      executorCalls++
      return { exitCode: 0, stdout: "", stderr: "" }
    },
  })

  assert.equal(executorCalls, 0)
  assert.equal(result.status, "timed_out")
  assert.equal(result.error?.reason, "execution_timed_out")
})


test("oci direct execution classifies standard timeout abort reasons", async () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-timeout-abort",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-timeout-abort",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "1"],
    imageRef: "docker://alpine:latest",
  })
  const controller = new AbortController()
  controller.abort(new DOMException("The operation timed out", "TimeoutError"))
  let executorCalls = 0

  const result = await executeOciSandboxRequest(request, {
    signal: controller.signal,
    commandExecutor: async () => {
      executorCalls++
      return { exitCode: 0, stdout: "", stderr: "" }
    },
  })

  assert.equal(executorCalls, 0)
  assert.equal(result.status, "timed_out")
  assert.equal(result.error?.reason, "execution_timed_out")
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
  const artifactRoot = fs.mkdtempSync(join(tmpdir(), "bb-oci-artifacts-"))
  const result = await executeOciSandboxRequest(request, {
    commandExecutor: async ({ runtimeCommand, runtimeArgs }) => ({
      exitCode: runtimeCommand === "docker" && runtimeArgs.includes("ghcr.io/example/ruff:latest") ? 0 : 1,
      stdout: "oci ok",
      stderr: "",
    }),
    tempDirRoot: artifactRoot,
  })
  assert.equal(result.status, "completed")
  if (!result.stdout_ref) assert.fail("OCI execution must return stdout evidence")
  assert.match(result.stdout_ref, /^sha256:/)
  assert.equal(
    fs.readFileSync(
      new URL(canonicalSandboxArtifactUri(result.stdout_ref, artifactRoot)),
      "utf8",
    ),
    "oci ok",
  )
  assert.ok(result.side_effect_digest?.startsWith("sha256:"))
})

test("defaultOciCommandExecutor removes abort listener after child close", async () => {
  let registeredListener: unknown
  let removals = 0
  const signal = {
    aborted: false,
    reason: undefined,
    addEventListener: (_type: string, listener: unknown) => {
      registeredListener = listener
    },
    removeEventListener: (_type: string, listener: unknown) => {
      if (listener === registeredListener) removals++
    },
  } as unknown as AbortSignal

  await defaultOciCommandExecutor({
    runtimeCommand: process.execPath,
    runtimeArgs: ["-e", ""],
    signal,
  })

  assert.equal(removals, 1)
})


test("defaultOciCommandExecutor removes abort listener after child error", async () => {
  let registeredListener: unknown
  let removals = 0
  const signal = {
    aborted: false,
    reason: undefined,
    addEventListener: (_type: string, listener: unknown) => {
      registeredListener = listener
    },
    removeEventListener: (_type: string, listener: unknown) => {
      if (listener === registeredListener) removals++
    },
  } as unknown as AbortSignal

  await assert.rejects(
    defaultOciCommandExecutor({
      runtimeCommand: "/breadboard-command-that-does-not-exist",
      runtimeArgs: [],
      signal,
    }),
  )

  assert.equal(removals, 1)
})


test("OCI direct execution classifies an ordinary abort as cancelled", async () => {
  const request = buildOciSandboxRequest({
    requestId: "oci-cancelled-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-cancelled-1",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    workspaceRef: "/tmp/workspace",
    imageRef: "ghcr.io/example/runtime:latest",
  })
  const controller = new AbortController()
  controller.abort()
  const result = await executeOciSandboxRequest(request, {
    signal: controller.signal,
    commandExecutor: async () => ({ exitCode: 143, stdout: "", stderr: "" }),
  })
  assert.equal(result.status, "cancelled")
  assert.equal(result.error?.reason, "execution_cancelled")
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
      if (interaction.interaction_kind === "stdin") {
        return {
          outputDeltas: [
            {
              schema_version: "bb.terminal_output_delta.v1",
              terminal_session_id: descriptor.terminal_session_id,
              startup_call_id: descriptor.startup_call_id ?? null,
              causing_call_id: interaction.causing_call_id ?? null,
              stream: "stdout",
              chunk_b64: Buffer.from("status: up\n", "utf8").toString("base64"),
              chunk_seq: 1,
            },
          ],
        }
      }
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
    async cleanupSessions({ sessionIds }) {
      return sessionIds
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
  const initialSnapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(initialSnapshot?.active_sessions.length, 1)
  assert.equal(initialSnapshot?.active_sessions[0]?.terminal_session_id, "term-oci-1")
  const stdinResult = await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-1",
    interactionKind: "stdin",
    inputText: "status\n",
    causingCallId: "call-stdin-1",
  })
  assert.equal(stdinResult?.outputDeltas.length, 1)
  assert.match(Buffer.from(stdinResult?.outputDeltas[0]?.chunk_b64 ?? "", "base64").toString("utf8"), /status: up/)
  const interacted = await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-1",
    interactionKind: "poll",
    causingCallId: "call-1",
  })
  assert.equal(interacted?.end?.terminal_state, "completed")
  const postEndSnapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(postEndSnapshot?.active_sessions.length, 0)
  const cleanup = await driver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-oci-1",
    scope: "single",
    sessionIds: ["term-oci-1"],
  })
  assert.deepEqual(cleanup?.cleaned_session_ids, ["term-oci-1"])
})

test("oci terminal support follows runtime selection semantics", async () => {
  const driver = makeOciExecutionDriver({
    async startSession() {
      return { outputDeltas: [] }
    },
    async interactSession() {
      return { outputDeltas: [] }
    },
    async cleanupSessions(request) {
      return {
        cleaned_session_ids: [...request.sessionIds],
        failed_session_ids: [],
      }
    },
  })
  assert.equal(
    driver.supportsTerminalSessions?.(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-term-oci-gvisor",
        security_tier: "shared_host",
        isolation_class: "gvisor",
        secret_mode: "ref_only",
        evidence_mode: "audit_full",
      },
      "local_oci_gvisor",
    ),
    true,
  )
  assert.equal(
    driver.supportsTerminalSessions?.(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-term-oci-kata",
        security_tier: "shared_host",
        isolation_class: "kata",
        secret_mode: "ref_only",
        evidence_mode: "audit_full",
      },
      "local_oci_kata",
    ),
    true,
  )
})

test("oci terminal driver keeps multi-session listing and no-output poll semantics explicit", async () => {
  const driver = makeOciExecutionDriver({
    async startSession({ descriptor }) {
      return {
        outputDeltas: [],
        end:
          descriptor.terminal_session_id === "term-oci-fast-exit"
            ? {
                schema_version: "bb.terminal_session_end.v1",
                terminal_session_id: descriptor.terminal_session_id,
                startup_call_id: descriptor.startup_call_id ?? null,
                causing_call_id: null,
                terminal_state: "completed",
                exit_code: 0,
                duration_ms: 5,
                artifact_refs: [],
                evidence_refs: [],
              }
            : undefined,
      }
    },
    async interactSession({ descriptor, interaction }) {
      return {
        outputDeltas: [],
        end:
          interaction.interaction_kind === "signal"
            ? {
                schema_version: "bb.terminal_session_end.v1",
                terminal_session_id: descriptor.terminal_session_id,
                startup_call_id: descriptor.startup_call_id ?? null,
                causing_call_id: interaction.causing_call_id ?? null,
                terminal_state: "cancelled",
                exit_code: null,
                duration_ms: 8,
                artifact_refs: [],
                evidence_refs: [],
              }
            : undefined,
      }
    },
    async cleanupSessions({ sessionIds }) {
      return sessionIds
    },
  })
  const capability = {
    schema_version: "bb.execution_capability.v1" as const,
    capability_id: "cap-term-oci-multi",
    security_tier: "single_tenant" as const,
    isolation_class: "oci" as const,
    secret_mode: "ref_only" as const,
    evidence_mode: "replay_strict" as const,
  }
  const placement = {
    schema_version: "bb.execution_placement.v1" as const,
    placement_id: "place-term-oci-multi",
    placement_class: "local_oci" as const,
    runtime_id: "oci",
    capability_id: "cap-term-oci-multi",
  }
  await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-keepalive",
    command: ["bash", "-lc", "sleep 10"],
    capability,
    placement,
  })
  const exited = await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-fast-exit",
    command: ["bash", "-lc", "echo done"],
    capability,
    placement,
  })
  assert.equal(exited?.end?.terminal_state, "completed")
  const snapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(snapshot?.active_sessions.length, 1)
  assert.ok((snapshot?.ended_session_ids ?? []).includes("term-oci-fast-exit"))
  assert.equal(snapshot?.active_sessions[0]?.terminal_session_id, "term-oci-keepalive")
  const polled = await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-keepalive",
    interactionKind: "poll",
    causingCallId: "call-oci-poll-no-output",
  })
  assert.equal(polled?.outputDeltas.length, 0)
  assert.equal(polled?.end, undefined)
  const cleanupExited = await driver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-oci-exited",
    scope: "single",
    sessionIds: ["term-oci-fast-exit"],
  })
  assert.deepEqual(cleanupExited?.cleaned_session_ids, ["term-oci-fast-exit"])
  await assert.rejects(
    () =>
      driver.interactTerminalSession?.({
        terminalSessionId: "term-oci-fast-exit",
        interactionKind: "stdin",
        inputText: "status\n",
        causingCallId: "call-oci-ended-stdin",
      }) ?? Promise.resolve(undefined),
    /already ended/,
  )
  const signaled = await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-keepalive",
    interactionKind: "signal",
    signal: "SIGTERM",
    causingCallId: "call-oci-signal-1",
  })
  assert.equal(signaled?.end?.terminal_state, "cancelled")
  const finalSnapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(finalSnapshot?.active_sessions.length, 0)
  assert.ok((finalSnapshot?.ended_session_ids ?? []).includes("term-oci-keepalive"))
  assert.ok((finalSnapshot?.ended_session_ids ?? []).includes("term-oci-fast-exit"))
})

test("oci driver terminate triggers signal and awaits runtime exit", async () => {
  const issuedCommands: { runtimeArgs: string[] }[] = []
  let runtimeExited = false
  const customExecutor: OciCommandExecutor = async ({ runtimeArgs, signal }) => {
    issuedCommands.push({ runtimeArgs })
    if (runtimeArgs[0] === "run") {
      return new Promise((resolve) => {
        signal?.addEventListener("abort", () => {
          runtimeExited = true
          resolve({
            exitCode: 137,
            stdout: "",
            stderr: "killed",
          })
        })
      })
    }
    return {
      exitCode: 0,
      stdout: "",
      stderr: "",
    }
  }
  const driver = makeConfiguredOciExecutionDriver({ commandExecutor: customExecutor })
  const request = buildOciSandboxRequest({
    requestId: "req-oci-term-verify",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-term-v",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    imageRef: "docker://alpine:latest",
  })
  const execPromise = driver.execute!(request)
  assert.equal(runtimeExited, false)
  await driver.terminate!(request, {
    reason: "deadline",
    signal: new AbortController().signal,
    deadlineAtMs: Date.now(),
  })
  assert.equal(runtimeExited, true)
  const result = await execPromise
  assert.equal(result.status, "timed_out")
  assert.ok(issuedCommands.some((c) => c.runtimeArgs.includes("--name") && c.runtimeArgs.some((a) => a.startsWith("bb-oci-req-oci-term-verify"))))
  assert.ok(issuedCommands.some((c) => c.runtimeArgs[0] === "stop" && c.runtimeArgs.some((a) => a.startsWith("bb-oci-req-oci-term-verify"))))
})


test("oci driver rejects termination when runtime exit is not observed", async () => {
  const customExecutor: OciCommandExecutor = async ({ runtimeArgs }) => {
    if (runtimeArgs[0] === "run") {
      return new Promise(() => {})
    }
    return { exitCode: 0, stdout: "", stderr: "" }
  }
  const driver = makeConfiguredOciExecutionDriver({ commandExecutor: customExecutor })
  const request = buildOciSandboxRequest({
    requestId: "req-oci-unobserved-exit",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-unobserved-exit",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    imageRef: "docker://alpine:latest",
  })
  void driver.execute!(request)
  await assert.rejects(
    async () => {
      await driver.terminate!(request, {
        reason: "cancelled",
        signal: new AbortController().signal,
        deadlineAtMs: null,
      })
    },
    /OCI termination was not observed/,
  )
})



test("oci driver handles stubborn container that resists normal stop and forces kill/rm", async () => {
  const recordedArgs: string[][] = []
  const customExecutor: OciCommandExecutor = async ({ runtimeArgs, signal }) => {
    recordedArgs.push(runtimeArgs)
    if (runtimeArgs[0] === "run") {
      return new Promise((resolve) => {
        signal?.addEventListener("abort", () => {
          resolve({
            exitCode: 137,
            stdout: "",
            stderr: "SIGKILL after stubborn run",
          })
        })
      })
    }
    if (runtimeArgs[0] === "stop") {
      throw new Error("container refusing stop")
    }
    return { exitCode: 0, stdout: "", stderr: "" }
  }
  const driver = makeConfiguredOciExecutionDriver({ commandExecutor: customExecutor })
  const request = buildOciSandboxRequest({
    requestId: "req-stubborn-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-stubborn",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    imageRef: "docker://alpine:latest",
  })
  const execPromise = driver.execute!(request)
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: null,
  })
  const result = await execPromise
  assert.equal(result.status, "cancelled")
  assert.ok(recordedArgs.some((args) => args[0] === "kill" && args.some((a) => a.startsWith("bb-oci-req-stubborn-1"))))
  assert.ok(recordedArgs.some((args) => args[0] === "rm" && args.some((a) => a.startsWith("bb-oci-req-stubborn-1"))))
})

test("oci driver terminate creates fresh bounded cleanup signal even when work signal is already aborted", async () => {
  const cleanupSignals: Array<{ arg0: string; signal?: AbortSignal }> = []
  const customExecutor: OciCommandExecutor = async ({ runtimeArgs, signal }) => {
    if (runtimeArgs[0] !== "run") {
      cleanupSignals.push({ arg0: runtimeArgs[0] ?? "", signal })
    }
    if (runtimeArgs[0] === "run") {
      return new Promise((resolve) => {
        signal?.addEventListener("abort", () => {
          resolve({
            exitCode: 137,
            stdout: "",
            stderr: "killed",
          })
        })
      })
    }
    return { exitCode: 0, stdout: "", stderr: "" }
  }
  const driver = makeConfiguredOciExecutionDriver({ commandExecutor: customExecutor })
  const request = buildOciSandboxRequest({
    requestId: "req-cleanup-sig",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-cleanup-sig",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    imageRef: "docker://alpine:latest",
  })
  const execPromise = driver.execute!(request)
  const abortController = new AbortController()
  abortController.abort(new Error("pre-aborted work"))
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: abortController.signal,
    deadlineAtMs: null,
  })
  await execPromise
  assert.ok(cleanupSignals.length >= 2, "received cleanup subprocess invocations")
  for (const call of cleanupSignals) {
    assert.ok(call.signal != null, `cleanup subprocess ${call.arg0} received a signal`)
    assert.equal(call.signal?.aborted, false, `cleanup subprocess ${call.arg0} signal is not pre-aborted`)
  }
})

test("oci driver terminate treats nonzero or hanging stop as failure and executes kill then rm with fresh signals", async () => {
  const invokedCommands: Array<{ command: string; abortedOnArrival: boolean }> = []
  const customExecutor: OciCommandExecutor = async ({ runtimeArgs, signal }) => {
    const cmd = runtimeArgs[0] ?? ""
    invokedCommands.push({ command: cmd, abortedOnArrival: signal?.aborted ?? false })

    if (cmd === "run") {
      return new Promise((resolve) => {
        signal?.addEventListener("abort", () => {
          resolve({ exitCode: 137, stdout: "", stderr: "killed" })
        })
      })
    }
    if (cmd === "stop") {
      // Return nonzero exit code to simulate failed stop
      return { exitCode: 1, stdout: "", stderr: "stop failed: container unresponsive" }
    }
    if (cmd === "kill") {
      return { exitCode: 0, stdout: "", stderr: "" }
    }
    if (cmd === "rm") {
      return { exitCode: 0, stdout: "", stderr: "" }
    }
    return { exitCode: 0, stdout: "", stderr: "" }
  }

  const driver = makeConfiguredOciExecutionDriver({ commandExecutor: customExecutor })
  const request = buildOciSandboxRequest({
    requestId: "req-nonzero-stop",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-nonzero-stop",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    imageRef: "docker://alpine:latest",
  })

  const execPromise = driver.execute!(request)
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: null,
  })
  await execPromise

  const stopCall = invokedCommands.find((c) => c.command === "stop")
  const killCall = invokedCommands.find((c) => c.command === "kill")
  const rmCall = invokedCommands.find((c) => c.command === "rm")

  assert.ok(stopCall, "stop was invoked")
  assert.equal(stopCall?.abortedOnArrival, false, "stop received fresh signal")
  assert.ok(killCall, "kill was invoked following nonzero stop")
  assert.equal(killCall?.abortedOnArrival, false, "kill received fresh signal")
  assert.ok(rmCall, "rm was invoked following kill")
  assert.equal(rmCall?.abortedOnArrival, false, "rm received fresh signal")
})

test("buildOciContainerName produces distinct collision-resistant names for distinct request IDs and valid OCI characters", () => {
  const name1 = buildOciContainerName("run/a")
  const name2 = buildOciContainerName("run_a")
  const name3 = buildOciContainerName("run-a")
  const name4 = buildOciContainerName("run.a")

  assert.notEqual(name1, name2, "run/a and run_a do not collide")
  assert.notEqual(name1, name3, "run/a and run-a do not collide")
  assert.notEqual(name2, name3, "run_a and run-a do not collide")
  assert.notEqual(name3, name4, "run-a and run.a do not collide")

  // Verify valid OCI name syntax: starts with alphanumeric, contains only valid characters
  for (const name of [name1, name2, name3, name4]) {
    assert.match(name, /^[a-zA-Z0-9][a-zA-Z0-9_.-]+$/, `name '${name}' is valid OCI container name`)
    assert.ok(name.length <= 63, `name '${name}' is bounded in length (${name.length} <= 63)`)
  }
})

test("OCI terminal session manager preserves unrequested session after malformed cleanup returns extra IDs", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-manager-extra",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-manager-extra",
    placement_class: "local_oci",
    runtime_id: "local_oci",
    capability_id: capability.capability_id,
  }

  const customAdapter: OciTerminalSessionAdapter = {
    async startSession() {
      return { outputDeltas: [] }
    },
    async interactSession() {
      return { outputDeltas: [] }
    },
    async cleanupSessions() {
      return ["term-oci-target", "term-oci-unrequested-victim"]
    },
  }

  const driver = makeOciTerminalSessionDriver(customAdapter)

  // 1. Start target session and victim session
  await driver.startTerminalSession!({
    terminalSessionId: "term-oci-target",
    command: ["bash"],
    capability,
    placement,
  })
  await driver.startTerminalSession!({
    terminalSessionId: "term-oci-unrequested-victim",
    command: ["bash"],
    capability,
    placement,
  })

  // 2. Perform filtered cleanup targeting only term-oci-target
  const cleanupRes = await driver.cleanupTerminalSessions!({
    cleanupId: "clean-oci-single-1",
    scope: "single",
    sessionIds: ["term-oci-target"],
  })

  assert.deepEqual(cleanupRes.cleaned_session_ids, ["term-oci-target"])

  // 3. Verify unrequested victim session remains active and interactive in manager
  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-oci-unrequested-victim",
    interactionKind: "stdin",
    inputText: "status\n",
  })
  assert.ok(interactRes.interaction != null, "victim session remains active and interactive")
})

test("OCI terminal session manager rejects overlapping cleaned and failed IDs, preserves session active for subsequent interact", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-manager-overlap",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-manager-overlap",
    placement_class: "local_oci",
    runtime_id: "local_oci",
    capability_id: capability.capability_id,
  }

  const customAdapter: OciTerminalSessionAdapter = {
    async startSession() {
      return { outputDeltas: [] }
    },
    async interactSession() {
      return { outputDeltas: [] }
    },
    async cleanupSessions() {
      return {
        cleaned_session_ids: ["term-oci-overlap-target"],
        failed_session_ids: ["term-oci-overlap-target"],
      }
    },
  }

  const driver = makeOciTerminalSessionDriver(customAdapter)

  await driver.startTerminalSession!({
    terminalSessionId: "term-oci-overlap-target",
    command: ["bash"],
    capability,
    placement,
  })

  await assert.rejects(
    () =>
      driver.cleanupTerminalSessions!({
        cleanupId: "clean-oci-overlap-1",
        scope: "single",
        sessionIds: ["term-oci-overlap-target"],
      }),
    /Invalid cleanup result: session ID 'term-oci-overlap-target' appears in both cleaned_session_ids and failed_session_ids/,
  )

  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-oci-overlap-target",
    interactionKind: "stdin",
    inputText: "echo alive\n",
  })
  assert.ok(interactRes.interaction != null, "session remains active and interactive after rejected cleanup")
})

test("OCI terminal session manager rejects wrong-ID end during interact without ending active session, preserving interactability", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-wrong-end-test",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-wrong-end-test",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: capability.capability_id,
  }

  let wrongEndTriggered = true
  const customAdapter: OciTerminalSessionAdapter = {
    startSession: async ({ descriptor }) => ({
      outputDeltas: [],
    }),
    interactSession: async ({ descriptor, interaction }) => {
      if (wrongEndTriggered) {
        wrongEndTriggered = false
        return {
          outputDeltas: [],
          end: {
            schema_version: "bb.terminal_session_end.v1",
            terminal_session_id: "wrong-foreign-session-id",
            terminal_state: "completed",
            exit_code: 0,
            signal: null,
            clean_exit: true,
            reason: "completed",
            occurred_at_utc: new Date().toISOString(),
          },
        }
      }
      return {
        outputDeltas: [],
        end: undefined,
      }
    },
    cleanupSessions: async ({ sessionIds }) => ({
      cleaned_session_ids: sessionIds ? [...sessionIds] : [],
      failed_session_ids: [],
    }),
  }

  const driver = makeOciTerminalSessionDriver(customAdapter)

  await driver.startTerminalSession!({
    terminalSessionId: "term-oci-wrong-end-target",
    command: ["bash"],
    capability,
    placement,
  })

  // Wrong end ID must be rejected
  await assert.rejects(
    () =>
      driver.interactTerminalSession!({
        terminalSessionId: "term-oci-wrong-end-target",
        interactionKind: "stdin",
        inputText: "trigger wrong end\n",
      }),
    /Terminal session end session ID 'wrong-foreign-session-id' does not match interaction session ID 'term-oci-wrong-end-target'/,
  )

  // Session was NOT deleted or marked ended in manager, so retry/subsequent interaction succeeds!
  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-oci-wrong-end-target",
    interactionKind: "stdin",
    inputText: "retry after wrong end rejection\n",
  })
  assert.ok(interactRes.interaction != null, "active session remains interactable after rejected wrong-ID end")
})

test("OCI cleanup response omitting cleaned_session_ids but reporting failed_session_ids derives cleaned = targets - failed, preserving successful targets", async () => {
  const capability: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-failed-only-test",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-failed-only-test",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: capability.capability_id,
  }

  const customAdapter: OciTerminalSessionAdapter = {
    startSession: async ({ descriptor }) => ({
      outputDeltas: [],
    }),
    interactSession: async ({ descriptor, interaction }) => ({
      outputDeltas: [],
    }),
    cleanupSessions: async ({ sessionIds }) => {
      // Omit cleaned_session_ids and report only failed_session_ids: ["term-fail-1"]
      return {
        failed_session_ids: ["term-fail-1"],
      } as unknown as string[]
    },
  }

  const driver = makeOciTerminalSessionDriver(customAdapter)

  await driver.startTerminalSession!({
    terminalSessionId: "term-success-1",
    command: ["bash"],
    capability,
    placement,
  })
  await driver.startTerminalSession!({
    terminalSessionId: "term-fail-1",
    command: ["bash"],
    capability,
    placement,
  })

  const cleanupRes = await driver.cleanupTerminalSessions!({
    cleanupId: "clean-failed-only-1",
    scope: "filtered",
    sessionIds: ["term-success-1", "term-fail-1"],
  })

  assert.ok(cleanupRes)
  assert.deepEqual(cleanupRes.cleaned_session_ids, ["term-success-1"])
  assert.deepEqual(cleanupRes.failed_session_ids, ["term-fail-1"])

  // term-fail-1 remains active for interact, while term-success-1 was cleaned
  const interactFail = await driver.interactTerminalSession!({
    terminalSessionId: "term-fail-1",
    interactionKind: "stdin",
    inputText: "echo still alive\n",
  })
  assert.ok(interactFail.interaction != null, "failed session remains active")

  await assert.rejects(
    () =>
      driver.interactTerminalSession!({
        terminalSessionId: "term-success-1",
        interactionKind: "stdin",
        inputText: "echo cleaned\n",
      }),
    /already ended/,
  )
})

test("OCI driver termination rejects boundedly when execution and cleanup never settle", async () => {
  const invokedCommands: string[] = []
  const neverSettlingExecutor: OciCommandExecutor = async ({ runtimeArgs }) => {
    invokedCommands.push(runtimeArgs[0])
    return new Promise<{ exitCode: number; stdout: string; stderr: string }>(() => {})
  }

  const driver = makeConfiguredOciExecutionDriver({
    commandExecutor: neverSettlingExecutor,
  })

  const request = buildOciSandboxRequest({
    requestId: "req-oci-never-settle",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-never-settle",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "100"],
    imageRef: "docker://breadboard/base:latest",
  })

  const controller = new AbortController()
  const cap = {
    schema_version: "bb.execution_capability.v1" as const,
    capability_id: "cap-oci-never-settle",
    security_tier: "single_tenant" as const,
    isolation_class: "oci" as const,
    secret_mode: "ref_only" as const,
    evidence_mode: "minimal" as const,
  }
  const place = {
    schema_version: "bb.execution_placement.v1" as const,
    placement_id: "place-oci-never-settle",
    placement_class: "local_oci" as const,
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }
  const execPromise = driver.execute!(request, {
    signal: controller.signal,
    deadlineAtMs: null,
    terminationGraceMs: 100,
    capability: cap,
    placement: place,
    driverId: "oci",
  })

  const startMs = Date.now()
  await assert.rejects(
    async () => {
      await driver.terminate!(request, {
        reason: "cancelled",
        signal: controller.signal,
        deadlineAtMs: null,
      })
    },
    /OCI termination was not observed/,
  )
  const durationMs = Date.now() - startMs
  assert.ok(durationMs < 10000, `terminate rejected boundedly in ${durationMs}ms`)
  assert.ok(invokedCommands.includes("stop"), "stop was invoked")
  assert.ok(invokedCommands.includes("kill"), "kill was invoked after stop timed out")
  assert.ok(invokedCommands.includes("rm"), "rm was invoked")
})

test("OCI driver termination continues to kill/rm when commandExecutor throws synchronously during stop", async () => {
  const invokedCommands: string[] = []
  const throwingExecutor: OciCommandExecutor = ({ runtimeArgs }) => {
    const op = runtimeArgs[0]
    invokedCommands.push(op)
    if (op === "stop") {
      throw new Error("Synchronous stop crash")
    }
    return Promise.resolve({ exitCode: 0, stdout: "", stderr: "" })
  }

  const driver = makeConfiguredOciExecutionDriver({
    commandExecutor: throwingExecutor,
  })

  const request = buildOciSandboxRequest({
    requestId: "req-oci-sync-throw",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-sync-throw",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "10"],
    imageRef: "docker://breadboard/base:latest",
  })

  const controller = new AbortController()
  const cap = {
    schema_version: "bb.execution_capability.v1" as const,
    capability_id: "cap-oci-sync-throw",
    security_tier: "single_tenant" as const,
    isolation_class: "oci" as const,
    secret_mode: "ref_only" as const,
    evidence_mode: "minimal" as const,
  }
  const place = {
    schema_version: "bb.execution_placement.v1" as const,
    placement_id: "place-oci-sync-throw",
    placement_class: "local_oci" as const,
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }
  driver.execute!(request, {
    signal: controller.signal,
    deadlineAtMs: null,
    terminationGraceMs: 100,
    capability: cap,
    placement: place,
    driverId: "oci",
  }).catch(() => {})

  await driver.terminate!(request, {
    reason: "cancelled",
    signal: controller.signal,
    deadlineAtMs: null,
  })

  assert.ok(invokedCommands.includes("stop"), "stop was invoked and threw synchronously")
  assert.ok(invokedCommands.includes("kill"), "kill was invoked after stop threw")
  assert.ok(invokedCommands.includes("rm"), "rm was invoked")
})

test("OCI terminals cleanup rejects invalid response shapes with typed error", async () => {
  const invalidAdapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [],
    }),
    interactSession: async () => ({
      outputDeltas: [],
    }),
    cleanupSessions: async () => {
      // Return invalid non-string array inside object
      return {
        cleaned_session_ids: [123 as any],
        failed_session_ids: ["ok"],
      } as any
    },
  }

  const driver = makeOciExecutionDriver(invalidAdapter)
  await driver.startTerminalSession?.({
    terminalSessionId: "term-invalid-cleanup",
    command: ["bash"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-invalid-1",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-oci-invalid-1",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-oci-invalid-1",
    },
  })

  await assert.rejects(
    () =>
      driver.cleanupTerminalSessions?.({
        cleanupId: "clean-1",
        scope: "single",
        sessionIds: ["term-invalid-cleanup"],
      }) ?? Promise.resolve(undefined as any),
    /Invalid cleanup result/,
  )
})

test("OCI terminals start cleans up backend on launched-then-invalid-result", async () => {
  let backendCleaned = false
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [
        {
          schema_version: "bb.terminal_output_delta.v1",
          terminal_session_id: "mismatched-session-id",
          chunk_seq: 1,
          stream: "stdout",
          chunk_b64: Buffer.from("hi").toString("base64"),
          text: "hi",
          bytes_b64: null,
          occurred_at_utc: new Date().toISOString(),
        },
      ],
    }),
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => {
      if (sessionIds.includes("term-launched-invalid-1")) {
        backendCleaned = true
      }
      return sessionIds
    },
  }

  const driver = makeOciExecutionDriver(adapter)
  await assert.rejects(
    () =>
      driver.startTerminalSession?.({
        terminalSessionId: "term-launched-invalid-1",
        command: ["bash"],
        capability: {
          schema_version: "bb.execution_capability.v1",
          capability_id: "cap-1",
          security_tier: "single_tenant",
          isolation_class: "oci",
          secret_mode: "ref_only",
          evidence_mode: "minimal",
        },
        placement: {
          schema_version: "bb.execution_placement.v1",
          placement_id: "place-1",
          placement_class: "local_oci",
          runtime_id: "oci",
          capability_id: "cap-1",
        },
      }) ?? Promise.resolve(undefined as any),
    /Terminal output delta session ID/,
  )

  assert.equal(backendCleaned, true, "backend was cleaned up after startSession returned invalid result")
})

test("OCI terminals cleanup without adapter cleanupSessions reports active sessions as failed", async () => {
  const adapterWithoutCleanup: OciTerminalSessionAdapter = {
    startSession: async () => ({ outputDeltas: [] }),
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => ({
      cleaned_session_ids: [],
      failed_session_ids: sessionIds ? [...sessionIds] : [],
    }),
  }

  const driver = makeOciExecutionDriver(adapterWithoutCleanup)
  await driver.startTerminalSession?.({
    terminalSessionId: "term-no-cleanup-1",
    command: ["bash"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-2",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-2",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-2",
    },
  })

  const cleanupResult = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-no-cleanup-1",
    scope: "single",
    sessionIds: ["term-no-cleanup-1"],
  })

  assert.ok(cleanupResult)
  assert.deepEqual(cleanupResult?.cleaned_session_ids, [])
  assert.deepEqual(cleanupResult?.failed_session_ids, ["term-no-cleanup-1"])
})

test("OCI driver rejects start for duplicate active terminal session ID", async () => {
  let startCallCount = 0
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => {
      startCallCount++
      return { outputDeltas: [] }
    },
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => sessionIds,
  }

  const driver = makeOciExecutionDriver(adapter)
  await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-active-dup",
    command: ["bash"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-dup",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-oci-dup",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-oci-dup",
    },
  })
  assert.equal(startCallCount, 1)

  await assert.rejects(
    () =>
      driver.startTerminalSession?.({
        terminalSessionId: "term-oci-active-dup",
        command: ["bash"],
      }) ?? Promise.resolve(undefined as any),
    /OCI terminal session already active/,
  )
  assert.equal(startCallCount, 1, "adapter.startSession was not called second time")
})

test("OCI cleanup with scope all filters foreign returned IDs and does not poison ended_session_ids", async () => {
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({ outputDeltas: [] }),
    interactSession: async () => ({ outputDeltas: [] }),
    // Adapter maliciously returns foreign session IDs not owned by this manager
    cleanupSessions: async ({ sessionIds }) => [...sessionIds, "foreign-rogue-session-999"],
  }

  const driver = makeOciExecutionDriver(adapter)
  await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-owned-1",
    command: ["bash"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-oci-owned",
      security_tier: "single_tenant",
      isolation_class: "oci",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-oci-owned",
      placement_class: "local_oci",
      runtime_id: "oci",
      capability_id: "cap-oci-owned",
    },
  })

  const cleanupResult = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-all-oci",
    scope: "all",
  })

  assert.ok(cleanupResult)
  assert.deepEqual(cleanupResult?.cleaned_session_ids, ["term-oci-owned-1"])
  assert.equal(cleanupResult?.cleaned_session_ids.includes("foreign-rogue-session-999"), false)

  // Check snapshot registry to confirm foreign ID was NOT added to ended_session_ids
  const snapshot = await driver.snapshotTerminalRegistry?.()
  assert.ok(snapshot)
  assert.deepEqual(snapshot?.ended_session_ids, ["term-oci-owned-1"])
  assert.equal(snapshot?.ended_session_ids.includes("foreign-rogue-session-999"), false)
})

test("OCI manager retains and cleans >32 ended sessions without cap failure", async () => {
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({ outputDeltas: [] }),
    interactSession: async ({ descriptor }) => ({
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: descriptor.terminal_session_id,
        startup_call_id: null,
        causing_call_id: null,
        terminal_state: "completed",
        exit_code: 0,
        duration_ms: 10,
        artifact_refs: [],
        evidence_refs: [],
      },
    }),
    cleanupSessions: async ({ sessionIds }) => ({
      cleaned_session_ids: sessionIds ? [...sessionIds] : [],
      failed_session_ids: [],
    }),
  }

  const driver = makeOciExecutionDriver(adapter)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-40",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-40",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  // Start and end 40 sessions
  for (let i = 1; i <= 40; i++) {
    await driver.startTerminalSession?.({
      terminalSessionId: `term-oci-40-${i}`,
      command: ["bash"],
      capability: cap,
      placement: place,
    })
    await driver.interactTerminalSession?.({
      terminalSessionId: `term-oci-40-${i}`,
      interactionKind: "poll",
    })
  }

  // Cleanup scope all must clean all 40 ended sessions
  const cleanupResult = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-oci-40",
    scope: "all",
  })

  assert.ok(cleanupResult)
  assert.equal(cleanupResult.cleaned_session_ids.length, 40)
  assert.deepEqual(cleanupResult.failed_session_ids, [])
  for (let i = 1; i <= 40; i++) {
    assert.ok(cleanupResult.cleaned_session_ids.includes(`term-oci-40-${i}`))
  }
})

test("OCI manager same-ID successful restart removes ID from ended_session_ids in registry snapshot", async () => {
  let endedOnStart = false
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [],
      end: endedOnStart
        ? {
            schema_version: "bb.terminal_session_end.v1",
            terminal_session_id: "term-oci-restart-id",
            startup_call_id: null,
            causing_call_id: null,
            terminal_state: "completed",
            exit_code: 0,
            duration_ms: 10,
            artifact_refs: [],
            evidence_refs: [],
          }
        : undefined,
    }),
    interactSession: async ({ descriptor }) => ({
      outputDeltas: [],
      end: {
        schema_version: "bb.terminal_session_end.v1",
        terminal_session_id: descriptor.terminal_session_id,
        startup_call_id: null,
        causing_call_id: null,
        terminal_state: "completed",
        exit_code: 0,
        duration_ms: 10,
        artifact_refs: [],
        evidence_refs: [],
      },
    }),
    cleanupSessions: async ({ sessionIds }) => ({
      cleaned_session_ids: sessionIds ? [...sessionIds] : [],
      failed_session_ids: [],
    }),
  }

  const driver = makeOciExecutionDriver(adapter)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-restart",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-restart",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  // 1. Start and end session
  await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-restart-id",
    command: ["bash"],
    capability: cap,
    placement: place,
  })
  await driver.interactTerminalSession?.({
    terminalSessionId: "term-oci-restart-id",
    interactionKind: "poll",
  })

  let snap = await driver.snapshotTerminalRegistry?.()
  assert.equal(snap?.active_sessions.length, 0)
  assert.deepEqual(snap?.ended_session_ids, ["term-oci-restart-id"])

  // 2. Restart with same ID (active)
  const restartRes = await driver.startTerminalSession?.({
    terminalSessionId: "term-oci-restart-id",
    command: ["bash"],
    capability: cap,
    placement: place,
  })
  assert.ok(restartRes?.descriptor)
  snap = await driver.snapshotTerminalRegistry?.()
  assert.equal(snap?.active_sessions.length, 1)
  assert.equal(snap?.active_sessions[0]?.terminal_session_id, "term-oci-restart-id")
  assert.equal(Boolean(snap?.ended_session_ids?.includes("term-oci-restart-id")), false)
})

test("OCI terminal driver rejects startSession without adapter cleanup capability without launching backend", async () => {
  let startCalled = false
  const adapterWithoutCleanup: OciTerminalSessionAdapter = {
    startSession: async () => {
      startCalled = true
      return { outputDeltas: [] }
    },
    interactSession: async () => ({ outputDeltas: [] }),
  }
  const driver = makeOciExecutionDriver(adapterWithoutCleanup)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-no-cleanup",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-no-cleanup",
    placement_class: "local_oci",
    runtime_id: "remote_worker",
    capability_id: cap.capability_id,
  }
  assert.equal(
    driver.supportsTerminalSessions?.(cap, place.placement_class),
    false,
    "driver reports false for supportsTerminalSessions when adapter lacks cleanupSessions",
  )
  await assert.rejects(
    async () => {
      await driver.startTerminalSession?.({
        terminalSessionId: "term-no-clean-launch",
        command: ["bash"],
        capability: cap,
        placement: place,
      })
    },
    /OCI terminal adapter must support cleanupSessions before launching terminal sessions/,
  )
  assert.equal(startCalled, false, "backend startSession was never invoked")
})

test("OCI terminal session manager malformed-start contradictory cleanup retains ended session handle for subsequent cleanup", async () => {
  let cleanupCalls = 0
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [
        {
          schema_version: "bb.terminal_output_delta.v1",
          // Mismatched delta ID triggers validation error
          terminal_session_id: "wrong-delta-id",
          stream: "stdout",
          chunk_seq: 1,
          chunk_b64: "YWxpdmU=",
          occurred_at_utc: new Date().toISOString(),
        },
      ],
    }),
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => {
      cleanupCalls++
      if (cleanupCalls === 1) {
        // Contradictory cleanup result: appears in both cleaned and failed
        return {
          cleaned_session_ids: sessionIds ? [...sessionIds] : [],
          failed_session_ids: sessionIds ? [...sessionIds] : [],
        }
      }
      return {
        cleaned_session_ids: sessionIds ? [...sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }
  const driver = makeOciExecutionDriver(adapter)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-contra",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-contra",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  await assert.rejects(
    async () => {
      await driver.startTerminalSession?.({
        terminalSessionId: "term-oci-contra-target",
        command: ["bash"],
        capability: cap,
        placement: place,
      })
    },
    /Terminal output delta session ID/,
  )
  assert.equal(cleanupCalls, 1)

  // Verify ended handle was retained in snapshot
  let snap = await driver.snapshotTerminalRegistry?.()
  assert.deepEqual(snap?.ended_session_ids, ["term-oci-contra-target"])

  // Subsequent cleanup cleans the retained handle
  const cleanRes = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-contra-retry",
    scope: "all",
  })
  assert.deepEqual(cleanRes?.cleaned_session_ids, ["term-oci-contra-target"])
  assert.equal(cleanupCalls, 2)
})

test("OCI terminal session manager malformed-start string cleaned_session_ids does not coerce to array and retains ended handle", async () => {
  let cleanupCalls = 0
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [
        {
          schema_version: "bb.terminal_output_delta.v1",
          // Mismatched delta ID triggers validation error
          terminal_session_id: "wrong-delta-id",
          stream: "stdout",
          chunk_seq: 1,
          chunk_b64: "YWxpdmU=",
          occurred_at_utc: new Date().toISOString(),
        },
      ],
    }),
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => {
      cleanupCalls++
      if (cleanupCalls === 1) {
        // String field instead of array of strings
        return {
          cleaned_session_ids: "term-oci-str-target",
          failed_session_ids: [],
        } as unknown as string[]
      }
      return {
        cleaned_session_ids: sessionIds ? [...sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }
  const driver = makeOciExecutionDriver(adapter)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-str",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-str",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  await assert.rejects(
    async () => {
      await driver.startTerminalSession?.({
        terminalSessionId: "term-oci-str-target",
        command: ["bash"],
        capability: cap,
        placement: place,
      })
    },
    /Terminal output delta session ID/,
  )
  assert.equal(cleanupCalls, 1)

  // Verify ended handle was retained in snapshot (never coerced to single-char array or falsely marked cleaned)
  let snap = await driver.snapshotTerminalRegistry?.()
  assert.deepEqual(snap?.ended_session_ids, ["term-oci-str-target"])

  // Subsequent cleanup cleans the retained handle
  const cleanRes = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-str-retry",
    scope: "all",
  })
  assert.deepEqual(cleanRes?.cleaned_session_ids, ["term-oci-str-target"])
  assert.equal(cleanupCalls, 2)
})

test("OCI terminal session manager malformed-start hung cleanup bounds wait, retains ended handle, and later cleanup recovers", async () => {
  let cleanupCalls = 0
  const adapter: OciTerminalSessionAdapter = {
    startSession: async () => ({
      outputDeltas: [
        {
          schema_version: "bb.terminal_output_delta.v1",
          // Mismatched delta ID triggers validation error
          terminal_session_id: "wrong-delta-id",
          stream: "stdout",
          chunk_seq: 1,
          chunk_b64: "YWxpdmU=",
          occurred_at_utc: new Date().toISOString(),
        },
      ],
    }),
    interactSession: async () => ({ outputDeltas: [] }),
    cleanupSessions: async ({ sessionIds }) => {
      cleanupCalls++
      if (cleanupCalls === 1) {
        // Hung cleanup on call 1
        return new Promise(() => {})
      }
      return {
        cleaned_session_ids: sessionIds ? [...sessionIds] : [],
        failed_session_ids: [],
      }
    },
  }
  const manager = new OciTerminalSessionManager(adapter, 30)
  const cap: ExecutionCapabilityV1 = {
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-oci-hung",
    security_tier: "single_tenant",
    isolation_class: "oci",
    secret_mode: "ref_only",
    evidence_mode: "minimal",
  }
  const place: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place-oci-hung",
    placement_class: "local_oci",
    runtime_id: "oci",
    capability_id: cap.capability_id,
  }

  const start = Date.now()
  await assert.rejects(
    async () => {
      await manager.startSession({
        terminalSessionId: "term-oci-hung-target",
        command: ["bash"],
        capability: cap,
        placement: place,
      })
    },
    /Terminal output delta session ID/,
  )
  const elapsed = Date.now() - start
  assert.ok(elapsed < 1000, `malformed-start bounded wait took ${elapsed}ms, expected < 1000ms`)
  assert.equal(cleanupCalls, 1)

  // Verify ended handle was retained in snapshot
  let snap = await manager.snapshotRegistry()
  assert.deepEqual(snap?.ended_session_ids, ["term-oci-hung-target"])

  // Subsequent cleanup cleans the retained handle
  const cleanRes = await manager.cleanupSessions({
    cleanupId: "clean-hung-retry",
    scope: "all",
  })
  assert.deepEqual(cleanRes?.cleaned_session_ids, ["term-oci-hung-target"])
  assert.equal(cleanupCalls, 2)
})
