import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import test from "node:test"
import assert from "node:assert/strict"

import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
} from "@breadboard/kernel-contracts"
import {
  buildCanonicalSandboxEvidence,
  canonicalSandboxArtifactUri,
  buildExecutionDriverUnsupportedCase,
  createExecutionWorld,
} from "@breadboard/execution-drivers"
import {
  buildRemoteExecutionErrorSummary,
  buildRemoteExecutionRequestEnvelope,
  buildRemoteExecutionResponseEnvelope,
  buildRemoteSandboxRequest,
  chooseRemotePlacement,
  executeRemoteSandboxRequest,
  makeRayExecutionDriver,
  makeRemoteExecutionDriver,
  makeRemoteTerminalSessionDriver,
  makeSlurmExecutionDriver,
  makeSshSlurmBackend,
  type RemoteSandboxExecutor,
  type ScheduledExecutionBackendV1,
} from "../src/index.js"

function slurmTestJobName(
  sshTarget: string,
  evidenceDirectory: string,
  requestId: string,
): string {
  return `bb-${createHash("sha256")
    .update(`${sshTarget}\0${evidenceDirectory}\0${requestId}`)
    .digest("hex")
    .slice(0, 32)}`
}

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

const slurmCapability: ExecutionCapabilityV1 = {
  ...remoteCapability,
  allow_net_hosts: undefined,
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

test("remote driver rejects programs outside the capability allowlist", () => {
  assert.throws(
    () =>
      buildRemoteSandboxRequest({
        requestId: "req:remote:disallowed",
        capability: remoteCapability,
        command: ["node", "worker.js"],
      }),
    /program "node" is not allowed by capability cap:remote:1/,
  )
})

test("remote terminal driver rejects disallowed programs before transport", async () => {
  let transportCalls = 0
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://worker.example/terminals",
    async fetchImpl() {
      transportCalls += 1
      throw new Error("transport must not run")
    },
  })
  await assert.rejects(
    () =>
      driver.startTerminalSession!({
        terminalSessionId: "terminal-disallowed",
        command: ["node", "worker.js"],
        capability: remoteCapability,
      }),
    /program "node" is not allowed by capability cap:remote:1/,
  )
  assert.equal(transportCalls, 0)
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

test("remote driver keeps the timeout active while parsing the response body", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:body-timeout",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })
  await assert.rejects(
    () =>
      executeRemoteSandboxRequest(request, {
        endpointUrl: "https://example.test/remote-body-timeout",
        timeoutMs: 5,
        fetchImpl: async () =>
          ({
            ok: true,
            status: 200,
            json: async () => new Promise<never>(() => {}),
          }) as unknown as Response,
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

test("remote terminal driver adopts active sessions returned by a backend snapshot", async () => {
  const descriptor = {
    schema_version: "bb.terminal_session_descriptor.v1" as const,
    terminal_session_id: "term-remote-recovered-1",
    public_handles: [],
    command: ["python", "worker.py"],
    cwd: null,
    startup_call_id: null,
    owner_task_id: null,
    stream_mode: "pipes" as const,
    stream_split: "stdout_stderr" as const,
    capability_id: remoteCapability.capability_id,
    placement_id: "place-term-remote-recovered-1",
    persistence_scope: "thread" as const,
    continuation_scope: "both" as const,
  }
  let interactSeen = false
  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-term-recovered",
    fetchImpl: async (_input, init) => {
      const body = JSON.parse(String(init?.body ?? "{}")) as {
        action: string
        payload: Record<string, unknown>
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
                snapshot_id: "remote-snap-recovered-1",
                active_sessions: [descriptor],
                ended_session_ids: [],
              },
            },
          }),
        } as Response
      }
      if (body.action === "interact") {
        interactSeen = true
        assert.deepEqual(body.payload.descriptor, descriptor)
        return {
          ok: true,
          status: 200,
          json: async () => ({
            schema_version: "bb.remote_terminal_response.v1",
            payload: { output_deltas: [] },
          }),
        } as Response
      }
      throw new Error(`unexpected remote terminal action: ${body.action}`)
    },
  })

  const snapshot = await driver.snapshotTerminalRegistry?.()
  assert.equal(snapshot?.active_sessions[0]?.terminal_session_id, descriptor.terminal_session_id)
  const interaction = await driver.interactTerminalSession?.({
    terminalSessionId: descriptor.terminal_session_id,
    interactionKind: "poll",
  })
  assert.ok(interaction)
  assert.equal(interactSeen, true)
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

test("remote driver retains rejected execution until termination cleanup", async () => {
  let receivedSignal: AbortSignal | undefined
  let launches = 0
  const driver = makeRemoteExecutionDriver(async (_request, context) => {
    launches += 1
    receivedSignal = context?.signal
    throw new Error("remote executor rejected after launch")
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:remote:rejected-after-launch",
    capability: remoteCapability,
    command: ["python", "worker.py"],
  })

  await assert.rejects(() => driver.execute!(request), /rejected after launch/)
  await assert.rejects(() => driver.execute!(request), /already active/)
  assert.equal(launches, 1)
  assert.equal(receivedSignal?.aborted, false)
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: null,
  })
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

