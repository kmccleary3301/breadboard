import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type KernelEventV2,
  type ProviderExchangeV1,
  type RunRequestV1,
  type SandboxRequestV1,
  type SandboxResultV1,
  type SessionTranscriptV2,
  type ToolCallV2,
  type ToolExecutionOutcomeV2,
  type ToolModelRenderV2,
} from "@breadboard/kernel-contracts"
import {
  buildExecutionDriverUnsupportedCase,
  buildPlannedExecution,
  type ExecutionDriverV1,
} from "@breadboard/execution-drivers"
import {
  chooseTrustedLocalPlacement,
  executeLocalProcessSandboxRequest,
  trustedLocalExecutionDriver,
} from "@breadboard/execution-driver-local"
import {
  chooseOciPlacement,
  executeOciSandboxRequest,
  ociExecutionDriver,
} from "@breadboard/execution-driver-oci"
import {
  chooseRemotePlacement,
  executeRemoteSandboxRequest,
  makeRemoteExecutionDriver,
} from "@breadboard/execution-driver-remote"

import {
  buildExecutionCapabilityFromRunRequest,
  buildExecutionPlacement,
  buildRunContextFromRequest,
  buildTranscriptContinuationPatch,
} from "./contracts.js"
import { buildKernelEventId, normalizeTranscriptContractItems } from "./transcript.js"
import type {
  DriverMediatedToolTurnOptions,
  DriverMediatedToolTurnResult,
  ProviderTextContinuationTurnOptions,
  ProviderTextTurnOptions,
  ProviderTextTurnResult,
  ScriptedToolTurnOptions,
  StaticTextTurnOptions,
  StaticTextTurnResult,
} from "./types.js"

