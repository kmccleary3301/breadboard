import test from "node:test"
import assert from "node:assert/strict"

import type { ExecutionCapabilityV1 } from "@breadboard/kernel-contracts"
import { buildExecutionDriverUnsupportedCase, createExecutionWorld } from "@breadboard/execution-drivers"
import {
  buildRemoteExecutionErrorSummary,
  buildRemoteExecutionRequestEnvelope,
  buildRemoteExecutionResponseEnvelope,
  buildRemoteSandboxRequest,
  chooseRemotePlacement,
  executeRemoteSandboxRequest,
  makeRemoteExecutionDriver,
  makeRemoteTerminalSessionDriver,
  type RemoteSandboxExecutor,
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

test("remote driver builds an explicit remote execution response envelope", () => {
  const envelope = buildRemoteExecutionResponseEnvelope({
    result: {
      schema_version: "bb.sandbox_result.v1",
      request_id: "req:remote:result",
      status: "completed",
      placement_id: "remote:result:1",
      stdout_ref: "artifact://remote/stdout/result",
      stderr_ref: "artifact://remote/stderr/result",
      artifact_refs: [],
      side_effect_digest: "sha256:remote-result",
      usage: { wall_ms: 11 },
      evidence_refs: [],
      error: null,
    },
    metadata: { route: "remote-primary" },
  })
  assert.equal(envelope.schema_version, "bb.remote_execution_response.v1")
  assert.equal(envelope.result.request_id, "req:remote:result")
  assert.equal(envelope.metadata?.route, "remote-primary")
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

test("remote driver surfaces error summaries from remote error payloads", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:error",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  await assert.rejects(
    () =>
      executeRemoteSandboxRequest(request, {
        endpointUrl: "https://example.test/remote-error",
        fetchImpl: async () =>
          ({
            ok: false,
            status: 503,
            json: async () => ({
              metadata: { summary: "remote backend unavailable" },
            }),
          }) as Response,
      }),
    /remote backend unavailable/,
  )
})

test("remote driver turns timeout aborts into a stable timeout error", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:timeout",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  await assert.rejects(
    () =>
      executeRemoteSandboxRequest(request, {
        endpointUrl: "https://example.test/remote-timeout",
        timeoutMs: 5,
        fetchImpl: async (_input, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
              reject(init.signal?.reason ?? new Error("aborted"))
            })
          }),
      }),
    /timed out after 5ms/,
  )
})

test("remote error-summary helper falls back cleanly", () => {
  assert.equal(buildRemoteExecutionErrorSummary({ message: "oops" }, 500), "oops")
  assert.equal(buildRemoteExecutionErrorSummary({}, 502), "Remote execution endpoint returned HTTP 502")
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

test("remote terminal driver can execute through a fetch-backed adapter", async () => {
  let seenInteract = false
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term",
    fetchImpl: async (_input, init) => {
      const body = JSON.parse(String(init?.body ?? "{}")) as { action: string; payload: Record<string, unknown> }
      if (body.action === "start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [],
            },
          }),
        } as Response
      }
      if (body.action === "interact") {
        seenInteract = true
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [
                {
                  schema_version: "bb.terminal_output_delta.v1",
                  terminal_session_id: "term-remote-1",
                  startup_call_id: null,
                  causing_call_id: "call-remote-poll-1",
                  stream: "stdout",
                  chunk_b64: Buffer.from("remote ok\n", "utf8").toString("base64"),
                  chunk_seq: 0,
                },
              ],
            },
          }),
        } as Response
      }
      if (body.action === "snapshot") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              snapshot: {
                schema_version: "bb.terminal_registry_snapshot.v1",
                snapshot_id: "remote-snap-1",
                active_sessions: [
                  {
                    schema_version: "bb.terminal_session_descriptor.v1",
                    terminal_session_id: "term-remote-1",
                    public_handles: [],
                    command: ["python", "worker.py"],
                    cwd: null,
                    startup_call_id: null,
                    owner_task_id: null,
                    stream_mode: "pipes",
                    stream_split: "stdout_stderr",
                    capability_id: remoteCapability.capability_id,
                    placement_id: "place-term-remote-1",
                    persistence_scope: "thread",
                    continuation_scope: "both"
                  }
                ],
                ended_session_ids: [],
              },
            },
          }),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {
            output_deltas: [],
            cleaned_session_ids: ["term-remote-1"],
            failed_session_ids: [],
          },
        }),
      } as Response
    },
  })
  await driver.startTerminalSession?.({
    terminalSessionId: "term-remote-1",
    command: ["python", "worker.py"],
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-remote-1",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
  })
  const interacted = await driver.interactTerminalSession?.({
    terminalSessionId: "term-remote-1",
    interactionKind: "poll",
    causingCallId: "call-remote-poll-1",
  })
  assert.equal(seenInteract, true)
  assert.equal(interacted?.outputDeltas.length, 1)
  const snapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(snapshot?.snapshot_id, "remote-snap-1")
  assert.equal(snapshot?.active_sessions.length, 1)
  const cleanup = await driver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-remote-1",
    scope: "single",
    sessionIds: ["term-remote-1"],
  })
  assert.deepEqual(cleanup?.cleaned_session_ids, ["term-remote-1"])
})