test("Remote terminal scope-all filters foreign cleanup confirmations and does not poison unknown IDs", async () => {
  const targetId = "term-remote-scope-all-target"
  const foreignId = "term-remote-scope-all-foreign"
  const customFetch: typeof fetch = async (_input, init) => {
    const request = JSON.parse(init?.body as string)
    if (request.action === "cleanup") {
      assert.deepEqual(request.payload.session_ids, [targetId])
      return new Response(
        JSON.stringify({
          schema_version: "bb.remote_terminal_response.v1",
          payload: {
            cleaned_session_ids: [targetId, foreignId],
            failed_session_ids: [foreignId],
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }
    return new Response(
      JSON.stringify({
        schema_version: "bb.remote_terminal_response.v1",
        payload: { output_deltas: [] },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    )
  }

  const driver = makeRemoteTerminalSessionDriver({
    endpointUrl: "https://example.test/remote-terminal-scope-all",
    fetchImpl: customFetch,
  })
  await driver.startTerminalSession!({
    terminalSessionId: targetId,
    command: ["python", "-i"],
    capability: remoteCapability,
  })

  const cleanup = await driver.cleanupTerminalSessions!({
    cleanupId: "clean-remote-scope-all-foreign",
    scope: "all",
  })
  assert.deepEqual(cleanup.cleaned_session_ids, [targetId])
  assert.deepEqual(cleanup.failed_session_ids, [])

  await assert.rejects(
    () =>
      driver.interactTerminalSession!({
        terminalSessionId: foreignId,
        interactionKind: "poll",
      }),
    /Unknown remote terminal session/,
  )
})


test("Ray scheduled adapter polls to one provider-neutral result", async () => {
  const states = ["accepted", "running", "completed"] as const
  let observation = 0
  const recordedEvidence: object[] = []
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-local",
    async submit() {
      return {
        executionId: "ray-task-1",
        evidenceRefs: ["evidence://ray/submit"],
      }
    },
    async observe() {
      const state = states[Math.min(observation++, states.length - 1)]
      return state === "completed"
        ? {
            state,
            result: {
              schema_version: "bb.sandbox_result.v1",
              request_id: "req:ray:1",
              status: "completed",
              stdout_ref: `sha256:${"0".repeat(64)}`,
              stderr_ref: `sha256:${"1".repeat(64)}`,
              artifact_refs: [
                `sha256:${"0".repeat(64)}`,
                `sha256:${"1".repeat(64)}`,
              ],
              side_effect_digest: `sha256:${"2".repeat(64)}`,
              usage: { exit_code: 0 },
              evidence_refs: [`sha256:${"3".repeat(64)}`],
            },
            evidenceRefs: ["evidence://ray/terminal"],
          }
        : { state }
    },
    async cancel() {
      assert.fail("completed Ray execution must not be cancelled")
    },
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence(evidence) {
        recordedEvidence.push(evidence)
      },
    })],
  })
  const capability = {
    ...remoteCapability,
    capability_id: "cap:ray:1",
  }
  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:1",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: capability.capability_id,
      metadata: {},
    },
    requestId: "req:ray:1",
    command: ["python", "-c", "print('world')"],
    driverId: "ray",
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.driverId, "ray")
  assert.equal(result.sandboxResult?.status, "completed")
  assert.deepEqual(result.sandboxResult?.evidence_refs, [`sha256:${"3".repeat(64)}`])
  assert.deepEqual(recordedEvidence.at(-1), {
    driverId: "ray",
    backendId: "ray-local",
    executionId: "ray-task-1",
    state: "completed",
    evidenceRefs: [
      "evidence://ray/submit",
      "evidence://ray/terminal",
    ],
  })
})

test("scheduled adapter retries failed evidence before later cleanup evidence", async () => {
  let cancelled = false
  let evidenceAttempts = 0
  const recordedStates: string[] = []
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-evidence-retry",
    async submit() {
      return {
        executionId: "ray-evidence-retry-1",
        evidenceRefs: ["ray://submission/retry"],
      }
    },
    async observe() {
      return {
        state: cancelled ? "cancelled" : "running",
        evidenceRefs: [cancelled ? "ray://cancelled/retry" : "ray://running/retry"],
      }
    },
    async cancel() {
      cancelled = true
    },
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence(evidence) {
        evidenceAttempts++
        if (evidenceAttempts === 1) throw new Error("transient evidence sink failure")
        recordedStates.push(evidence.state)
      },
    })],
  })

  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:evidence-retry",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req:ray:evidence-retry",
    command: ["python", "-c", "print('retry')"],
    driverId: "ray",
    terminationGraceMs: 50,
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(cancelled, true)
  assert.deepEqual(recordedStates, ["accepted", "cancelled"])
})

test("scheduled adapter retries failed terminal evidence before suppressing later states", async () => {
  let cancelled = false
  let completedAttempts = 0
  const recordedStates: string[] = []
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-terminal-evidence-retry",
    async submit() {
      return {
        executionId: "ray-terminal-evidence-retry-1",
        evidenceRefs: ["ray://submission/terminal-retry"],
      }
    },
    async observe() {
      return {
        state: cancelled ? "cancelled" : "completed",
        evidenceRefs: [
          cancelled
            ? "ray://cancelled/terminal-retry"
            : "ray://completed/terminal-retry",
        ],
        result: cancelled
          ? undefined
          : {
              schema_version: "bb.sandbox_result.v1",
              request_id: "req:ray:terminal-evidence-retry",
              status: "completed",
              stdout_ref: `sha256:${"0".repeat(64)}`,
              stderr_ref: `sha256:${"1".repeat(64)}`,
              artifact_refs: [
                `sha256:${"0".repeat(64)}`,
                `sha256:${"1".repeat(64)}`,
              ],
              side_effect_digest: `sha256:${"2".repeat(64)}`,
              usage: { exit_code: 0 },
              evidence_refs: [`sha256:${"3".repeat(64)}`],
            },
      }
    },
    async cancel() {
      cancelled = true
    },
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence(evidence) {
        if (evidence.state === "completed") {
          completedAttempts += 1
          if (completedAttempts === 1) {
            throw new Error("transient terminal evidence failure")
          }
        }
        recordedStates.push(evidence.state)
      },
    })],
  })

  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:terminal-evidence-retry",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req:ray:terminal-evidence-retry",
    command: ["python", "-c", "print('retry')"],
    driverId: "ray",
    terminationGraceMs: 50,
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(cancelled, true)
  assert.equal(completedAttempts, 2)
  assert.deepEqual(recordedStates, ["accepted", "completed"])
})


test("scheduled evidence never regresses after a concurrent terminal observation", async () => {
  let observationCount = 0
  let releaseConflictingTerminal!: () => void
  let observationStarted!: () => void
  const firstObservationStarted = new Promise<void>((resolve) => {
    observationStarted = resolve
  })
  const states: string[] = []
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-terminal-monotonic",
    async submit() {
      return { executionId: "ray-terminal-monotonic-1" }
    },
    async observe() {
      observationCount++
      if (observationCount === 1) {
        observationStarted()
        return new Promise((resolve) => {
          releaseConflictingTerminal = () => resolve({ state: "completed" })
        })
      }
      return { state: "cancelled" }
    },
    async cancel() {},
  }
  const driver = makeRayExecutionDriver(backend, {
    pollIntervalMs: 1,
    cancellationObservationTimeoutMs: 50,
    recordEvidence(evidence) {
      states.push(evidence.state)
    },
  })
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place:ray:terminal-monotonic",
    placement_class: "delegated_python",
    runtime_id: "ray",
    capability_id: remoteCapability.capability_id,
  }
  const request = driver.buildSandboxRequest!({
    requestId: "req:ray:terminal-monotonic",
    capability: remoteCapability,
    placement,
    command: ["python", "-c", "print('monotonic')"],
  })
  const execution = driver.execute!(request, {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
    capability: remoteCapability,
    placement,
    driverId: "ray",
  })

  await firstObservationStarted
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: null,
  })
  releaseConflictingTerminal()

  assert.equal((await execution).status, "cancelled")
  assert.deepEqual(states, ["accepted", "cancelled"])
})