function buildDriverMediatedToolOutcome(input: {
  sandboxResult: SandboxResultV1
  callId: string
  toolName: string
  completedAtUtc: string
}): ToolExecutionOutcomeV2 {
  const terminalState =
    input.sandboxResult.status === "completed"
      ? "completed"
      : input.sandboxResult.status === "timed_out"
        ? "timed_out"
        : "errored"
  const outcome: Record<string, unknown> = {
    schema_version: "bb.tool_execution_outcome.v2",
    call_id: input.callId,
    terminal_state: terminalState,
    completed_at_utc: input.completedAtUtc,
    result:
      input.sandboxResult.status === "completed"
        ? {
            tool: input.toolName,
            stdout_ref: input.sandboxResult.stdout_ref ?? null,
            stderr_ref: input.sandboxResult.stderr_ref ?? null,
            artifact_refs: input.sandboxResult.artifact_refs ?? [],
            evidence_refs: input.sandboxResult.evidence_refs ?? [],
            side_effect_digest: input.sandboxResult.side_effect_digest ?? null,
            usage: input.sandboxResult.usage ?? null,
          }
        : null,
    visibility: { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" },
    metadata: {
      placement_id: input.sandboxResult.placement_id ?? null,
      sandbox_status: input.sandboxResult.status,
      stdout_ref: input.sandboxResult.stdout_ref ?? null,
      stderr_ref: input.sandboxResult.stderr_ref ?? null,
      artifact_refs: input.sandboxResult.artifact_refs ?? [],
      evidence_refs: input.sandboxResult.evidence_refs ?? [],
      side_effect_digest: input.sandboxResult.side_effect_digest ?? null,
      usage: input.sandboxResult.usage ?? null,
    },
  }
  if (input.sandboxResult.status !== "completed") {
    outcome.error = input.sandboxResult.error ?? {
      message: `${input.toolName} ended with status ${input.sandboxResult.status}`,
    }
  }
  return assertValid<ToolExecutionOutcomeV2>("toolExecutionOutcomeV2", outcome)
}

function buildDriverMediatedToolRender(input: {
  sandboxResult: SandboxResultV1
  callId: string
  toolName: string
  command: string[]
}): ToolModelRenderV2 {
  const preview =
    input.sandboxResult.status === "completed"
      ? `${input.toolName} completed via sandbox (${input.command.join(" ")})`
      : `${input.toolName} ${input.sandboxResult.status} via sandbox`
  return assertValid<ToolModelRenderV2>("toolModelRenderV2", {
    schema_version: "bb.tool_model_render.v2",
    call_id: input.callId,
    visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
    parts: [
      {
        part_kind: input.sandboxResult.status === "completed" ? "text" : "error",
        content: preview,
        truncated: false,
      },
    ],
    metadata: {
      placement_id: input.sandboxResult.placement_id ?? null,
      stdout_ref: input.sandboxResult.stdout_ref ?? null,
      artifact_count: input.sandboxResult.artifact_refs?.length ?? 0,
      evidence_count: input.sandboxResult.evidence_refs?.length ?? 0,
    },
  })
}

function chooseDriverMediatedPlacement(
  capability: ExecutionCapabilityV1,
  driverIdHint?: DriverMediatedToolTurnOptions["driverIdHint"],
): ExecutionPlacementV1["placement_class"] {
  if (driverIdHint === "remote" || capability.isolation_class === "remote_service") {
    return chooseRemotePlacement(capability)
  }
  if (driverIdHint === "oci" || ["oci", "gvisor", "kata"].includes(capability.isolation_class)) {
    return chooseOciPlacement(capability)
  }
  return chooseTrustedLocalPlacement(capability)
}

function buildDriverExecutionAdapter(
  plan: NonNullable<ReturnType<typeof buildPlannedExecution>>,
  options: DriverMediatedToolTurnOptions,
): ((request: SandboxRequestV1, context: {
  capability: ExecutionCapabilityV1
  placement: ExecutionPlacementV1
  driverId: string
}) => Promise<SandboxResultV1>) | null {
  if (options.executeSandbox) {
    return options.executeSandbox
  }

  if (plan.driver.driverId === "local-process" && options.localCommandExecutor) {
    return (request) => executeLocalProcessSandboxRequest(request, { commandExecutor: options.localCommandExecutor })
  }

  if (plan.driver.driverId === "oci" && (options.ociCommandExecutor || options.ociRuntimeCommand || options.ociWorkspaceMountTarget)) {
    return (request) =>
      executeOciSandboxRequest(request, {
        commandExecutor: options.ociCommandExecutor,
        runtimeCommand: options.ociRuntimeCommand,
        workspaceMountTarget: options.ociWorkspaceMountTarget,
      })
  }

  if (plan.driver.driverId === "remote" && options.remoteHttp) {
    return (request) => executeRemoteSandboxRequest(request, options.remoteHttp!)
  }

  if (plan.driver.driverId === "remote" && options.remoteExecutor) {
    return (request) => options.remoteExecutor!(request)
  }

  if (plan.driver.execute) {
    return (request) => plan.driver.execute!(request)
  }

  return null
}

export async function executeDriverMediatedToolTurn(
  requestInput: RunRequestV1,
  options: DriverMediatedToolTurnOptions,
): Promise<DriverMediatedToolTurnResult> {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  const capability = buildExecutionCapabilityFromRunRequest(request, {
    capabilityId: `cap:${request.request_id}:driver_tool`,
    securityTier: options.securityTier ?? "trusted_dev",
    isolationClass: options.isolationClass ?? "process",
    evidenceMode: options.evidenceMode ?? "replay_strict",
    allowReadPaths: options.workspaceRef ? [options.workspaceRef] : request.workspace_root ? [request.workspace_root] : [],
    allowWritePaths: options.workspaceRef ? [options.workspaceRef] : request.workspace_root ? [request.workspace_root] : [],
    allowRunPrograms: options.allowRunPrograms ?? [],
    allowNetHosts: options.allowNetHosts ?? [],
    ttyMode: "optional",
  })
  const placementClass = chooseDriverMediatedPlacement(capability, options.driverIdHint)
  const placement = buildExecutionPlacement(capability, {
    placementId: `${request.request_id}:placement:${placementClass}`,
    placementClass,
    runtimeId:
      placementClass === "local_process"
        ? "breadboard.ts-execution-driver-local"
        : placementClass === "remote_worker" ||
            placementClass === "delegated_python" ||
            placementClass === "delegated_oci" ||
            placementClass === "delegated_microvm"
          ? "breadboard.ts-execution-driver-remote"
        : "breadboard.ts-execution-driver-oci",
  })
  const remoteDriver =
    options.remoteExecutor || options.remoteHttp
      ? makeRemoteExecutionDriver(options.remoteExecutor, options.remoteHttp)
      : makeRemoteExecutionDriver()
  const drivers: ExecutionDriverV1[] =
    options.driverIdHint === "remote"
      ? [remoteDriver, ociExecutionDriver, trustedLocalExecutionDriver]
      : options.driverIdHint === "oci"
        ? [ociExecutionDriver, trustedLocalExecutionDriver, remoteDriver]
        : [trustedLocalExecutionDriver, ociExecutionDriver, remoteDriver]
  const plan = buildPlannedExecution({
    capability,
    placement,
    drivers,
    requestId: `${request.request_id}:sandbox`,
    command: options.command,
    workspaceRef: options.workspaceRef ?? request.workspace_root ?? null,
    imageRef: options.imageRef ?? null,
    metadata: {
      tool_name: options.toolName,
      tool_description: options.toolDescription ?? null,
    },
  })
  if (!plan || !plan.sandboxRequest) {
    const unsupported = buildExecutionDriverUnsupportedCase({
      capability,
      placementClass,
      fallbackAllowed: false,
      fallbackTaken: false,
      summary: `No execution driver produced a sandbox request for ${placementClass}`,
    })
    throw new Error(unsupported.summary)
  }

  const executeSandbox = buildDriverExecutionAdapter(plan, options)
  if (!executeSandbox) {
    const unsupported = buildExecutionDriverUnsupportedCase({
      capability,
      placementClass,
      fallbackAllowed: false,
      fallbackTaken: false,
      summary: `Execution driver ${plan.driver.driverId} has no executable backend for ${placementClass}`,
    })
    throw new Error(unsupported.summary)
  }
  const sandboxResult = assertValid<SandboxResultV1>(
    "sandboxResult",
    await executeSandbox(plan.sandboxRequest, {
      capability: plan.capability,
      placement: plan.placement,
      driverId: plan.driver.driverId,
    }),
  )
  const callId = `${request.request_id}:tool:1`
  const completedAtUtc = options.startedAt ?? new Date().toISOString()
  const toolCallState: ToolCallV2["state"] =
    sandboxResult.status === "completed" ? "completed" : sandboxResult.status === "timed_out" ? "timed_out" : "failed"
  const toolCall = assertValid<ToolCallV2>("toolCallV2", {
    schema_version: "bb.tool_call.v2",
    call_id: callId,
    tool_name: options.toolName,
    args: {
      command: options.command,
      image_ref: options.imageRef ?? null,
    },
    state: toolCallState,
    requested_at_utc: completedAtUtc,
    actor: { actor_kind: "host", actor_id: "breadboard.ts-kernel-core" },
    visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
  })
  const toolOutcome = buildDriverMediatedToolOutcome({
    sandboxResult,
    callId,
    toolName: options.toolName,
    completedAtUtc,
  })
  const toolRender = buildDriverMediatedToolRender({
    sandboxResult,
    callId,
    toolName: options.toolName,
    command: options.command,
  })
  const assistantText =
    options.assistantText ??
    (sandboxResult.status === "completed"
      ? `${options.toolName} completed successfully.`
      : `${options.toolName} ${sandboxResult.status}.`)
  const turn = executeScriptedToolTurn(request, {
    sessionId: options.sessionId,
    engineFamily: options.engineFamily,
    engineRef: options.engineRef,
    resolvedModel: options.resolvedModel,
    resolvedProviderRoute: options.resolvedProviderRoute,
    executionMode: "driver_mediated_tool_turn",
    activeMode: options.activeMode,
    toolCall,
    toolOutcome,
    toolRender,
    assistantText,
    startedAt: options.startedAt,
  })
  return {
    ...turn,
    executionCapability: plan.capability,
    executionPlacement: plan.placement,
    driverId: plan.driver.driverId,
    sandboxRequest: plan.sandboxRequest,
    sandboxResult,
    sideEffectExpectation: plan.sideEffectExpectation,
    evidenceExpectation: plan.evidenceExpectation,
  }
}

export function executeStaticTextTurn(
  requestInput: RunRequestV1,
  options: StaticTextTurnOptions,
): StaticTextTurnResult {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  const runContext = buildRunContextFromRequest(request, options)
  const occurredAtUtc = options.startedAt ?? "2026-03-08T00:00:00Z"
  const events: KernelEventV2[] = [
    {
      schema_version: "bb.kernel_event.v2",
      event_id: buildKernelEventId(request.request_id, 1),
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 1,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "user", actor_id: "user" },
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      kind: "user_message",
      payload: { text: request.task },
      payload_schema_version: null,
    },
    {
      schema_version: "bb.kernel_event.v2",
      event_id: buildKernelEventId(request.request_id, 2),
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 2,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "agent", actor_id: runContext.engine_ref ?? runContext.engine_family },
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      kind: "assistant_message",
      payload: { text: options.assistantText },
      payload_schema_version: null,
    },
  ]
  const transcript: SessionTranscriptV2 = {
    schema_version: "bb.session_transcript.v2",
    session_id: runContext.session_id,
    run_id: request.request_id,
    event_cursor: 2,
    items: [
      {
        kind: "user_message",
        visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
        content: { text: request.task },
        content_schema_version: null,
        event_id: events[0].event_id,
        provenance: { source: "live", source_ref: "ts_static_text_turn" },
      },
      {
        kind: "assistant_message",
        visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
        content: { text: options.assistantText },
        content_schema_version: null,
        event_id: events[1].event_id,
        provenance: { source: "live", source_ref: "ts_static_text_turn" },
      },
    ],
    metadata: {
      execution_mode: runContext.execution_mode,
      source: "ts-kernel-core",
    },
  }
  return { runContext, events, transcript }
}