test("remote terminal driver surfaces endpoint failures cleanly", async () => {
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term-error",
    fetchImpl: async () =>
      ({
        ok: false,
        status: 503,
        json: async () => ({
          message: "terminal backend unavailable",
        }),
      }) as Response,
  })

  await assert.rejects(
    () =>
      driver.startTerminalSession?.({
        terminalSessionId: "term-remote-error-1",
        command: ["python", "worker.py"],
        capability: remoteCapability,
        placement: {
          schema_version: "bb.execution_placement.v1",
          placement_id: "place-term-remote-error-1",
          placement_class: "remote_worker",
          runtime_id: "remote",
          capability_id: remoteCapability.capability_id,
        },
      }) ?? Promise.resolve(undefined),
    /HTTP 503/,
  )
})

test("remote terminal driver preserves no-output poll and multi-session listing semantics", async () => {
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term-multi",
    fetchImpl: async (_input, init) => {
      const body = JSON.parse(String(init?.body ?? "{}")) as { action: string; payload: Record<string, unknown> }
      if (body.action === "start") {
        const descriptor = body.payload.descriptor as { terminal_session_id: string }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [],
              end:
                descriptor.terminal_session_id === "term-remote-fast-exit"
                  ? {
                      schema_version: "bb.terminal_session_end.v1",
                      terminal_session_id: descriptor.terminal_session_id,
                      startup_call_id: null,
                      causing_call_id: null,
                      terminal_state: "completed",
                      exit_code: 0,
                      duration_ms: 4,
                      artifact_refs: [],
                      evidence_refs: [],
                    }
                  : undefined,
            },
          }),
        } as Response
      }
      if (body.action === "interact") {
        const interaction = body.payload.interaction as { interaction_kind: string; causing_call_id?: string | null }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [],
              end:
                interaction.interaction_kind === "signal"
                  ? {
                      schema_version: "bb.terminal_session_end.v1",
                      terminal_session_id: "term-remote-keepalive",
                      startup_call_id: null,
                      causing_call_id: interaction.causing_call_id ?? null,
                      terminal_state: "cancelled",
                      exit_code: null,
                      duration_ms: 9,
                      artifact_refs: [],
                      evidence_refs: [],
                    }
                  : undefined,
            },
          }),
        } as Response
      }
      if (body.action === "snapshot") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              snapshot: {
                schema_version: "bb.terminal_registry_snapshot.v1",
                snapshot_id: "remote-snap-multi",
                active_sessions: [
                  {
                    schema_version: "bb.terminal_session_descriptor.v1",
                    terminal_session_id: "term-remote-keepalive",
                    public_handles: [],
                    command: ["python", "worker.py"],
                    cwd: null,
                    startup_call_id: null,
                    owner_task_id: null,
                    stream_mode: "pipes",
                    stream_split: "stdout_stderr",
                    capability_id: remoteCapability.capability_id,
                    placement_id: "place-term-remote-keepalive",
                    persistence_scope: "thread",
                    continuation_scope: "both"
                  }
                ],
                ended_session_ids: ["term-remote-fast-exit"],
              },
            },
          }),
        } as Response
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {
            output_deltas: [],
            cleaned_session_ids: body.payload.session_ids ?? [],
            failed_session_ids: [],
          },
        }),
      } as Response
    },
  })
  const placement = {
    schema_version: "bb.execution_placement.v1" as const,
    placement_id: "place-term-remote-multi",
    placement_class: "remote_worker" as const,
    runtime_id: "remote",
    capability_id: remoteCapability.capability_id,
  }
  await driver.startTerminalSession?.({
    terminalSessionId: "term-remote-keepalive",
    command: ["python", "worker.py"],
    capability: remoteCapability,
    placement,
  })
  const fastExit = await driver.startTerminalSession?.({
    terminalSessionId: "term-remote-fast-exit",
    command: ["python", "worker.py", "--once"],
    capability: remoteCapability,
    placement,
  })
  assert.equal(fastExit?.end?.terminal_state, "completed")
  const polled = await driver.interactTerminalSession?.({
    terminalSessionId: "term-remote-keepalive",
    interactionKind: "poll",
    causingCallId: "call-remote-poll-no-output",
  })
  assert.equal(polled?.outputDeltas.length, 0)
  assert.equal(polled?.end, undefined)
  const snapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(snapshot?.active_sessions.length, 1)
  assert.deepEqual(snapshot?.ended_session_ids, ["term-remote-fast-exit"])
  const cleanupExited = await driver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-remote-exited",
    scope: "single",
    sessionIds: ["term-remote-fast-exit"],
  })
  assert.deepEqual(cleanupExited?.cleaned_session_ids, ["term-remote-fast-exit"])
  await assert.rejects(
    () =>
      driver.interactTerminalSession?.({
        terminalSessionId: "term-remote-fast-exit",
        interactionKind: "stdin",
        inputText: "status\n",
        causingCallId: "call-remote-ended-stdin",
      }) ?? Promise.resolve(undefined),
    /already ended/,
  )
  const signaled = await driver.interactTerminalSession?.({
    terminalSessionId: "term-remote-keepalive",
    interactionKind: "signal",
    signal: "SIGTERM",
    causingCallId: "call-remote-signal-1",
  })
  assert.equal(signaled?.end?.terminal_state, "cancelled")
  const finalSnapshot = await driver.snapshotTerminalRegistry?.()
  assert.ok((finalSnapshot?.ended_session_ids ?? []).includes("term-remote-fast-exit"))
  assert.equal(finalSnapshot?.active_sessions[0]?.terminal_session_id, "term-remote-keepalive")
})