test("scheduled adapter cancels a handle that arrives after timeout settlement", async () => {
  let releaseSubmit!: (value: { executionId: string }) => void
  let cancelled = false
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-late-submit",
    submit: async () => new Promise((resolve) => {
      releaseSubmit = resolve
    }),
    async observe() {
      return { state: cancelled ? "cancelled" : "running" }
    },
    async cancel(_executionId, context) {
      assert.equal(context.signal.aborted, false)
      cancelled = true
    },
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      cancellationObservationTimeoutMs: 20,
      recordEvidence() {},
    })],
  })
  const execution = world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:late",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req:ray:late",
    command: ["python", "-c", "print('late')"],
    driverId: "ray",
    deadlineMs: 5,
    terminationGraceMs: 2,
  })
  const result = await execution
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.terminationObserved, false)

  releaseSubmit({ executionId: "ray-late-1" })
  await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(cancelled, true)
})

test("scheduled cancellation bounds a hung backend cancel call", async () => {
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-hung-cancel",
    async submit() {
      return { executionId: "ray-hung-cancel-1" }
    },
    async observe() {
      return { state: "running" }
    },
    async cancel() {
      return new Promise<void>(() => {})
    },
  }
  const driver = makeRayExecutionDriver(backend, {
    pollIntervalMs: 1,
    cancellationObservationTimeoutMs: 10,
    recordEvidence() {},
  })
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place:ray:hung-cancel",
    placement_class: "delegated_python",
    runtime_id: "ray",
    capability_id: remoteCapability.capability_id,
  }
  const request = driver.buildSandboxRequest!({
    requestId: "req:ray:hung-cancel",
    capability: remoteCapability,
    placement,
    command: ["python", "-c", "print('hung cancel')"],
  })
  const executionController = new AbortController()
  const execution = driver.execute!(request, {
    signal: executionController.signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
    capability: remoteCapability,
    placement,
    driverId: "ray",
  })
  const executionRejected = assert.rejects(execution)
  await new Promise((resolve) => setTimeout(resolve, 1))
  executionController.abort(new Error("cancel requested"))

  await assert.rejects(
    () => Promise.resolve(driver.terminate!(request, {
      reason: "cancelled",
      signal: new AbortController().signal,
      deadlineAtMs: null,
    })),
    /cancellation failed/,
  )
  await executionRejected
})


test("scheduled adapter cleans up after observation transport failure", async () => {
  let cancelled = false
  let observations = 0
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-observe-failure",
    async submit() {
      return { executionId: "ray-observe-1" }
    },
    async observe() {
      observations++
      if (observations === 1) throw new Error("raw provider transport detail")
      return cancelled
        ? {
            state: "cancelled",
            result: {
              schema_version: "bb.sandbox_result.v1",
              request_id: "req:ray:observe-failure",
              status: "cancelled",
              stdout_ref: `sha256:${"a".repeat(64)}`,
              stderr_ref: `sha256:${"d".repeat(64)}`,
              artifact_refs: [
                `sha256:${"a".repeat(64)}`,
                `sha256:${"d".repeat(64)}`,
              ],
              side_effect_digest: `sha256:${"b".repeat(64)}`,
              usage: { exit_code: null },
              evidence_refs: [`sha256:${"c".repeat(64)}`],
              error: { reason: "execution_cancelled" },
            },
          }
        : { state: "running" }
    },
    async cancel(_executionId, context) {
      assert.equal(context.signal.aborted, false)
      cancelled = true
    },
  }
  const driver = makeRayExecutionDriver(backend, {
    pollIntervalMs: 1,
    cancellationObservationTimeoutMs: 20,
    recordEvidence() {},
  })
  const world = createExecutionWorld({ drivers: [driver] })
  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:observe-failure",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req:ray:observe-failure",
    command: ["python", "-c", "print('observe')"],
    driverId: "ray",
    terminationGraceMs: 50,
  })
  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.sandboxResult?.error?.reason, "adapter_execution_failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(
    JSON.stringify(result.sandboxResult).includes("raw provider transport detail"),
    false,
  )
  assert.equal(cancelled, true)
  assert.equal(result.livenessEvidence.terminationObserved, true)
  assert.deepEqual(result.sandboxResult?.evidence_refs, [])
  assert.equal(result.sandboxResult?.side_effect_digest, null)
})

test("scheduled adapter rejects unknown states before recording evidence", async () => {
  let cancelled = false
  const recordedStates: string[] = []
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-invalid-state",
    async submit() {
      return { executionId: "ray-invalid-state-1" }
    },
    async observe() {
      return cancelled
        ? { state: "cancelled" }
        : ({ state: "BOGUS" } as never)
    },
    async cancel() {
      cancelled = true
    },
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence(evidence) {
        recordedStates.push(evidence.state)
      },
    })],
  })

  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:invalid-state",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: remoteCapability.capability_id,
    },
    requestId: "req:ray:invalid-state",
    command: ["python", "-c", "print('invalid')"],
    driverId: "ray",
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(result.livenessEvidence.state, "failed")
  assert.equal(recordedStates.includes("BOGUS"), false)
})