export function executeScriptedToolTurn(
  requestInput: RunRequestV1,
  options: ScriptedToolTurnOptions,
): StaticTextTurnResult {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  const toolCall = assertValid<ToolCallV2>("toolCallV2", options.toolCall)
  const toolOutcome = assertValid<ToolExecutionOutcomeV2>("toolExecutionOutcomeV2", options.toolOutcome)
  const toolRender = assertValid<ToolModelRenderV2>("toolModelRenderV2", options.toolRender)
  const runContext = buildRunContextFromRequest(request, {
    sessionId: options.sessionId,
    engineFamily: options.engineFamily,
    engineRef: options.engineRef,
    resolvedModel: options.resolvedModel,
    resolvedProviderRoute: options.resolvedProviderRoute,
    executionMode: options.executionMode ?? "scripted_tool_turn",
    activeMode: options.activeMode,
  })
  const occurredAtUtc = options.startedAt ?? "2026-03-08T00:00:00Z"
  const toolCallEventId = buildKernelEventId(request.request_id, 2)
  const toolResultEventId = buildKernelEventId(request.request_id, 3)
  const events: KernelEventV2[] = [
    {
      schema_version: "bb.kernel_event.v2",
      event_id: buildKernelEventId(request.request_id, 1),
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 1,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "user", actor_id: "user" },
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      kind: "user_message",
      payload: { text: request.task },
      payload_schema_version: null,
    },
    {
      schema_version: "bb.kernel_event.v2",
      event_id: toolCallEventId,
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 2,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "agent", actor_id: runContext.engine_ref ?? runContext.engine_family },
      visibility: { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" },
      kind: "tool_call",
      payload: {
        callId: toolCall.call_id,
        toolName: toolCall.tool_name,
        args: toolCall.args,
        state: toolCall.state,
        metadata: toolCall.metadata ?? {},
      },
      payload_schema_version: null,
      ...(toolCall.task_id ? { task_id: toolCall.task_id } : {}),
      call_id: toolCall.call_id,
    },
    {
      schema_version: "bb.kernel_event.v2",
      event_id: toolResultEventId,
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 3,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "service", actor_id: toolCall.tool_name },
      visibility: { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" },
      kind: "tool_result",
      payload: {
        callId: toolOutcome.call_id,
        terminalState: toolOutcome.terminal_state,
        result: toolOutcome.result,
        error: toolOutcome.error,
        metadata: toolOutcome.metadata ?? {},
      },
      payload_schema_version: null,
      ...(toolCall.task_id ? { task_id: toolCall.task_id } : {}),
      call_id: toolOutcome.call_id,
      caused_by: toolCallEventId,
    },
  ]
  const transcriptItems: SessionTranscriptV2["items"] = [
    {
      kind: "user_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: { text: request.task },
      content_schema_version: null,
      event_id: events[0].event_id,
      provenance: { source: "live", source_ref: "ts_scripted_tool_turn" },
    },
    {
      kind: "tool_result",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      call_id: toolRender.call_id,
      content: { parts: toolRender.parts },
      content_schema_version: null,
      event_id: toolResultEventId,
      provenance: { source: "live", source_ref: "ts_scripted_tool_turn" },
    },
  ]
  if (typeof options.assistantText === "string" && options.assistantText.length > 0) {
    const assistantEvent: KernelEventV2 = {
      schema_version: "bb.kernel_event.v2",
      event_id: buildKernelEventId(request.request_id, 4),
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 4,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "agent", actor_id: runContext.engine_ref ?? runContext.engine_family },
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      kind: "assistant_message",
      payload: { text: options.assistantText },
      payload_schema_version: null,
      caused_by: toolResultEventId,
    }
    events.push(assistantEvent)
    transcriptItems.push({
      kind: "assistant_message",
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      content: { text: options.assistantText },
      content_schema_version: null,
      event_id: assistantEvent.event_id,
      provenance: { source: "live", source_ref: "ts_scripted_tool_turn" },
    })
  }
  const transcript: SessionTranscriptV2 = {
    schema_version: "bb.session_transcript.v2",
    session_id: runContext.session_id,
    run_id: request.request_id,
    event_cursor: events.length,
    items: transcriptItems,
    metadata: {
      execution_mode: runContext.execution_mode,
      source: "ts-kernel-core",
      tool_name: toolCall.tool_name,
    },
  }
  return { runContext, events, transcript }
}