test("remote terminal driver turns timeout aborts into a stable timeout error", async () => {
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term-timeout",
    timeoutMs: 5,
    fetchImpl: async (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(init.signal?.reason ?? new Error("aborted"))
        })
      }),
  })

  await assert.rejects(
    () =>
      driver.startTerminalSession?.({
        terminalSessionId: "term-remote-timeout",
        command: ["python", "worker.py"],
        capability: remoteCapability,
        placement: {
          schema_version: "bb.execution_placement.v1",
          placement_id: "place-term-remote-timeout",
          placement_class: "remote_worker",
          runtime_id: "remote",
          capability_id: remoteCapability.capability_id,
        },
      }) ?? Promise.resolve(undefined),
    /timed out after 5ms/,
  )
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

test("remote driver injected executor receives world context signal and aborts on cancellation", async () => {
  const abortController = new AbortController()
  let receivedSignal: AbortSignal | undefined
  const driver = makeRemoteExecutionDriver(async (request, context) => {
    receivedSignal = context?.signal
    return new Promise((_resolve, reject) => {
      context?.signal?.addEventListener("abort", () => {
        reject(new Error("injected executor cancelled"))
      })
    })
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:injected:cancel",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const executePromise = driver.execute!(request, {
    signal: abortController.signal,
    deadlineAtMs: null,
    terminationGraceMs: 100,
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:cancel",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    driverId: "remote",
  })
  assert.equal(receivedSignal?.aborted, false)
  abortController.abort(new Error("world cancelled"))
  await assert.rejects(() => executePromise, /injected executor cancelled/)
  assert.equal(receivedSignal?.aborted, true)
})

