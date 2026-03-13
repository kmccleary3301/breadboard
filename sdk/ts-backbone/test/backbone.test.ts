import test from "node:test"
import assert from "node:assert/strict"

import { createBackbone } from "../src/index.js"
import { buildWorkspaceCapabilitySet, createWorkspace } from "@breadboard/workspace"
import type { KernelEventV1, ProviderExchangeV1, RunRequestV1 } from "@breadboard/kernel-contracts"

const request: RunRequestV1 = {
  schema_version: "bb.run_request.v1",
  request_id: "req-1",
  entry_mode: "hostkit",
  task: "Say hello",
  workspace_root: "/tmp/project",
  requested_model: "openai/gpt-5.4-mini",
  requested_features: {},
  metadata: { host: "test" },
}

const exchange: ProviderExchangeV1 = {
  schema_version: "bb.provider_exchange.v1",
  exchange_id: "ex-1",
  request: {
    provider_family: "openai",
    runtime_id: "responses",
    route_id: "default",
    model: "openai/gpt-5.4-mini",
    stream: true,
    message_count: 1,
    tool_count: 0,
    metadata: {},
  },
  response: {
    message_count: 1,
    finish_reasons: ["stop"],
    usage: null,
    metadata: {},
  },
}

test("createBackbone opens a session with a projection profile", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1", workspaceRoot: "/tmp/project" })
  assert.equal(session.projectionProfile.id, "host_callbacks")
})

test("classifyProviderTurn returns a supported claim on the default profile", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const claim = session.classifyProviderTurn({ request, providerExchange: exchange, assistantText: "hello" })
  assert.equal(claim.level, "supported")
  assert.equal(claim.executionProfileId, "trusted_local")
  assert.equal(claim.executionProfile.backendHint, "inline")
  assert.equal(claim.recommendedHostMode, "inline")
  assert.equal(claim.confidence, "high")
})

test("runProviderTurn returns transcript and provider turn output", async () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const result = await session.runProviderTurn({
    request,
    providerExchange: exchange,
    assistantText: "hello from provider",
  })
  assert.equal(result.providerTurn?.providerExchange.exchange_id, "ex-1")
  assert.equal(result.transcript.items.length, 3)
  assert.deepEqual(result.coordinationInspection, {
    signals: [],
    review_verdicts: [],
    directives: [],
    latest_signal_by_code: {},
    unresolved_interventions: [],
    resolved_interventions: [],
  })
})

test("classifyToolTurn advertises remote profile when requested", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet({ canRunRemoteIsolated: true }),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const claim = session.classifyToolTurn({
    request,
    toolName: "ls",
    command: ["pwd"],
    driverIdHint: "remote",
  })
  assert.equal(claim.executionProfileId, "remote_isolated")
  assert.equal(claim.recommendedHostMode, "background")
})

test("inspectCoordination derives read-only intervention state from kernel events", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const backbone = createBackbone({ workspace })
  const session = backbone.openSession({ sessionId: "s-1" })
  const events: KernelEventV1[] = [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-1",
      runId: "run-1",
      sessionId: "s-1",
      seq: 1,
      ts: "2026-03-13T12:00:00Z",
      actor: "engine",
      visibility: "host",
      kind: "coordination.signal",
      payload: {
        schema_version: "bb.signal.v1",
        signal_id: "signal-human-1",
        code: "human_required",
        task_id: "task_worker_1",
        parent_task_id: "task_supervisor_1",
        mission_task_id: "task_supervisor_1",
        authority_scope: "task",
        status: "accepted",
        source: { kind: "runtime", emitter_role: "runtime", detail: null },
        evidence_refs: ["evidence://human-required"],
        payload: {
          required_input: "Approve guarded retry",
          blocking_reason: "Operator approval required",
          allowed_host_actions: ["continue", "checkpoint", "terminate"],
        },
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-2",
      runId: "run-1",
      sessionId: "s-1",
      seq: 2,
      ts: "2026-03-13T12:00:01Z",
      actor: "engine",
      visibility: "host",
      kind: "coordination.review_verdict",
      payload: {
        schema_version: "bb.review_verdict.v1",
        verdict_id: "review-1",
        reviewer_task_id: "task_supervisor_1",
        reviewer_role: "supervisor",
        subject: {
          kind: "signal",
          signal_id: "signal-human-1",
          signal_code: "human_required",
          source_task_id: "task_worker_1",
          mission_task_id: "task_supervisor_1",
        },
        verdict_code: "human_required",
        mission_completed: false,
        required_deliverable_refs: [],
        deliverable_refs: [],
        missing_deliverable_refs: [],
        blocking_reason: "Operator approval required",
        recommended_next_action: null,
        support_claim_ref: null,
        signal_evidence_refs: ["evidence://human-required"],
        metadata: {},
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: "evt-3",
      runId: "run-1",
      sessionId: "s-1",
      seq: 3,
      ts: "2026-03-13T12:00:02Z",
      actor: "human",
      visibility: "host",
      kind: "coordination.directive",
      payload: {
        schema_version: "bb.directive.v1",
        directive_id: "directive-host-1",
        directive_code: "continue",
        issuer_task_id: "host::task_supervisor_1",
        issuer_role: "host",
        target_task_id: "task_worker_1",
        target_job_id: "job_worker_1",
        based_on_verdict_id: "review-1",
        based_on_signal_id: "signal-human-1",
        payload: { wake_target: true, intervention_response: true },
        evidence_refs: [],
        metadata: {},
      },
    },
  ]

  const snapshot = session.inspectCoordination({ events })
  assert.equal(snapshot.latest_signal_by_code.human_required?.signal_id, "signal-human-1")
  assert.equal(snapshot.unresolved_interventions.length, 0)
  assert.equal(snapshot.resolved_interventions.length, 1)
  assert.deepEqual(snapshot.resolved_interventions[0].allowed_host_actions, ["continue", "checkpoint", "terminate"])
  assert.equal(snapshot.resolved_interventions[0].host_responses[0]?.directive_code, "continue")
  assert.equal(session.inspectCoordination().resolved_interventions.length, 1)
})
