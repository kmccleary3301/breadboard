import {
  assertValid,
  type ExecutionCapabilityV1,
  type ExecutionPlacementV1,
  type KernelEventV1,
  type ProviderExchangeV1,
  type RunRequestV1,
  type SandboxRequestV1,
  type SandboxResultV1,
  type SessionTranscriptV1,
  type SessionTranscriptV1Item,
  type ToolCallV1,
  type ToolExecutionOutcomeV1,
  type ToolModelRenderV1,
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
}): ToolExecutionOutcomeV1 {
  const outcome: Record<string, unknown> = {
    schemaVersion: "bb.tool_execution_outcome.v1",
    callId: input.callId,
    terminalState: input.sandboxResult.status === "completed" ? "completed" : "errored",
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
  return assertValid<ToolExecutionOutcomeV1>("toolExecutionOutcome", outcome)
}

function buildDriverMediatedToolRender(input: {
  sandboxResult: SandboxResultV1
  callId: string
  toolName: string
  command: string[]
}): ToolModelRenderV1 {
  const preview =
    input.sandboxResult.status === "completed"
      ? `${input.toolName} completed via sandbox (${input.command.join(" ")})`
      : `${input.toolName} ${input.sandboxResult.status} via sandbox`
  return assertValid<ToolModelRenderV1>("toolModelRender", {
    schemaVersion: "bb.tool_model_render.v1",
    callId: input.callId,
    visibility: "model",
    parts: [
      {
        tool: input.toolName,
        preview,
        status: input.sandboxResult.status === "completed" ? "ok" : "error",
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
        : "breadboard.ts-execution-driver-oci",
  })
  const drivers: ExecutionDriverV1[] = options.driverIdHint === "oci"
    ? [ociExecutionDriver, trustedLocalExecutionDriver]
    : [trustedLocalExecutionDriver, ociExecutionDriver]
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
  const toolCall = assertValid<ToolCallV1>("toolCall", {
    schemaVersion: "bb.tool_call.v1",
    callId,
    toolName: options.toolName,
    args: {
      command: options.command,
      image_ref: options.imageRef ?? null,
    },
    state: sandboxResult.status === "completed" ? "completed" : "errored",
  })
  const toolOutcome = buildDriverMediatedToolOutcome({
    sandboxResult,
    callId,
    toolName: options.toolName,
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
  const ts = options.startedAt ?? "2026-03-08T00:00:00Z"
  const events: KernelEventV1[] = [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 1),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 1,
      ts,
      actor: "human",
      visibility: "model",
      kind: "user_message",
      payload: { text: request.task },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 2),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 2,
      ts,
      actor: "engine",
      visibility: "model",
      kind: "assistant_message",
      payload: { text: options.assistantText },
    },
  ]
  const transcript: SessionTranscriptV1 = {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: runContext.session_id,
    runId: request.request_id,
    eventCursor: 2,
    items: [
      {
        kind: "user_message",
        visibility: "model",
        content: { text: request.task },
        provenance: { source: "ts_static_text_turn", eventId: events[0].eventId },
      },
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: options.assistantText },
        provenance: { source: "ts_static_text_turn", eventId: events[1].eventId },
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
  const toolCall = assertValid<ToolCallV1>("toolCall", options.toolCall)
  const toolOutcome = assertValid<ToolExecutionOutcomeV1>("toolExecutionOutcome", options.toolOutcome)
  const toolRender = assertValid<ToolModelRenderV1>("toolModelRender", options.toolRender)
  const runContext = buildRunContextFromRequest(request, {
    sessionId: options.sessionId,
    engineFamily: options.engineFamily,
    engineRef: options.engineRef,
    resolvedModel: options.resolvedModel,
    resolvedProviderRoute: options.resolvedProviderRoute,
    executionMode: options.executionMode ?? "scripted_tool_turn",
    activeMode: options.activeMode,
  })
  const ts = options.startedAt ?? "2026-03-08T00:00:00Z"
  const events: KernelEventV1[] = [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 1),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 1,
      ts,
      actor: "human",
      visibility: "model",
      kind: "user_message",
      payload: { text: request.task },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 2),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 2,
      ts,
      actor: "engine",
      visibility: "host",
      kind: "tool_call",
      payload: {
        callId: toolCall.callId,
        toolName: toolCall.toolName,
        args: toolCall.args,
        state: toolCall.state,
        metadata: toolCall.metadata ?? {},
      },
      taskId: toolCall.taskId,
      callId: toolCall.callId,
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 3),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 3,
      ts,
      actor: "tool",
      visibility: "host",
      kind: "tool_result",
      payload: {
        callId: toolOutcome.callId,
        terminalState: toolOutcome.terminalState,
        result: toolOutcome.result,
        error: toolOutcome.error,
        metadata: toolOutcome.metadata ?? {},
      },
      taskId: toolCall.taskId,
      callId: toolOutcome.callId,
      causedBy: buildKernelEventId(request.request_id, 2),
    },
  ]
  const transcriptItems: SessionTranscriptV1Item[] = [
    {
      kind: "user_message",
      visibility: "model",
      content: { text: request.task },
      provenance: { source: "ts_scripted_tool_turn", eventId: events[0].eventId },
    },
    {
      kind: "tool_result",
      visibility: toolRender.visibility ?? "model",
      callId: toolRender.callId,
      content: { parts: toolRender.parts },
      provenance: { source: "ts_scripted_tool_turn", eventId: events[2].eventId },
    },
  ]
  if (typeof options.assistantText === "string" && options.assistantText.length > 0) {
    const assistantEvent: KernelEventV1 = {
      schemaVersion: "bb.kernel_event.v1",
      eventId: buildKernelEventId(request.request_id, 4),
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 4,
      ts,
      actor: "engine",
      visibility: "model",
      kind: "assistant_message",
      payload: { text: options.assistantText },
      causedBy: buildKernelEventId(request.request_id, 3),
    }
    events.push(assistantEvent)
    transcriptItems.push({
      kind: "assistant_message",
      visibility: "model",
      content: { text: options.assistantText },
      provenance: { source: "ts_scripted_tool_turn", eventId: assistantEvent.eventId },
    })
  }
  const transcript: SessionTranscriptV1 = {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: runContext.session_id,
    runId: request.request_id,
    eventCursor: events.length,
    items: transcriptItems,
    metadata: {
      execution_mode: runContext.execution_mode,
      source: "ts-kernel-core",
      tool_name: toolCall.toolName,
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
  const ts = options.startedAt ?? "2026-03-08T00:00:00Z"
  const providerEventId = buildKernelEventId(request.request_id, 1)
  const assistantEventId = buildKernelEventId(request.request_id, 2)
  const events: KernelEventV1[] = [
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: providerEventId,
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 1,
      ts,
      actor: "provider",
      visibility: "host",
      kind: "provider_response",
      payload: {
        exchangeId: providerExchange.exchange_id,
        model: providerExchange.request.model,
        routeId: providerExchange.request.route_id ?? null,
        messageCount: providerExchange.response.message_count,
        finishReasons: providerExchange.response.finish_reasons ?? [],
        metadata: providerExchange.response.metadata ?? {},
      },
    },
    {
      schemaVersion: "bb.kernel_event.v1",
      eventId: assistantEventId,
      runId: request.request_id,
      sessionId: runContext.session_id,
      seq: 2,
      ts,
      actor: "engine",
      visibility: "model",
      kind: "assistant_message",
      payload: { text: options.assistantText },
      causedBy: providerEventId,
    },
  ]
  const transcript: SessionTranscriptV1 = {
    schemaVersion: "bb.session_transcript.v1",
    sessionId: runContext.session_id,
    runId: request.request_id,
    eventCursor: 2,
    items: [
      {
        kind: "user_message",
        visibility: "model",
        content: { text: request.task },
        provenance: { source: "ts_provider_text_turn", kind: "request" },
      },
      {
        kind: "provider_response",
        visibility: "host",
        content: {
          exchangeId: providerExchange.exchange_id,
          finishReasons: providerExchange.response.finish_reasons ?? [],
          metadata: providerExchange.response.metadata ?? {},
        },
        provenance: { source: "ts_provider_text_turn", eventId: providerEventId },
      },
      {
        kind: "assistant_message",
        visibility: "model",
        content: { text: options.assistantText },
        provenance: { source: "ts_provider_text_turn", eventId: assistantEventId },
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