test("remote driver injected executor aborts on driver.terminate", async () => {
  let receivedSignal: AbortSignal | undefined
  const driver = makeRemoteExecutionDriver(async (request, context) => {
    receivedSignal = context?.signal
    return new Promise((_resolve, reject) => {
      context?.signal?.addEventListener("abort", () => {
        reject(new Error("injected executor terminated"))
      })
    })
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:injected:term",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const executePromise = driver.execute!(request)
  assert.ok(receivedSignal)
  assert.equal(receivedSignal?.aborted, false)
  driver.terminate!(request, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: null,
  })
  await assert.rejects(() => executePromise, /injected executor terminated/)
  assert.equal(receivedSignal?.aborted, true)
})

test("remote driver direct execute with httpOptions.timeoutMs enforces timeout without hanging", async () => {
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-direct-timeout",
    timeoutMs: 10,
    fetchImpl: async (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(init.signal?.reason ?? new Error("aborted"))
        })
      }),
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:direct:timeout",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  await assert.rejects(
    () => driver.execute!(request),
    /timed out after 10ms/,
  )
})

test("remote driver combines caller signal, world signal, and timeout without replacing each other", async () => {
  const callerController = new AbortController()
  const driverWithCaller = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-caller-signal",
    signal: callerController.signal,
    fetchImpl: async (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new Error("fetch aborted by caller"))
        })
      }),
  })
  const req1 = buildRemoteSandboxRequest({
    requestId: "req:remote:caller:1",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const call1 = driverWithCaller.execute!(req1)
  callerController.abort(new Error("caller aborted"))
  await assert.rejects(() => call1, /caller aborted/)
  const worldController = new AbortController()
  const driverForWorld = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-world-signal",
    timeoutMs: 5000,
    fetchImpl: async (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(init.signal?.reason ?? new Error("fetch aborted by world"))
        })
      }),
  })
  const req2 = buildRemoteSandboxRequest({
    requestId: "req:remote:world:1",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  const call2 = driverForWorld.execute!(req2, {
    signal: worldController.signal,
    deadlineAtMs: Date.now() + 5000,
    terminationGraceMs: 100,
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:world",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    driverId: "remote",
  })
  worldController.abort(new Error("world aborted"))
  await assert.rejects(() => call2, /world aborted/)

  const driverForTimeout = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-timeout-signal",
    timeoutMs: 15,
    fetchImpl: async (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(init.signal?.reason ?? new Error("aborted"))
        })
      }),
  })
  const req3 = buildRemoteSandboxRequest({
    requestId: "req:remote:timeout:1",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  await assert.rejects(
    () => driverForTimeout.execute!(req3),
    /timed out after 15ms/,
  )
})

test("makeRemoteExecutionDriver with httpOptions delegates terminal session methods", async () => {
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-terminal-exec",
    fetchImpl: async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {},
        }),
      }) as Response,
  })
  assert.ok(driver.supportsTerminalSessions)
  assert.ok(driver.startTerminalSession)
  assert.ok(driver.interactTerminalSession)
  assert.ok(driver.snapshotTerminalRegistry)
  assert.ok(driver.cleanupTerminalSessions)
  assert.equal(driver.supportsTerminalSessions(remoteCapability, "remote_worker"), true)

  const startResult = await driver.startTerminalSession({
    terminalSessionId: "term-remote-delegated-1",
    command: ["python", "-m", "worker"],
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:1",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
  })
  assert.equal(startResult.descriptor.terminal_session_id, "term-remote-delegated-1")
  assert.equal(startResult.descriptor.persistence_scope, "thread")
  const cleanupResult = await driver.cleanupTerminalSessions({
    cleanupId: "clean-remote-1",
    scope: "single",
    sessionIds: ["term-remote-delegated-1"],
  })
  assert.deepEqual(cleanupResult.cleaned_session_ids, ["term-remote-delegated-1"])
})
test("remote execution driver with stubborn executor marks terminationObserved false when terminate exceeds grace", async () => {
  const stubbornExecutor: RemoteSandboxExecutor = async () => {
    // Intentionally ignores abort signal and hangs
    return new Promise(() => {})
  }
  const driver = makeRemoteExecutionDriver(stubbornExecutor)
  const world = createExecutionWorld({ drivers: [driver] })
  const abortController = new AbortController()
  const execPromise = world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-stubborn-remote",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req-stubborn-remote-1",
    command: ["python", "-m", "worker"],
    signal: abortController.signal,
    terminationGraceMs: 20,
  })
  await new Promise((r) => setTimeout(r, 10))
  abortController.abort(new Error("external abort"))
  const result = await execPromise
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult?.status, "cancelled")
  assert.equal(result.livenessEvidence.state, "cancelled")
  assert.equal(result.livenessEvidence.terminationRequested, true)
  assert.equal(result.livenessEvidence.terminationObserved, false)
})