export function executeProviderTextTurn(
  requestInput: RunRequestV1,
  options: ProviderTextTurnOptions,
): ProviderTextTurnResult {
  const request = assertValid<RunRequestV1>("runRequest", requestInput)
  const providerExchange = assertValid<ProviderExchangeV1>("providerExchange", options.providerExchange)
  const runContext = buildRunContextFromRequest(request, {
    sessionId: options.sessionId,
    engineFamily: options.engineFamily,
    engineRef: options.engineRef,
    resolvedModel: providerExchange.request.model,
    resolvedProviderRoute: providerExchange.request.route_id ?? null,
    executionMode: options.executionMode ?? "provider_text_turn",
    activeMode: options.activeMode,
  })
  const occurredAtUtc = options.startedAt ?? "2026-03-08T00:00:00Z"
  const providerEventId = buildKernelEventId(request.request_id, 1)
  const assistantEventId = buildKernelEventId(request.request_id, 2)
  const events: KernelEventV2[] = [
    {
      schema_version: "bb.kernel_event.v2",
      event_id: providerEventId,
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 1,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "provider", actor_id: providerExchange.request.provider_family },
      visibility: { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" },
      kind: "provider_response",
      payload: {
        exchangeId: providerExchange.exchange_id,
        model: providerExchange.request.model,
        routeId: providerExchange.request.route_id ?? null,
        messageCount: providerExchange.response.message_count,
        finishReasons: providerExchange.response.finish_reasons ?? [],
        metadata: providerExchange.response.metadata ?? {},
      },
      payload_schema_version: null,
    },
    {
      schema_version: "bb.kernel_event.v2",
      event_id: assistantEventId,
      run_id: request.request_id,
      session_id: runContext.session_id,
      seq: 2,
      occurred_at_utc: occurredAtUtc,
      actor: { actor_kind: "agent", actor_id: runContext.engine_ref ?? runContext.engine_family },
      visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
      kind: "assistant_message",
      payload: { text: options.assistantText },
      payload_schema_version: null,
      caused_by: providerEventId,
    },
  ]
  const transcript: SessionTranscriptV2 = {
    schema_version: "bb.session_transcript.v2",
    session_id: runContext.session_id,
    run_id: request.request_id,
    event_cursor: 2,
    items: [
      {
        kind: "user_message",
        visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
        content: { text: request.task },
        content_schema_version: null,
        provenance: { source: "live", source_ref: "ts_provider_text_turn:request" },
      },
      {
        kind: "provider_response",
        visibility: { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" },
        content: {
          exchangeId: providerExchange.exchange_id,
          finishReasons: providerExchange.response.finish_reasons ?? [],
          metadata: providerExchange.response.metadata ?? {},
        },
        content_schema_version: null,
        event_id: providerEventId,
        provenance: { source: "live", source_ref: "ts_provider_text_turn" },
      },
      {
        kind: "assistant_message",
        visibility: { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" },
        content: { text: options.assistantText },
        content_schema_version: null,
        event_id: assistantEventId,
        provenance: { source: "live", source_ref: "ts_provider_text_turn" },
      },
    ],
    metadata: {
      execution_mode: runContext.execution_mode,
      source: "ts-kernel-core",
      exchange_id: providerExchange.exchange_id,
      provider_family: providerExchange.request.provider_family,
    },
  }
  return { runContext, events, transcript, providerExchange }
}

export function executeProviderTextContinuationTurn(
  requestInput: RunRequestV1,
  options: ProviderTextContinuationTurnOptions,
): ProviderTextTurnResult {
  const base = executeProviderTextTurn(requestInput, options)
  const existingItems = Array.isArray(options.existingTranscript)
    ? normalizeTranscriptContractItems(options.existingTranscript)
    : normalizeTranscriptContractItems(options.existingTranscript.items)
  const transcript = {
    ...base.transcript,
    items: [...existingItems, ...base.transcript.items],
    metadata: {
      ...base.transcript.metadata,
      continuation_from_existing_transcript: true,
      preserved_prefix_items: existingItems.length,
    },
  }
  return {
    ...base,
    transcript,
    transcriptContinuationPatch: buildTranscriptContinuationPatch(transcript, {
      patchId: `${base.runContext.request_id}:transcript_patch`,
      preservedPrefixItems: existingItems.length,
    }),
  }
}