test("scheduled adapter coalesces identical active requests and rejects collisions", async () => {
  let releases = 0
  let submits = 0
  let releaseSubmit!: (value: { executionId: string }) => void
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-idempotent",
    async submit() {
      submits++
      return new Promise((resolve) => {
        releaseSubmit = resolve
      })
    },
    async observe() {
      return {
        state: "completed",
        result: {
          schema_version: "bb.sandbox_result.v1",
          request_id: "req:ray:idempotent",
          status: "completed",
          stdout_ref: `sha256:${"0".repeat(64)}`,
          stderr_ref: `sha256:${"1".repeat(64)}`,
          artifact_refs: [
            `sha256:${"0".repeat(64)}`,
            `sha256:${"1".repeat(64)}`,
          ],
          side_effect_digest: `sha256:${"2".repeat(64)}`,
          usage: { exit_code: 0 },
          evidence_refs: [`sha256:${"3".repeat(64)}`],
        },
      }
    },
    async cancel() {
      releases++
    },
  }
  const driver = makeRayExecutionDriver(backend, {
    pollIntervalMs: 1,
    recordEvidence() {},
  })
  const placement: ExecutionPlacementV1 = {
    schema_version: "bb.execution_placement.v1",
    placement_id: "place:ray:idempotent",
    placement_class: "delegated_python",
    runtime_id: "ray",
    capability_id: remoteCapability.capability_id,
  }
  const context = {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
    capability: remoteCapability,
    placement,
    driverId: "ray",
  }
  const request = driver.buildSandboxRequest!({
    requestId: "req:ray:idempotent",
    capability: remoteCapability,
    placement,
    command: ["python", "-c", "print('same')"],
    metadata: { alpha: 1, beta: 2 },
  })
  const execute = driver.execute
  assert.ok(execute)
  const first = execute(request, context)
  const second = execute(
    {
      ...request,
      metadata: {
        driver: request.metadata!["driver"]!,
        beta: 2,
        alpha: 1,
      },
    },
    context,
  )
  const collision = await execute(
    { ...request, command: ["python", "-c", "print('different')"] },
    context,
  )
  assert.equal(collision?.status, "failed")
  assert.equal(collision?.error?.reason, "request_id_collision")
  assert.equal(releases, 0)
  releaseSubmit({ executionId: "ray-idempotent-1" })
  assert.deepEqual(await first, await second)
  assert.equal(submits, 1)
})

test("Slurm scheduled adapter confirms cancellation before timeout settlement", async () => {
  let cancelled = false
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "slurm-cluster",
    async submit() {
      return { executionId: "12345" }
    },
    async observe() {
      if (!cancelled) return { state: "running" }
      return {
        state: "cancelled",
        result: {
          schema_version: "bb.sandbox_result.v1",
          request_id: "req:slurm:1",
          status: "cancelled",
          stdout_ref: `sha256:${"0".repeat(64)}`,
          stderr_ref: `sha256:${"1".repeat(64)}`,
          artifact_refs: [
            `sha256:${"0".repeat(64)}`,
            `sha256:${"1".repeat(64)}`,
          ],
          side_effect_digest: `sha256:${"2".repeat(64)}`,
          usage: { exit_code: null },
          evidence_refs: [`sha256:${"3".repeat(64)}`],
          error: { reason: "execution_cancelled" },
        },
      } as const
    },
    async cancel(executionId) {
      assert.equal(executionId, "12345")
      cancelled = true
    },
  }
  const world = createExecutionWorld({
    drivers: [
      makeSlurmExecutionDriver(backend, {
        pollIntervalMs: 1,
        cancellationObservationTimeoutMs: 20,
        recordEvidence() {},
      }),
    ],
  })
  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:slurm:1",
      placement_class: "remote_worker",
      runtime_id: "slurm",
      capability_id: remoteCapability.capability_id,
      metadata: {},
    },
    requestId: "req:slurm:1",
    command: ["python", "-c", "print('world')"],
    driverId: "slurm",
    deadlineMs: 5,
    terminationGraceMs: 50,
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(cancelled, true)
  assert.equal(result.sandboxResult?.status, "timed_out")
  assert.equal(result.livenessEvidence.state, "timed_out")
  assert.equal(result.sandboxResult?.stdout_ref, `sha256:${"0".repeat(64)}`)
  assert.deepEqual(result.sandboxResult?.evidence_refs, [])
  assert.equal(result.sandboxResult?.side_effect_digest, null)
  assert.equal(result.livenessEvidence.terminationObserved, true)
})

test("scheduled adapter canonicalizes terminal observations without a result", async () => {
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "slurm-cluster",
    async submit() {
      return { executionId: "terminal-without-result" }
    },
    async observe() {
      return { state: "failed" }
    },
    async cancel() {},
  }
  const world = createExecutionWorld({
    drivers: [makeSlurmExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence() {},
    })],
  })
  const command = ["python", "-c", "raise SystemExit(2)"]

  const result = await world.execute({
    kind: "sandbox",
    capability: remoteCapability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:slurm:terminal-without-result",
      placement_class: "remote_worker",
      runtime_id: "slurm",
      capability_id: remoteCapability.capability_id,
      metadata: {},
    },
    requestId: "req:slurm:terminal-without-result",
    command,
    driverId: "slurm",
    deadlineMs: 1_000,
    terminationGraceMs: 50,
  })

  assert.equal(result.kind, "sandbox")
  assert.deepEqual(result.sandboxResult, {
    schema_version: "bb.sandbox_result.v1",
    request_id: "req:slurm:terminal-without-result",
    status: "failed",
    ...buildCanonicalSandboxEvidence({
      command,
      status: "failed",
      exitCode: null,
      stdout: "",
      stderr: "",
      evidenceMode: remoteCapability.evidence_mode,
    }),
    error: { reason: "execution_failed" },
  })
  const fallbackStdoutRef = result.sandboxResult?.stdout_ref
  assert.ok(fallbackStdoutRef)
  assert.equal(
    await readFile(new URL(canonicalSandboxArtifactUri(fallbackStdoutRef)), "utf8"),
    "",
  )
})