test("remote terminal driver rejects malformed cleanup response arrays", async () => {
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term-malformed-cleanup",
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        schema_version: "bb.remote_terminal_response.v1",
        payload: {
          // Invalid: string instead of array of strings
          cleaned_session_ids: "not-an-array",
        },
      }),
    }) as Response,
  })
  await assert.rejects(
    () =>
      driver.cleanupTerminalSessions!({
        cleanupId: "clean-malformed-1",
        scope: "filtered",
        sessionIds: ["term-1"],
      }),
    /malformed cleaned_session_ids/,
  )
})

test("remote execution driver under shared world deadline returns timed_out status and liveness without competing timer failure", async () => {
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-deadline",
    fetchImpl: async (_input, init) => {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new Error("aborted"))
        })
      })
    },
  })
  const world = createExecutionWorld({ drivers: [driver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:deadline-1",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req-remote-deadline-1",
    command: ["python", "-m", "worker"],
    deadlineMs: 50,
    terminationGraceMs: 20,
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.livenessEvidence.terminationRequested, true)
})

test("remote execution driver with configured transport timeout returns timed_out status and liveness", async () => {
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "https://example.test/remote-transport-timeout",
    timeoutMs: 40,
    fetchImpl: async (_input, init) => {
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new Error("timed out"))
        })
      })
    },
  })
  const world = createExecutionWorld({ drivers: [driver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:transport-1",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req-remote-transport-1",
    command: ["python", "-m", "worker"],
    terminationGraceMs: 20,
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.livenessEvidence.terminationRequested, true)
})

test("unconfigured remote driver returns false for supportsCapability and yields unsupportedCase under execution world", async () => {
  const unconfiguredDriver = makeRemoteExecutionDriver()
  assert.equal(unconfiguredDriver.supportsCapability(remoteCapability, "remote_worker"), false)

  const world = createExecutionWorld({ drivers: [unconfiguredDriver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:remote:unconfigured-1",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req-remote-unconfigured-1",
    command: ["python", "-m", "worker"],
  })
  assert.equal(result.kind, "sandbox")
  if (result.kind !== "sandbox") throw new Error("expected sandbox result")
  assert.equal(result.sandboxResult, null)
  assert.ok(result.unsupportedCase != null)
  assert.equal(result.unsupportedCase?.unavailable_placement, "remote_worker")
})

test("Remote terminal session manager preserves unrequested session after malformed cleanup returns extra IDs", async () => {
  const customFetch: typeof fetch = async (_input, init) => {
    const body = JSON.parse(init?.body as string)
    if (body.action === "start") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: { output_deltas: [] },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    if (body.action === "cleanup") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {
            cleaned_session_ids: ["term-remote-target", "term-remote-unrequested-victim"],
            failed_session_ids: [],
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    if (body.action === "interact") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: { output_deltas: [] },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ error: "not found" }), { status: 404 })
  }

  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-terminal",
    fetchImpl: customFetch,
  })

  // 1. Start target and victim sessions
  await driver.startTerminalSession!({
    terminalSessionId: "term-remote-target",
    command: ["python", "-i"],
    capability: remoteCapability,
  })
  await driver.startTerminalSession!({
    terminalSessionId: "term-remote-unrequested-victim",
    command: ["python", "-i"],
    capability: remoteCapability,
  })

  // 2. Cleanup only target session
  const cleanupRes = await driver.cleanupTerminalSessions!({
    cleanupId: "clean-remote-single-1",
    scope: "single",
    sessionIds: ["term-remote-target"],
  })
  assert.deepEqual(cleanupRes.cleaned_session_ids, ["term-remote-target"])

  // 3. Verify unrequested victim session remains active in manager
  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-remote-unrequested-victim",
    interactionKind: "stdin",
    inputText: "1 + 1\n",
  })
  assert.ok(interactRes.interaction != null, "victim session remains active and interactive")
})

