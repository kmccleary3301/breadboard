import test from "node:test"
import assert from "node:assert/strict"

import type { ExecutionCapabilityV1 } from "@breadboard/kernel-contracts"
import { buildExecutionDriverUnsupportedCase } from "@breadboard/execution-drivers"
import {
  buildRemoteExecutionErrorSummary,
  buildRemoteExecutionRequestEnvelope,
  buildRemoteExecutionResponseEnvelope,
  buildRemoteSandboxRequest,
  chooseRemotePlacement,
  executeRemoteSandboxRequest,
  makeRemoteExecutionDriver,
  makeRemoteTerminalSessionDriver,
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
                      terminal_state: "signaled",
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
  const signaled = await driver.interactTerminalSession?.({
    terminalSessionId: "term-remote-keepalive",
    interactionKind: "signal",
    signal: "SIGTERM",
    causingCallId: "call-remote-signal-1",
  })
  assert.equal(signaled?.end?.terminal_state, "signaled")
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