test("scheduled adapter rejects provider-specific terminal result evidence", async () => {
  const capability = {
    ...remoteCapability,
    capability_id: "cap:ray:minimal-evidence",
    evidence_mode: "minimal" as const,
  }
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "ray-minimal-evidence",
    async submit() {
      return { executionId: "ray-minimal-evidence-1" }
    },
    async observe() {
      return {
        state: "completed",
        result: {
          schema_version: "bb.sandbox_result.v1" as const,
          request_id: "req:ray:minimal-evidence",
          status: "completed" as const,
          stdout_ref: `sha256:${"0".repeat(64)}`,
          stderr_ref: `sha256:${"1".repeat(64)}`,
          artifact_refs: [
            `sha256:${"0".repeat(64)}`,
            `sha256:${"1".repeat(64)}`,
          ],
          side_effect_digest: `sha256:${"2".repeat(64)}`,
          usage: { exit_code: 0 },
          evidence_refs: ["ray://provider/result"],
        },
      }
    },
    async cancel() {},
  }
  const world = createExecutionWorld({
    drivers: [makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      recordEvidence() {},
    })],
  })

  const result = await world.execute({
    kind: "sandbox",
    capability,
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place:ray:minimal-evidence",
      placement_class: "delegated_python",
      runtime_id: "ray",
      capability_id: capability.capability_id,
    },
    requestId: "req:ray:minimal-evidence",
    command: ["python", "-c", "print('world')"],
    driverId: "ray",
  })

  assert.equal(result.kind, "sandbox")
  assert.equal(result.sandboxResult?.status, "failed")
  assert.equal(JSON.stringify(result.sandboxResult).includes("ray://provider"), false)
})