test("Remote terminal session manager rejects overlapping cleaned and failed IDs, preserves session active for subsequent interact", async () => {
  const customFetch: typeof fetch = async (_input, init) => {
    const body = JSON.parse(init?.body as string)
    if (body.action === "start") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: { output_deltas: [] },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    if (body.action === "cleanup") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {
            cleaned_session_ids: ["term-remote-overlap-target"],
            failed_session_ids: ["term-remote-overlap-target"],
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    if (body.action === "interact") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: { output_deltas: [] },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ error: "not found" }), { status: 404 })
  }

  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-terminal",
    fetchImpl: customFetch,
  })

  await driver.startTerminalSession!({
    terminalSessionId: "term-remote-overlap-target",
    command: ["python", "-i"],
    capability: remoteCapability,
  })

  await assert.rejects(
    () =>
      driver.cleanupTerminalSessions!({
        cleanupId: "clean-remote-overlap-1",
        scope: "single",
        sessionIds: ["term-remote-overlap-target"],
      }),
    /Invalid cleanup result: session ID 'term-remote-overlap-target' appears in both cleaned_session_ids and failed_session_ids/,
  )

  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-remote-overlap-target",
    interactionKind: "stdin",
    inputText: "print('alive')\n",
  })
  assert.ok(interactRes.interaction != null, "session remains active and interactive after rejected cleanup")
})

test("Remote terminal session manager rejects wrong-ID end during interact without ending active session, preserving interactability", async () => {
  let wrongEndTriggered = true
  const customFetch: typeof fetch = async (_input, init) => {
    const req = JSON.parse(init?.body as string)
    if (req.action === "start") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          action: "start",
          payload: { descriptor: req.payload.descriptor, output_deltas: [] },
          metadata: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (req.action === "interact") {
      if (wrongEndTriggered) {
        wrongEndTriggered = false
        return new Response(
          JSON.stringify({
            schema_version: "bb.remote_terminal_response.v1",
            action: "interact",
            payload: {
              output_deltas: [],
              end: {
                schema_version: "bb.terminal_session_end.v1",
                terminal_session_id: "wrong-foreign-remote-session-id",
                terminal_state: "completed",
                exit_code: 0,
                signal: null,
                clean_exit: true,
                reason: "completed",
                occurred_at_utc: new Date().toISOString(),
              },
            },
            metadata: {},
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          action: "interact",
          payload: { output_deltas: [] },
          metadata: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ error: "not handled" }), { status: 404 })
  }

  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "http://remote-backend:8080/terminals",
    fetchImpl: customFetch,
  })

  await driver.startTerminalSession!({
    terminalSessionId: "term-remote-wrong-end-target",
    command: ["python", "-i"],
    capability: remoteCapability,
  })

  // Wrong end ID must be rejected
  await assert.rejects(
    () =>
      driver.interactTerminalSession!({
        terminalSessionId: "term-remote-wrong-end-target",
        interactionKind: "stdin",
        inputText: "trigger wrong end\n",
      }),
    /Terminal session end session ID 'wrong-foreign-remote-session-id' does not match interaction session ID 'term-remote-wrong-end-target'/,
  )

  // Session was NOT deleted or marked ended in manager, so retry/subsequent interaction succeeds!
  const interactRes = await driver.interactTerminalSession!({
    terminalSessionId: "term-remote-wrong-end-target",
    interactionKind: "stdin",
    inputText: "print('recovered')\n",
  })
  assert.ok(interactRes.interaction != null, "active remote session remains interactable after rejected wrong-ID end")
})

test("Remote cleanup response omitting cleaned_session_ids but reporting failed_session_ids derives cleaned = targets - failed, preserving successful targets", async () => {
  const customFetch: typeof fetch = async (_input, init) => {
    const req = JSON.parse(init?.body as string)
    if (req.action === "start") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          action: "start",
          payload: { descriptor: req.payload.descriptor, output_deltas: [] },
          metadata: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (req.action === "cleanup") {
      // Omit cleaned_session_ids, provide only failed_session_ids: ["term-rem-fail-1"]
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          action: "cleanup",
          payload: { failed_session_ids: ["term-rem-fail-1"] },
          metadata: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (req.action === "interact") {
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          action: "interact",
          payload: { output_deltas: [] },
          metadata: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ error: "not handled" }), { status: 404 })
  }

  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "http://remote-backend:8080/terminals",
    fetchImpl: customFetch,
  })

  await driver.startTerminalSession!({
    terminalSessionId: "term-rem-success-1",
    command: ["python", "-i"],
    capability: remoteCapability,
  })
  await driver.startTerminalSession!({
    terminalSessionId: "term-rem-fail-1",
    command: ["python", "-i"],
    capability: remoteCapability,
  })

  const cleanupRes = await driver.cleanupTerminalSessions!({
    cleanupId: "clean-rem-failed-only-1",
    scope: "filtered",
    sessionIds: ["term-rem-success-1", "term-rem-fail-1"],
  })

  assert.ok(cleanupRes)
  assert.deepEqual(cleanupRes.cleaned_session_ids, ["term-rem-success-1"])
  assert.deepEqual(cleanupRes.failed_session_ids, ["term-rem-fail-1"])

  // term-rem-fail-1 remains active for interact, while term-rem-success-1 was cleaned
  const interactFail = await driver.interactTerminalSession!({
    terminalSessionId: "term-rem-fail-1",
    interactionKind: "stdin",
    inputText: "print('still active')\n",
  })
  assert.ok(interactFail.interaction != null, "failed remote session remains active")

  await assert.rejects(
    () =>
      driver.interactTerminalSession!({
        terminalSessionId: "term-rem-success-1",
        interactionKind: "stdin",
        inputText: "print('cleaned')\n",
      }),
    /already ended/,
  )
})

test("remote execution driver direct execute enforces bounded deadline when fetch transport ignores abort", async () => {
  const abortIgnoringFetch: typeof fetch = async () => new Promise<Response>(() => {})
  const driver = makeRemoteExecutionDriver(undefined, {
    endpointUrl: "http://remote-backend:8080/sandbox",
    fetchImpl: abortIgnoringFetch,
    timeoutMs: 5000,
  })

  const request = buildRemoteSandboxRequest({
    requestId: "req-remote-deadline-direct",
    capability: remoteCapability,
    command: ["python", "-c", "print(1)"],
    placementClass: "remote_worker",
  })

  const startMs = Date.now()
  await assert.rejects(
    () =>
      driver.execute!(request, {
        signal: new AbortController().signal,
        deadlineAtMs: Date.now() + 100,
        terminationGraceMs: 50,
        capability: remoteCapability,
        placement: {
          schema_version: "bb.execution_placement.v1",
          placement_id: "place-remote-direct",
          placement_class: "remote_worker",
          runtime_id: "remote",
          capability_id: remoteCapability.capability_id,
        },
        driverId: "remote",
      }),
    /Remote execution timed out/,
  )
  const durationMs = Date.now() - startMs
  assert.ok(durationMs < 2000, `execute rejected boundedly in ${durationMs}ms despite abort-ignoring transport`)
})

test("remote terminal manager retains and cleans >32 ended sessions without cap failure", async () => {
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "http://remote-backend:8080/terminals",
    fetchImpl: async (_input, init) => {
      const body = JSON.parse(init?.body as string)
      if (body.action === "start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              descriptor: {
                schema_version: "bb.terminal_session_descriptor.v1",
                terminal_session_id: body.payload.terminal_session_id,
                public_handles: [],
                command: ["python", "worker.py"],
                cwd: null,
                startup_call_id: null,
                owner_task_id: null,
                stream_mode: "pipes",
                stream_split: "stdout_stderr",
                capability_id: remoteCapability.capability_id,
                placement_id: "place-term-rem-40",
                persistence_scope: "thread",
                continuation_scope: "both",
              },
            },
          }),
        } as Response
      }
      if (body.action === "interact") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [],
              end: {
                schema_version: "bb.terminal_session_end.v1",
                terminal_session_id: body.payload.descriptor?.terminal_session_id ?? body.payload.interaction?.terminal_session_id ?? "unknown",
                startup_call_id: null,
                causing_call_id: null,
                terminal_state: "completed",
                exit_code: 0,
                duration_ms: 10,
                artifact_refs: [],
                evidence_refs: [],
              },
            },
          }),
        } as Response
      }
      if (body.action === "cleanup") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              cleaned_session_ids: body.payload.session_ids ? [...body.payload.session_ids] : [],
              failed_session_ids: [],
            },
          }),
        } as Response
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response
    },
  })

  // Start and end 40 sessions
  for (let i = 1; i <= 40; i++) {
    await driver.startTerminalSession?.({
      terminalSessionId: `term-rem-40-${i}`,
      command: ["python", "worker.py"],
      capability: remoteCapability,
      placement: {
        schema_version: "bb.execution_placement.v1",
        placement_id: "place-term-rem-40",
        placement_class: "remote_worker",
        runtime_id: "remote",
        capability_id: remoteCapability.capability_id,
      },
    })
    await driver.interactTerminalSession?.({
      terminalSessionId: `term-rem-40-${i}`,
      interactionKind: "poll",
    })
  }

  // Cleanup scope all must clean all 40 ended sessions
  const cleanupResult = await driver.cleanupTerminalSessions?.({
    cleanupId: "clean-rem-40",
    scope: "all",
  })

  assert.ok(cleanupResult)
  assert.equal(cleanupResult.cleaned_session_ids.length, 40)
  assert.deepEqual(cleanupResult.failed_session_ids, [])
  for (let i = 1; i <= 40; i++) {
    assert.ok(cleanupResult.cleaned_session_ids.includes(`term-rem-40-${i}`))
  }
})