test("SSH Slurm backend durably submits, polls, and cancels with external scheduler evidence", async () => {
  const invocations: string[][] = []
  const commandTimeouts: number[] = []
  const commandOutputLimits: number[] = []
  let encodedMetadata = ""
  let sacctResult = ""
  let squeueResult = ""
  let oversizedTerminalOutput = false
  let missingTerminalOutput = false
  let malformedTerminalOutput = false
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/breadboard evidence",
    maxOutputBytes: 8,
    async runCommand(program, args, options) {
      assert.equal(program, "ssh")
      invocations.push([...args])
      commandTimeouts.push(options.timeoutMs)
      commandOutputLimits.push(options.maxOutputBytes)
      const remoteCommand = args[1] ?? ""
      if (remoteCommand.includes("setsid sh -c")) return { stdout: "", stderr: "" }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat"))
        return { stdout: "24680;cluster\n", stderr: "" }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64"))
        return { stdout: encodedMetadata, stderr: "" }
      if (remoteCommand.includes("squeue")) return { stdout: squeueResult, stderr: "" }
      if (remoteCommand.includes("sacct")) return { stdout: sacctResult, stderr: "" }
      if (remoteCommand.includes("scancel")) return { stdout: "", stderr: "" }
      if (
        (remoteCommand.includes(".out") || remoteCommand.includes(".err"))
        && remoteCommand.includes("sha256sum")
      ) {
        if (missingTerminalOutput) return { stdout: "M\n", stderr: "" }
        const content = remoteCommand.includes(".out")
          ? oversizedTerminalOutput ? "abcdefghij" : "world\n"
          : oversizedTerminalOutput ? "error-data" : ""
        const bytes = malformedTerminalOutput
          ? remoteCommand.includes(".out") ? Buffer.from([0x66, 0x80]) : Buffer.from([0x67, 0xff])
          : Buffer.from(content, "utf8")
        const digest = createHash("sha256").update(bytes).digest("hex")
        return {
          stdout: `F:${bytes.length}:${digest}\n`,
          stderr: "",
        }
      }
      if (remoteCommand.startsWith("dd if=")) {
        const content = remoteCommand.includes(".out")
          ? oversizedTerminalOutput ? "abcdefghij" : "world\n"
          : oversizedTerminalOutput ? "error-data" : ""
        const offset = Number.parseInt(
          /\bskip=(\d+)/.exec(remoteCommand)?.[1] ?? "",
          10,
        )
        const count = Number.parseInt(
          /\bcount=(\d+)/.exec(remoteCommand)?.[1] ?? "",
          10,
        )
        const source = malformedTerminalOutput
          ? remoteCommand.includes(".out") ? Buffer.from([0x66, 0x80]) : Buffer.from([0x67, 0xff])
          : Buffer.from(content, "utf8")
        const bytes = source.subarray(offset, offset + count)
        return { stdout: bytes.toString("base64"), stderr: "" }
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:backend",
    capability: slurmCapability,
    command: ["python", "-c", "print(\"a'b\")"],
    workspaceRef: "/tmp/workspace with space",
    placementClass: "remote_worker",
  })
  const jobName = slurmTestJobName(
    "cluster.example",
    "/tmp/breadboard evidence",
    request.request_id,
  )
  sacctResult = `${jobName}|COMPLETED|0:0|node-a\n`
  squeueResult = `${jobName}|RUNNING|node-a\n`
  const imagedRequest = buildRemoteSandboxRequest({
    requestId: "req:slurm:imaged",
    capability: slurmCapability,
    command: ["python", "-c", "print('wrong runtime')"],
    placementClass: "remote_worker",
    imageRef: "docker://breadboard/worker:latest",
  })
  await assert.rejects(
    () =>
      backend.submit(imagedRequest, {
        signal: new AbortController().signal,
        deadlineAtMs: null,
        terminationGraceMs: 50,
      }),
    /cannot honor an image_ref/,
  )
  assert.equal(invocations.length, 0)

  encodedMetadata = Buffer.from(JSON.stringify(request), "utf8").toString("base64")

  const handle = await backend.submit(request, {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
  })
  assert.ok(handle.evidenceRefs?.some((ref) => ref.endsWith(".receipt")))
  assert.ok(handle.evidenceRefs?.some((ref) => ref.endsWith(".command.b64")))
  assert.ok(handle.evidenceRefs?.some((ref) => ref.endsWith(".log")))
  assert.match(handle.executionId, /^24680@[0-9a-f]{64}$/)
  assert.equal(invocations[0]?.[1]?.includes(`mkdir -p "$evidence"`), true)
  assert.equal(invocations[0]?.[1]?.includes(`umask 077`), true)
  assert.equal(invocations[0]?.[1]?.includes(`stat -c %u "$evidence"`), true)
  assert.equal(invocations[0]?.[1]?.includes(`chmod 700 "$evidence" || exit 75`), true)
  assert.equal(invocations[0]?.[1]?.includes(`stat -c %a "$evidence"`), true)
  assert.equal(invocations[0]?.[1]?.includes("setsid sh -c"), true)
  assert.equal(invocations[0]?.[1]?.includes("--wrap="), true)
  const submissionCommand = (invocations[0]?.[1]?.match(/[A-Za-z0-9+/]{40,}={0,2}/g) ?? [])
    .map((encoded) => Buffer.from(encoded, "base64").toString("utf8"))
    .find((decoded) => decoded.includes("sbatch --parsable"))
  assert.ok(submissionCommand?.includes("--chdir='/tmp/workspace with space'"))
  assert.ok(invocations[0]?.[1]?.includes(`[ ! -s "$receipt" ]`))
  assert.ok(invocations[0]?.[1]?.includes(`cancel_by_name() { touch "$cancel"`))
  assert.ok(invocations[0]?.[1]?.includes("scancel --name"))
  assert.ok(invocations[0]?.[1]?.includes("timeout 30s sh -c"))
  assert.ok(invocations[0]?.[1]?.includes(`[ ! -f "$cancel" ] || exit 75`))
  assert.ok(invocations[0]?.[1]?.includes(`[ $((now-created)) -ge`))
  assert.equal(invocations[0]?.[1]?.includes(`cat "$lock/job"`), false)
  assert.ok(invocations[0]?.[1]?.includes(`> "$lock/attempt"`))
  assert.ok(invocations[0]?.[1]?.includes("scancel --name"))
  assert.ok(invocations[0]?.[1]?.includes("timeout 30s sbatch"))
  assert.ok(invocations[0]?.[1]?.includes(`rm -f "$cancel"`))
  assert.ok(invocations[0]?.[1]?.includes(`rm -rf "$lock"; }; trap cleanup EXIT;`))

  const activeObservation = await backend.observe(handle.executionId)
  assert.equal(activeObservation.state, "running")
  assert.ok(
    activeObservation.evidenceRefs?.includes("slurm://job/24680/node/node-a"),
  )
  assert.ok(invocations[0]?.[1]?.includes(`case "$id" in ""|*[!0-9]*) exit 65`))
  assert.ok(invocations[0]?.[1]?.includes(`"$job" "$attempt" "$digest" > "$receipt.tmp"`))
  squeueResult = "|RUNNING|node-a\n"
  await assert.rejects(
    () => backend.observe(handle.executionId),
    /no longer owns the scheduler job id/,
  )
  squeueResult = ""
  const observation = await backend.observe(handle.executionId)
  assert.equal(observation.state, "completed")
  assert.equal(observation.result?.status, "completed")
  assert.equal(observation.result?.request_id, request.request_id)
  assert.deepEqual(observation.result?.evidence_refs, [
    observation.result?.side_effect_digest,
  ])
  assert.equal(observation.result?.usage?.exit_code, 0)
  assert.ok(observation.result?.stdout_ref?.startsWith("sha256:"))
  assert.ok(observation.result?.stderr_ref?.startsWith("sha256:"))
  assert.deepEqual(observation.result?.artifact_refs, [
    observation.result?.stdout_ref,
    observation.result?.stderr_ref,
  ])
  malformedTerminalOutput = true
  const malformedObservation = await backend.observe(handle.executionId)
  assert.equal(
    malformedObservation.result?.stdout_ref,
    `sha256:${createHash("sha256").update("f\ufffd").digest("hex")}`,
  )
  assert.equal(
    malformedObservation.result?.stderr_ref,
    `sha256:${createHash("sha256").update("g\ufffd").digest("hex")}`,
  )
  malformedTerminalOutput = false
  missingTerminalOutput = true
  await assert.rejects(
    () => backend.observe(handle.executionId),
    /completed Slurm execution output is missing/,
  )
  missingTerminalOutput = false
  assert.ok(observation.result?.side_effect_digest?.startsWith("sha256:"))
  assert.ok(
    invocations.some(
      (args) =>
        args[1]?.includes("wc -c")
        && args[1]?.includes("sha256sum"),
    ),
  )
  assert.ok(invocations.some((args) => args[1]?.startsWith("dd if=")))
  assert.deepEqual(observation.evidenceRefs, [
    "slurm://job/24680",
    "slurm://job/24680/state/COMPLETED",
    "slurm://job/24680/node/node-a",
    "ssh://cluster.example/tmp/breadboard%20evidence/slurm-24680.out",
    "ssh://cluster.example/tmp/breadboard%20evidence/slurm-24680.err",
  ])
  sacctResult = `${jobName}|DEADLINE|0:0|node-a\n`
  const deadline = await backend.observe(handle.executionId)
  assert.equal(deadline.state, "timed_out")
  assert.deepEqual(deadline.result?.error, { reason: "execution_timed_out" })
  sacctResult = `${jobName}|FAILED|1:0|node-a\n`
  missingTerminalOutput = true
  await assert.rejects(
    () => backend.observe(handle.executionId),
    /failed Slurm execution output is missing/,
  )
  missingTerminalOutput = false
  sacctResult = `${jobName}|COMPLETED|0:9|node-a\n`
  const signalled = await backend.observe(handle.executionId)
  assert.equal(signalled.state, "failed")
  assert.equal(signalled.result?.usage?.exit_code, 137)
  oversizedTerminalOutput = true
  sacctResult = `${jobName}|CANCELLED|0:15|node-a\n`
  await assert.rejects(
    () => backend.observe(handle.executionId),
    /exceeds the configured per-stream limit/,
  )
  const cancelDeadlineAtMs = Date.now() + 100
  await backend.cancel(handle.executionId, {
    reason: "cancelled",
    signal: new AbortController().signal,
    deadlineAtMs: cancelDeadlineAtMs,
  })
  const cancelCommand = [...invocations]
    .reverse()
    .find((args) => args[1]?.includes("scancel --name"))
  assert.ok(cancelCommand?.[1]?.includes(jobName))
  assert.equal(cancelCommand?.[1]?.includes("scancel '24680'"), false)
  assert.equal(commandTimeouts.length, invocations.length)
  assert.ok(commandTimeouts.every((timeout) => timeout > 0 && timeout <= 30_000))
  assert.ok(commandOutputLimits.some((limit) => limit > 8))
  const metadataCommandIndex = invocations.findIndex(
    (args) => args[1]?.startsWith("cat ") && args[1]?.includes(".request.b64"),
  )
  assert.notEqual(metadataCommandIndex, -1)
  assert.equal(commandOutputLimits[metadataCommandIndex], 8 * 1024 * 1024)
  assert.ok((commandTimeouts.at(-1) ?? Number.POSITIVE_INFINITY) <= 100)
})