test("remote terminal manager same-ID successful restart removes ID from ended_session_ids in registry snapshot", async () => {
  let ended = false
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "http://remote-backend:8080/terminals",
    fetchImpl: async (_input, init) => {
      const body = JSON.parse(init?.body as string)
      if (body.action === "start") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              descriptor: {
                schema_version: "bb.terminal_session_descriptor.v1",
                terminal_session_id: body.payload.terminal_session_id,
                public_handles: [],
                command: ["python", "worker.py"],
                cwd: null,
                startup_call_id: null,
                owner_task_id: null,
                stream_mode: "pipes",
                stream_split: "stdout_stderr",
                capability_id: remoteCapability.capability_id,
                placement_id: "place-term-rem-restart",
                persistence_scope: "thread",
                continuation_scope: "both",
              },
            },
          }),
        } as Response
      }
      if (body.action === "interact") {
        ended = true
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              output_deltas: [],
              end: {
                schema_version: "bb.terminal_session_end.v1",
                terminal_session_id: body.payload.descriptor?.terminal_session_id ?? "term-rem-restart-id",
                startup_call_id: null,
                causing_call_id: null,
                terminal_state: "completed",
                exit_code: 0,
                duration_ms: 10,
                artifact_refs: [],
                evidence_refs: [],
              },
            },
          }),
        } as Response
      }
      if (body.action === "snapshot") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: {
              snapshot: {
                schema_version: "bb.terminal_registry_snapshot.v1",
                snapshot_id: "snap-remote-1",
                active_sessions: ended
                  ? []
                  : [
                      {
                        schema_version: "bb.terminal_session_descriptor.v1",
                        terminal_session_id: "term-rem-restart-id",
                        public_handles: [],
                        command: ["python", "worker.py"],
                        cwd: null,
                        startup_call_id: null,
                        owner_task_id: null,
                        stream_mode: "pipes",
                        stream_split: "stdout_stderr",
                        capability_id: remoteCapability.capability_id,
                        placement_id: "place-term-rem-restart",
                        persistence_scope: "thread",
                        continuation_scope: "both",
                      },
                    ],
                ended_session_ids: ended ? ["term-rem-restart-id"] : [],
              },
            },
          }),
        } as Response
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response
    },
  })

  // 1. Start and end session
  await driver.startTerminalSession?.({
    terminalSessionId: "term-rem-restart-id",
    command: ["python", "worker.py"],
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-rem-restart",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
  })
  await driver.interactTerminalSession?.({
    terminalSessionId: "term-rem-restart-id",
    interactionKind: "poll",
  })

  let snap = await driver.snapshotTerminalRegistry?.()
  assert.equal(snap?.active_sessions.length, 0)
  assert.deepEqual(snap?.ended_session_ids, ["term-rem-restart-id"])

  // 2. Restart with same ID (active)
  ended = false
  const restartRes = await driver.startTerminalSession?.({
    terminalSessionId: "term-rem-restart-id",
    command: ["python", "worker.py"],
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-rem-restart",
      placement_class: "remote_worker",
      runtime_id: "remote",
      capability_id: remoteCapability.capability_id,
    },
  })
  assert.ok(restartRes?.descriptor)

  // 3. Registry must show exactly 1 active and 0 ended for that ID
  snap = await driver.snapshotTerminalRegistry?.()
  assert.equal(snap?.active_sessions.length, 1)
  assert.equal(snap?.active_sessions[0]?.terminal_session_id, "term-rem-restart-id")
  assert.equal(Boolean(snap?.ended_session_ids?.includes("term-rem-restart-id")), false)
})