test("SSH Slurm backend rejects unsafe targets and relative evidence paths", () => {
  assert.throws(
    () => makeSshSlurmBackend({
      sshTarget: "-oProxyCommand=unsafe",
      remoteEvidenceDirectory: "/tmp/evidence",
    }),
    /option prefix/,
  )
  assert.throws(
    () => makeSshSlurmBackend({
      sshTarget: "cluster.example",
      remoteEvidenceDirectory: "tmp/evidence",
    }),
    /absolute path/,
  )
  assert.throws(
    () => makeSshSlurmBackend({
      sshTarget: "cluster.example",
      remoteEvidenceDirectory: "/tmp/..",
    }),
    /must not be the filesystem root/,
  )
  assert.throws(
    () => makeSshSlurmBackend({
      sshTarget: "cluster.example",
      remoteEvidenceDirectory: "/tmp/evidence",
      commandTimeoutMs: Number.POSITIVE_INFINITY,
    }),
    /positive safe integer/,
  )
  assert.throws(
    () => makeSshSlurmBackend({
      sshTarget: "cluster.example",
      remoteEvidenceDirectory: "/tmp/evidence",
      maxOutputBytes: Number.MAX_SAFE_INTEGER,
    }),
    /too large for framed transport/,
  )
  const backend: ScheduledExecutionBackendV1 = {
    backendId: "invalid-options",
    async submit() {
      return { executionId: "unused" }
    },
    async observe() {
      return { state: "running" }
    },
    async cancel() {},
  }
  assert.throws(
    () => makeRayExecutionDriver(backend, {
      pollIntervalMs: Number.NaN,
      recordEvidence() {},
    }),
    /pollIntervalMs/,
  )
  assert.throws(
    () => makeRayExecutionDriver(backend, {
      pollIntervalMs: 1,
      cancellationObservationTimeoutMs: Number.POSITIVE_INFINITY,
      recordEvidence() {},
    }),
    /cancellationObservationTimeoutMs/,
  )
})

test("SSH Slurm backend rejects network policies it cannot enforce", async () => {
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand() {
      assert.fail("network policy rejection must precede SSH")
    },
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:network-policy",
    capability: remoteCapability,
    command: ["python", "-c", "print('blocked')"],
    placementClass: "remote_worker",
  })

  await assert.rejects(
    () => backend.submit(request, {
      signal: new AbortController().signal,
      deadlineAtMs: null,
      terminationGraceMs: 50,
    }),
    /cannot enforce the requested network policy/,
  )
})

test("SSH Slurm backend recovers a durable receipt after an ambiguous launch acknowledgement", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:ambiguous",
    capability: slurmCapability,
    command: ["python", "-c", "print('durable')"],
    placementClass: "remote_worker",
  })
  const encodedMetadata = Buffer.from(JSON.stringify(request), "utf8").toString("base64")
  let launches = 0
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      if (remoteCommand.includes("setsid sh -c")) {
        launches++
        throw new Error("connection lost after remote launch")
      }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat"))
        return { stdout: "31415;cluster\n", stderr: "" }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64"))
        return { stdout: encodedMetadata, stderr: "" }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  const handle = await backend.submit(request, {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
  })
  assert.match(handle.executionId, /^31415@[0-9a-f]{64}$/)
  assert.equal(launches, 1)
})

test("SSH Slurm backend recovers an owned receipt when metadata recovery fails", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:metadata-failure",
    capability: slurmCapability,
    command: ["python", "-c", "print('recover handle')"],
    placementClass: "remote_worker",
  })
  const commands: string[] = []
  let submissionAttemptToken = ""
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      commands.push(remoteCommand)
      if (remoteCommand.includes("setsid sh -c")) {
        submissionAttemptToken =
          /\battempt=([0-9a-f]{32});/.exec(remoteCommand)?.[1] ?? ""
        assert.match(submissionAttemptToken, /^[0-9a-f]{32}$/)
        return { stdout: "", stderr: "" }
      }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat")) {
        return {
          stdout: `27182;cluster\n${submissionAttemptToken}\n`,
          stderr: "",
        }
      }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        throw new Error("metadata unavailable")
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  const handle = await backend.submit(request, {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
  })
  assert.match(handle.executionId, /^27182@[0-9a-f]{64}$/)
  assert.equal(commands.some((command) => command.includes("scancel '27182'")), false)
})

test("SSH Slurm backend never cancels an unowned pre-existing receipt", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:unowned-metadata-failure",
    capability: slurmCapability,
    command: ["python", "-c", "print('do not cancel')"],
    placementClass: "remote_worker",
  })
  const commands: string[] = []
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      commands.push(remoteCommand)
      if (remoteCommand.includes("setsid sh -c")) return { stdout: "", stderr: "" }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat")) {
        return { stdout: "27183;cluster\n", stderr: "" }
      }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        throw new Error("metadata unavailable")
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  await assert.rejects(
    () => backend.submit(request, {
      signal: new AbortController().signal,
      deadlineAtMs: null,
      terminationGraceMs: 50,
    }),
    /job ownership is unproven/,
  )
  assert.equal(commands.some((command) => command.includes("scancel '27183'")), false)
})

test("SSH Slurm backend recovers a digest-authorized receipt after restart", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:digest-recovery",
    capability: slurmCapability,
    command: ["python", "-c", "print('recover durable handle')"],
    placementClass: "remote_worker",
  })
  let receiptDigest = ""
  const commands: string[] = []
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      commands.push(remoteCommand)
      if (remoteCommand.includes("setsid sh -c")) {
        receiptDigest =
          /\bdigest=(sha256:[0-9a-f]{64});/.exec(remoteCommand)?.[1] ?? ""
        assert.match(receiptDigest, /^sha256:[0-9a-f]{64}$/)
        return { stdout: "", stderr: "" }
      }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat")) {
        return {
          stdout: `27184;cluster\n${"a".repeat(32)}\n${receiptDigest}\n`,
          stderr: "",
        }
      }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        throw new Error("metadata unavailable")
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  const handle = await backend.submit(request, {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
  })
  assert.match(handle.executionId, /^27184@[0-9a-f]{64}$/)
  assert.equal(commands.some((command) => command.includes("scancel '27184'")), false)
})

test("SSH Slurm backend rejects a reused request id with changed request content", async () => {
  const original = buildRemoteSandboxRequest({
    requestId: "req:slurm:collision",
    capability: slurmCapability,
    command: ["python", "-c", "print('original')"],
    placementClass: "remote_worker",
  })
  const changed = buildRemoteSandboxRequest({
    requestId: original.request_id,
    capability: slurmCapability,
    command: ["python", "-c", "print('changed')"],
    placementClass: "remote_worker",
  })
  const encodedMetadata = Buffer.from(JSON.stringify(original), "utf8").toString("base64")
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      if (remoteCommand.includes("setsid sh -c")) return { stdout: "", stderr: "" }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat")) {
        return { stdout: "16180;cluster\n", stderr: "" }
      }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        return { stdout: encodedMetadata, stderr: "" }
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  await assert.rejects(
    () => backend.submit(changed, {
      signal: new AbortController().signal,
      deadlineAtMs: null,
      terminationGraceMs: 50,
    }),
    /request_id collision; existing execution remains owned/,
  )
})

test("SSH Slurm backend refreshes metadata when the scheduler reuses a job id", async () => {
  const first = buildRemoteSandboxRequest({
    requestId: "req:slurm:first-job-id-owner",
    capability: slurmCapability,
    command: ["python", "-c", "print('first')"],
    placementClass: "remote_worker",
  })
  const second = buildRemoteSandboxRequest({
    requestId: "req:slurm:second-job-id-owner",
    capability: slurmCapability,
    command: ["python", "-c", "print('second')"],
    placementClass: "remote_worker",
  })
  const encoded = [first, second].map((request) =>
    Buffer.from(JSON.stringify(request), "utf8").toString("base64")
  )
  let metadataReads = 0
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      if (remoteCommand.includes("setsid sh -c")) return { stdout: "", stderr: "" }
      if (remoteCommand.includes("submission-") && remoteCommand.includes("then cat")) {
        return { stdout: "4242;cluster\n", stderr: "" }
      }
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        return {
          stdout: encoded[Math.min(metadataReads++, encoded.length - 1)]!,
          stderr: "",
        }
      }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })
  const context = {
    signal: new AbortController().signal,
    deadlineAtMs: null,
    terminationGraceMs: 50,
  }

  const firstHandle = await backend.submit(first, context)
  const secondHandle = await backend.submit(second, context)
  assert.match(firstHandle.executionId, /^4242@[0-9a-f]{64}$/)
  assert.match(secondHandle.executionId, /^4242@[0-9a-f]{64}$/)
  assert.notEqual(firstHandle.executionId, secondHandle.executionId)
  await assert.rejects(
    () => backend.observe(firstHandle.executionId),
    /no longer owns the scheduler job id/,
  )
  await assert.rejects(
    () => backend.cancel(firstHandle.executionId, {
      reason: "cancelled",
      signal: new AbortController().signal,
      deadlineAtMs: null,
    }),
    /no longer owns the scheduler job id/,
  )
  assert.equal(metadataReads, 4)
})

test("SSH Slurm backend leaves a durable cancel marker when cancellation precedes a receipt", async () => {
  const commands: string[] = []
  const controller = new AbortController()
  controller.abort(new Error("cancel now"))
  const backend = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp/evidence",
    async runCommand(_program, args, options) {
      const remoteCommand = args[1] ?? ""
      commands.push(remoteCommand)
      if (commands.length === 1) assert.equal(options.signal, controller.signal)
      else assert.equal(options.signal, undefined)
      return { stdout: "", stderr: "" }
    },
  })
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:cancel-before-receipt",
    capability: slurmCapability,
    command: ["python", "-c", "print('cancel')"],
    placementClass: "remote_worker",
  })

  await assert.rejects(
    () => backend.submit(request, {
      signal: controller.signal,
      deadlineAtMs: null,
      terminationGraceMs: 50,
    }),
    /submission was cancelled/,
  )
  assert.equal(commands.length, 2)
  assert.ok(commands[1]?.includes(".cancel';"))
  assert.ok(commands[0]?.includes("[ ! -f"))
  assert.ok(commands[0]?.includes("scancel --name"))
  assert.ok(commands[1]?.startsWith("touch "))
  assert.ok(commands[1]?.includes(".lock/attempt"))
  assert.ok(commands[1]?.includes("scancel --name"))
  assert.ok(commands[1]?.includes("receipt_attempt=$(sed -n '2p'"))
  assert.equal(commands[1]?.includes("receipt_digest="), false)
  assert.ok(commands[0]?.includes(`[ -f "$cancel" ] || [ ! -s "$receipt" ]`))
  assert.equal(commands[1]?.includes("sed -n '1p'"), false)
})

test("SSH Slurm backend reconciles an active job after adapter restart", async () => {
  const request = buildRemoteSandboxRequest({
    requestId: "req:slurm:restart",
    capability: slurmCapability,
    command: ["python", "-c", "print('restart')"],
    placementClass: "remote_worker",
  })
  const encodedMetadata = Buffer.from(JSON.stringify(request), "utf8").toString("base64")
  const recoveredJobName = slurmTestJobName(
    "cluster.example",
    "/tmp/evidence",
    request.request_id,
  )
  const recovered = makeSshSlurmBackend({
    sshTarget: "cluster.example",
    remoteEvidenceDirectory: "/tmp//nested/../evidence/",
    async runCommand(_program, args) {
      const remoteCommand = args[1] ?? ""
      if (remoteCommand.startsWith("cat ") && remoteCommand.includes(".request.b64")) {
        assert.ok(remoteCommand.includes("'/tmp/evidence/slurm-27182.request.b64'"))
        return { stdout: encodedMetadata, stderr: "" }
      }
      if (remoteCommand.includes("squeue"))
        return { stdout: `${recoveredJobName}|RUNNING|node-recovered\n`, stderr: "" }
      assert.fail(`unexpected Slurm command: ${remoteCommand}`)
    },
  })

  const observation = await recovered.observe("27182")
  assert.equal(observation.state, "running")
  assert.ok(
    observation.evidenceRefs?.includes("slurm://job/27182/node/node-recovered"),
  )
})
