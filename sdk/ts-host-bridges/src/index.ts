import {
  createBackbone,
  type BackboneSession,
  type SupportClaim,
} from "@breadboard/backbone"
import type { HostKit, HostKitClassification, HostKitInvocation } from "@breadboard/host-kits"
import {
  buildFallbackHostKitInvocation,
  buildSupportedHostKitInvocation,
  createHostKit,
  normalizeHostKitSupportClaim,
} from "@breadboard/host-kits"
import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  KernelEventV1,
  ProviderExchangeV1,
  RunRequestV1,
  SandboxRequestV1,
  SandboxResultV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"
import {
  buildExecutionCapabilityFromRunRequest,
  buildExecutionPlacement,
  buildUnsupportedCase,
  executeDriverMediatedToolTurn,
  type DriverMediatedToolTurnResult,
  type ProviderTextTurnResult,
} from "@breadboard/kernel-core"
import type { LocalCommandExecutor } from "@breadboard/execution-driver-local"
import type { OciCommandExecutor } from "@breadboard/execution-driver-oci"
import type { RemoteExecutionHttpOptions, RemoteSandboxExecutor } from "@breadboard/execution-driver-remote"
import { buildWorkspaceCapabilitySet, createWorkspace } from "@breadboard/workspace"

export type OpenClawClientToolDefinition = {
  type: "function"
  function: {
    name: string
    description?: string
    parameters?: Record<string, unknown>
  }
}

export type OpenClawEmbeddedRunParams = {
  sessionId: string
  sessionKey?: string
  agentId?: string
  messageChannel?: string
  messageProvider?: string
  agentAccountId?: string
  groupId?: string | null
  groupChannel?: string | null
  groupSpace?: string | null
  spawnedBy?: string | null
  sessionFile: string
  workspaceDir: string
  agentDir?: string
  prompt: string
  images?: unknown[]
  clientTools?: OpenClawClientToolDefinition[]
  disableTools?: boolean
  provider?: string
  model?: string
  authProfileId?: string
  authProfileIdSource?: "auto" | "user"
  thinkLevel?: string
  reasoningLevel?: string
  timeoutMs: number
  runId: string
  abortSignal?: AbortSignal
  onPartialReply?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>
  onAssistantMessageStart?: () => void | Promise<void>
  onReasoningStream?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>
  onReasoningEnd?: () => void | Promise<void>
  onToolResult?: (payload: { text?: string; mediaUrls?: string[] }) => void | Promise<void>
  onAgentEvent?: (evt: { stream: string; data: Record<string, unknown> }) => void
  onBlockReply?: (payload: Record<string, unknown>) => void | Promise<void>
  onBlockReplyFlush?: () => void | Promise<void>
  extraSystemPrompt?: string
}

export type OpenClawEmbeddedRunMeta = {
  durationMs: number
  agentMeta?: {
    sessionId: string
    provider: string
    model: string
    usage?: Record<string, unknown>
  }
  stopReason?: string
}

export type OpenClawEmbeddedRunResult = {
  payloads?: Array<{
    text?: string
    mediaUrl?: string
    mediaUrls?: string[]
    replyToId?: string
    isError?: boolean
  }>
  meta: OpenClawEmbeddedRunMeta
  didSendViaMessagingTool?: boolean
  messagingToolSentTexts?: string[]
  messagingToolSentMediaUrls?: string[]
}

export type OpenClawBreadboardBridgeOutput = {
  assistantText: string
  providerExchange?: ProviderExchangeV1
  reasoningDeltas?: string[]
  toolResults?: Array<{ text?: string; mediaUrls?: string[] }>
  agentEvents?: Array<{ stream: string; data: Record<string, unknown> }>
  usage?: Record<string, unknown>
  stopReason?: string
}

export type OpenClawBreadboardExecutor = (
  request: RunRequestV1,
  params: OpenClawEmbeddedRunParams,
) => Promise<OpenClawBreadboardBridgeOutput>

export type OpenClawNativeFallback = (
  params: OpenClawEmbeddedRunParams,
) => Promise<OpenClawEmbeddedRunResult>

export type OpenClawToolSliceOptions = {
  command: string[] | ((tool: OpenClawClientToolDefinition, params: OpenClawEmbeddedRunParams) => string[])
  executeSandbox?: (request: SandboxRequestV1, context: {
    capability: ExecutionCapabilityV1
    placement: ExecutionPlacementV1
    driverId: string
    tool: OpenClawClientToolDefinition
    params: OpenClawEmbeddedRunParams
  }) => Promise<SandboxResultV1>
  imageRef?: string | ((tool: OpenClawClientToolDefinition, params: OpenClawEmbeddedRunParams) => string | null)
  isolationClass?: ExecutionCapabilityV1["isolation_class"]
  securityTier?: ExecutionCapabilityV1["security_tier"]
  allowRunPrograms?: string[]
  allowNetHosts?: string[]
  localCommandExecutor?: LocalCommandExecutor
  ociCommandExecutor?: OciCommandExecutor
  ociRuntimeCommand?: string
  ociWorkspaceMountTarget?: string
  remoteExecutor?: RemoteSandboxExecutor
  remoteHttp?: RemoteExecutionHttpOptions
  assistantText?: string | ((result: DriverMediatedToolTurnResult, tool: OpenClawClientToolDefinition) => string)
}

export type OpenClawBridgeInvocation =
  | {
      mode: "breadboard"
      runRequest: RunRequestV1
      executionCapability: ExecutionCapabilityV1
      executionPlacement: ExecutionPlacementV1
      result: OpenClawEmbeddedRunResult
      unsupportedFields: string[]
      providerTurn?: ProviderTextTurnResult
      driverTurn?: DriverMediatedToolTurnResult
      transcriptPostState?: SessionTranscriptV1
      unsupportedCase?: UnsupportedCaseV1
    }
  | {
      mode: "fallback"
      result: OpenClawEmbeddedRunResult
      unsupportedFields: string[]
      unsupportedCase?: UnsupportedCaseV1
    }

export interface OpenClawHostKitOptions {
  readonly nativeFallback?: OpenClawNativeFallback
  readonly executeBreadboard?: OpenClawBreadboardExecutor
  readonly existingTranscript?: SessionTranscriptV1 | Array<Record<string, unknown> | SessionTranscriptV1Item>
  readonly toolSlice?: OpenClawToolSliceOptions
}

export class UnsupportedOpenClawEmbeddedRunError extends Error {
  readonly unsupportedFields: string[]

  constructor(message: string, unsupportedFields: string[]) {
    super(message)
    this.name = "UnsupportedOpenClawEmbeddedRunError"
    this.unsupportedFields = unsupportedFields
  }
}

export function buildOpenClawSupportClaim(
  params: OpenClawEmbeddedRunParams,
  options: { toolSlice?: OpenClawToolSliceOptions } = {},
): SupportClaim {
  const request = buildOpenClawBreadboardRunRequest(params)
  const session = buildOpenClawBackboneSession(params)
  const unsupportedFields = findUnsupportedOpenClawFields(params, {
    allowSingleFunctionToolSlice: Boolean(options.toolSlice),
  })
  if ((params.clientTools?.length ?? 0) > 0) {
    return normalizeHostKitSupportClaim(
      session.classifyToolTurn({
        request,
        toolName: params.clientTools?.[0]?.function.name ?? "unknown_tool",
        command: [],
        driverIdHint: options.toolSlice?.remoteExecutor || options.toolSlice?.remoteHttp
          ? "remote"
          : options.toolSlice?.imageRef
            ? "oci"
            : "trusted_local",
      }),
      {
        unsupportedFields,
      },
    )
  }
  return normalizeHostKitSupportClaim(
    session.classifyProviderTurn({
      request,
      providerExchange: buildOpenClawProviderExchange(params, {
        assistantText: "",
        usage: undefined,
      }),
      assistantText: "",
    }),
    {
      unsupportedFields,
      summary:
        unsupportedFields.length === 0
          ? "OpenClaw embedded request is supported by the current Host Kit slice."
          : `OpenClaw embedded request requires fallback for: ${unsupportedFields.join(", ")}`,
    },
  )
}

function buildOpenClawWorkspace(params: OpenClawEmbeddedRunParams) {
  return createWorkspace({
    workspaceId: `openclaw:${params.sessionId}`,
    rootDir: params.workspaceDir,
    capabilitySet: buildWorkspaceCapabilitySet({
      canRunSandboxedLocal: true,
      canRunRemoteIsolated: true,
    }),
    defaultExecutionProfileId: "trusted_local",
  })
}

function buildOpenClawBackboneSession(
  params: OpenClawEmbeddedRunParams,
  projectionProfileId: "host_callbacks" | "raw_kernel_events" | "ai_sdk_transport" = "host_callbacks",
): BackboneSession {
  const workspace = buildOpenClawWorkspace(params)
  const backbone = createBackbone({
    workspace,
    defaultProjectionProfileId: projectionProfileId,
  })
  return backbone.openSession({
    sessionId: params.sessionKey ?? params.sessionId,
    workspaceRoot: params.workspaceDir,
    requestedModel: params.model ?? null,
    requestedProvider: params.provider ?? null,
    projectionProfileId,
  })
}

export function findUnsupportedOpenClawFields(
  params: OpenClawEmbeddedRunParams,
  options: { allowSingleFunctionToolSlice?: boolean } = {},
): string[] {
  const unsupported: string[] = []
  if ((params.images?.length ?? 0) > 0) unsupported.push("images")
  if ((params.clientTools?.length ?? 0) > 0) {
    const supportedToolSlice =
      options.allowSingleFunctionToolSlice === true &&
      params.clientTools?.length === 1 &&
      params.clientTools.every((tool) => tool.type === "function")
    if (!supportedToolSlice) unsupported.push("clientTools")
  }
  if (params.disableTools) unsupported.push("disableTools")
  if (typeof params.onBlockReply === "function") unsupported.push("onBlockReply")
  if (typeof params.onBlockReplyFlush === "function") unsupported.push("onBlockReplyFlush")
  if (params.messageChannel) unsupported.push("messageChannel")
  if (params.messageProvider) unsupported.push("messageProvider")
  if (params.groupId != null) unsupported.push("groupId")
  if (params.groupChannel != null) unsupported.push("groupChannel")
  if (params.groupSpace != null) unsupported.push("groupSpace")
  if (params.spawnedBy != null) unsupported.push("spawnedBy")
  return unsupported
}

export function buildOpenClawBreadboardRunRequest(params: OpenClawEmbeddedRunParams): RunRequestV1 {
  return {
    schema_version: "bb.run_request.v1",
    request_id: params.runId,
    entry_mode: "openclaw_embedded",
    task: params.prompt,
    workspace_root: params.workspaceDir,
    requested_model: params.model ?? null,
    requested_features: {
      think_level: params.thinkLevel ?? null,
      reasoning_level: params.reasoningLevel ?? null,
    },
    metadata: {
      host: "openclaw",
      session_id: params.sessionId,
      session_key: params.sessionKey ?? null,
      session_file: params.sessionFile,
      agent_id: params.agentId ?? null,
      agent_dir: params.agentDir ?? null,
      auth_profile_id: params.authProfileId ?? null,
      auth_profile_source: params.authProfileIdSource ?? null,
      timeout_ms: params.timeoutMs,
      extra_system_prompt: params.extraSystemPrompt ?? null,
    },
  }
}

export function buildOpenClawExecutionCapability(params: OpenClawEmbeddedRunParams): ExecutionCapabilityV1 {
  return buildExecutionCapabilityFromRunRequest(buildOpenClawBreadboardRunRequest(params), {
    capabilityId: `openclaw:${params.runId}:embedded`,
    securityTier: "trusted_dev",
    isolationClass: "none",
    evidenceMode: "replay_strict",
    allowReadPaths: [params.workspaceDir],
    allowWritePaths: [params.workspaceDir],
    allowRunPrograms: [],
    allowNetHosts: [],
    ttyMode: "optional",
  })
}

export function buildOpenClawExecutionPlacement(
  capability: ExecutionCapabilityV1,
  params: OpenClawEmbeddedRunParams,
): ExecutionPlacementV1 {
  return buildExecutionPlacement(capability, {
    placementId: `openclaw:${params.runId}:inline_ts`,
    placementClass: "inline_ts",
    runtimeId: "breadboard.ts-host-bridges/openclaw",
    metadata: {
      host: "openclaw",
      mode: "embedded",
    },
  })
}

export function buildOpenClawUnsupportedCase(
  reasonCode: string,
  summary: string,
  options: {
    params: OpenClawEmbeddedRunParams
    fallbackAllowed?: boolean
    fallbackTaken?: boolean
    unavailablePlacement?: string | null
    metadata?: Record<string, unknown>
  },
): UnsupportedCaseV1 {
  return buildUnsupportedCase(reasonCode, summary, {
    contractFamily: "bb.unsupported_case.v1",
    fallbackAllowed: options.fallbackAllowed,
    fallbackTaken: options.fallbackTaken,
    requiredCapabilityId: `openclaw:${options.params.runId}:embedded`,
    unavailablePlacement: options.unavailablePlacement ?? null,
    metadata: {
      host: "openclaw",
      ...options.metadata,
    },
  })
}

export function buildOpenClawProviderExchange(
  params: OpenClawEmbeddedRunParams,
  output: OpenClawBreadboardBridgeOutput,
): ProviderExchangeV1 {
  const providerFamily = params.provider ?? "breadboard-bridge"
  return (
    output.providerExchange ?? {
      schema_version: "bb.provider_exchange.v1",
      exchange_id: `${params.runId}:provider_exchange`,
      request: {
        provider_family: providerFamily,
        runtime_id: "openclaw_embedded_bridge",
        route_id: params.authProfileId ?? null,
        model: params.model ?? "unspecified",
        stream: true,
        message_count: 1,
        tool_count: 0,
        metadata: {
          host: "openclaw",
          session_id: params.sessionId,
          auth_profile_source: params.authProfileIdSource ?? null,
        },
      },
      response: {
        message_count: output.assistantText.length > 0 ? 1 : 0,
        finish_reasons: [output.stopReason ?? "stop"],
        usage: output.usage ?? null,
        metadata: {
          provider_family: providerFamily,
          runtime_id: "openclaw_embedded_bridge",
        },
      },
    }
  )
}

async function emitKernelEventsToOpenClawCallbacks(
  params: OpenClawEmbeddedRunParams,
  events: KernelEventV1[],
): Promise<void> {
  let assistantStarted = false
  for (const event of events) {
    if (event.kind === "assistant_message" && event.visibility === "model") {
      if (!assistantStarted) {
        assistantStarted = true
        await params.onAssistantMessageStart?.()
      }
      const payload = event.payload as { text?: string }
      if (payload.text) {
        await params.onPartialReply?.({ text: payload.text })
      }
      continue
    }
    if (event.kind === "provider_response") {
      params.onAgentEvent?.({
        stream: "provider_response",
        data: event.payload as Record<string, unknown>,
      })
    }
  }
}

async function emitBridgeOutputToOpenClawCallbacks(
  params: OpenClawEmbeddedRunParams,
  output: OpenClawBreadboardBridgeOutput,
): Promise<void> {
  if (output.reasoningDeltas?.length) {
    for (const delta of output.reasoningDeltas) {
      await params.onReasoningStream?.({ text: delta })
    }
    await params.onReasoningEnd?.()
  }
  if (output.toolResults?.length) {
    for (const item of output.toolResults) {
      await params.onToolResult?.(item)
    }
  }
  if (output.agentEvents?.length) {
    for (const item of output.agentEvents) {
      params.onAgentEvent?.(item)
    }
  }
}

async function emitDriverTurnToOpenClawCallbacks(
  params: OpenClawEmbeddedRunParams,
  turn: DriverMediatedToolTurnResult,
): Promise<void> {
  let assistantStarted = false
  for (const item of turn.transcript.items) {
    if (item.kind === "tool_result") {
      const parts = (item.content as { parts?: Array<{ preview?: string }> }).parts ?? []
      const text = parts
        .map((part) => part.preview)
        .filter((value): value is string => typeof value === "string" && value.length > 0)
        .join("\n")
      if (text.length > 0) {
        await params.onToolResult?.({ text, mediaUrls: [] })
      }
      continue
    }
    if (item.kind === "assistant_message" && item.visibility === "model") {
      const text = (item.content as { text?: string }).text
      if (typeof text === "string" && text.length > 0) {
        if (!assistantStarted) {
          assistantStarted = true
          await params.onAssistantMessageStart?.()
        }
        await params.onPartialReply?.({ text })
      }
    }
  }
}

function buildOpenClawResult(
  params: OpenClawEmbeddedRunParams,
  output: OpenClawBreadboardBridgeOutput,
): OpenClawEmbeddedRunResult {
  return {
    payloads: output.assistantText
      ? [
          {
            text: output.assistantText,
            mediaUrls: [],
          },
        ]
      : [],
    meta: {
      durationMs: 0,
      agentMeta: {
        sessionId: params.sessionId,
        provider: params.provider ?? "breadboard-bridge",
        model: params.model ?? "unspecified",
        usage: output.usage,
      },
      stopReason: output.stopReason ?? "completed",
    },
  }
}

function buildOpenClawResultFromDriverTurn(
  params: OpenClawEmbeddedRunParams,
  turn: DriverMediatedToolTurnResult,
): OpenClawEmbeddedRunResult {
  const assistantText = ((turn.transcript.items.at(-1)?.content ?? {}) as { text?: string }).text ?? ""
  return {
    payloads: assistantText
      ? [
          {
            text: assistantText,
            mediaUrls: [],
          },
        ]
      : [],
    meta: {
      durationMs: 0,
      agentMeta: {
        sessionId: params.sessionId,
        provider: params.provider ?? "breadboard-driver",
        model: params.model ?? "unspecified",
        usage: turn.sandboxResult.usage ?? undefined,
      },
      stopReason: turn.sandboxResult.status,
    },
  }
}

export async function runOpenClawEmbeddedViaBreadboard(
  params: OpenClawEmbeddedRunParams,
  options: {
    executeBreadboard?: OpenClawBreadboardExecutor
    nativeFallback?: OpenClawNativeFallback
    existingTranscript?: SessionTranscriptV1 | Array<Record<string, unknown> | SessionTranscriptV1Item>
    toolSlice?: OpenClawToolSliceOptions
  } = {},
): Promise<OpenClawBridgeInvocation> {
  const toolSliceRequested = (params.clientTools?.length ?? 0) > 0
  const unsupportedFields = findUnsupportedOpenClawFields(params, {
    allowSingleFunctionToolSlice: Boolean(options.toolSlice),
  })
  if (unsupportedFields.length > 0) {
    const unsupportedCase = buildOpenClawUnsupportedCase(
      "unsupported_openclaw_embedded_fields",
      `Unsupported OpenClaw embedded fields: ${unsupportedFields.join(", ")}`,
      {
        params,
        fallbackAllowed: Boolean(options.nativeFallback),
        fallbackTaken: Boolean(options.nativeFallback),
        metadata: { unsupported_fields: unsupportedFields },
      },
    )
    if (options.nativeFallback) {
      return {
        mode: "fallback",
        result: await options.nativeFallback(params),
        unsupportedFields,
        unsupportedCase,
      }
    }
    throw new UnsupportedOpenClawEmbeddedRunError(
      `Unsupported OpenClaw embedded BreadBoard slice: ${unsupportedFields.join(", ")}`,
      unsupportedFields,
    )
  }

  const runRequest = buildOpenClawBreadboardRunRequest(params)
  if (toolSliceRequested && options.toolSlice) {
    const tool = params.clientTools?.[0]
    if (!tool) {
      throw new UnsupportedOpenClawEmbeddedRunError("Tool-bearing OpenClaw slice requires one function tool", [
        "clientTools",
      ])
    }
    const command =
      typeof options.toolSlice.command === "function"
        ? options.toolSlice.command(tool, params)
        : options.toolSlice.command
    const imageRef =
      typeof options.toolSlice.imageRef === "function"
        ? options.toolSlice.imageRef(tool, params)
        : options.toolSlice.imageRef ?? null
    const driverTurn = await executeDriverMediatedToolTurn(runRequest, {
      sessionId: params.sessionKey ?? params.sessionId,
      activeMode: "embedded",
      toolName: tool.function.name,
      toolDescription: tool.function.description ?? null,
      command,
      workspaceRef: params.workspaceDir,
      imageRef,
      isolationClass: options.toolSlice.isolationClass ?? "process",
      securityTier: options.toolSlice.securityTier ?? "trusted_dev",
      allowRunPrograms: options.toolSlice.allowRunPrograms ?? [command[0]].filter(Boolean),
      allowNetHosts: options.toolSlice.allowNetHosts ?? [],
      driverIdHint: options.toolSlice.remoteExecutor || options.toolSlice.remoteHttp ? "remote" : imageRef ? "oci" : undefined,
      assistantText: null,
      executeSandbox: options.toolSlice.executeSandbox
        ? (request, context) =>
            options.toolSlice!.executeSandbox!(request, {
              ...context,
              tool,
              params,
            })
        : undefined,
      localCommandExecutor: options.toolSlice.localCommandExecutor,
      ociCommandExecutor: options.toolSlice.ociCommandExecutor,
      ociRuntimeCommand: options.toolSlice.ociRuntimeCommand,
      ociWorkspaceMountTarget: options.toolSlice.ociWorkspaceMountTarget,
      remoteExecutor: options.toolSlice.remoteExecutor,
      remoteHttp: options.toolSlice.remoteHttp,
    })
    if (typeof options.toolSlice.assistantText === "function") {
      const assistantText = options.toolSlice.assistantText(driverTurn, tool)
      if (assistantText.length > 0) {
        const assistantItem = driverTurn.transcript.items.at(-1)
        if (assistantItem?.kind === "assistant_message") {
          assistantItem.content = { text: assistantText }
        }
      }
    } else if (typeof options.toolSlice.assistantText === "string" && options.toolSlice.assistantText.length > 0) {
      const assistantItem = driverTurn.transcript.items.at(-1)
      if (assistantItem?.kind === "assistant_message") {
        assistantItem.content = { text: options.toolSlice.assistantText }
      }
    }

    await emitDriverTurnToOpenClawCallbacks(params, driverTurn)
    return {
      mode: "breadboard",
      runRequest,
      executionCapability: driverTurn.executionCapability,
      executionPlacement: driverTurn.executionPlacement,
      unsupportedFields,
      providerTurn: undefined,
      driverTurn,
      transcriptPostState: driverTurn.transcript,
      unsupportedCase: undefined,
      result: buildOpenClawResultFromDriverTurn(params, driverTurn),
    }
  }

  const executionCapability = buildOpenClawExecutionCapability(params)
  const executionPlacement = buildOpenClawExecutionPlacement(executionCapability, params)
  const session = buildOpenClawBackboneSession(params)
  const executeBreadboard = options.executeBreadboard
  if (!executeBreadboard) {
    const unsupportedCase = buildOpenClawUnsupportedCase(
      "missing_breadboard_executor",
      "No BreadBoard executor was provided for the OpenClaw embedded bridge",
      {
        params,
        fallbackAllowed: Boolean(options.nativeFallback),
        fallbackTaken: Boolean(options.nativeFallback),
      },
    )
    if (options.nativeFallback) {
      return {
        mode: "fallback",
        result: await options.nativeFallback(params),
        unsupportedFields: ["executeBreadboard"],
        unsupportedCase,
      }
    }
    throw new UnsupportedOpenClawEmbeddedRunError(
      "No BreadBoard executor was provided for the OpenClaw embedded bridge",
      ["executeBreadboard"],
    )
  }

  const output = await executeBreadboard(runRequest, params)
  const providerExchange = buildOpenClawProviderExchange(params, output)
  const providerTurn = await session.runProviderTurn({
    request: runRequest,
    providerExchange,
    assistantText: output.assistantText,
    existingTranscript: options.existingTranscript,
  })

  await emitKernelEventsToOpenClawCallbacks(params, [...providerTurn.events])
  await emitBridgeOutputToOpenClawCallbacks(params, output)

  return {
    mode: "breadboard",
    runRequest,
    executionCapability,
    executionPlacement,
    unsupportedFields,
    providerTurn: providerTurn.providerTurn,
    transcriptPostState: providerTurn.transcript,
    unsupportedCase: undefined,
    result: buildOpenClawResult(params, output),
  }
}

export function createOpenClawHostKit(
  options: OpenClawHostKitOptions = {},
): HostKit<OpenClawEmbeddedRunParams, OpenClawEmbeddedRunResult, OpenClawBridgeInvocation> {
  return createHostKit({
    id: "openclaw.embedded.v1",
    classify(
      request: OpenClawEmbeddedRunParams,
    ): HostKitClassification<OpenClawEmbeddedRunParams> {
      const supportClaim = buildOpenClawSupportClaim(request, { toolSlice: options.toolSlice })
      return {
        mode: supportClaim.level === "supported" ? "supported" : "fallback",
        request,
        unsupportedFields: [...supportClaim.unsupportedFields],
        supportClaim,
      }
    },
    async invoke(
      request: OpenClawEmbeddedRunParams,
    ): Promise<HostKitInvocation<OpenClawEmbeddedRunResult, OpenClawBridgeInvocation>> {
      const baseSupportClaim = buildOpenClawSupportClaim(request, { toolSlice: options.toolSlice })
      const invocation = await runOpenClawEmbeddedViaBreadboard(request, {
        nativeFallback: options.nativeFallback,
        executeBreadboard: options.executeBreadboard,
        existingTranscript: options.existingTranscript,
        toolSlice: options.toolSlice,
      })
      if (invocation.mode === "breadboard") {
        return buildSupportedHostKitInvocation({
          result: invocation.result,
          invocation,
          supportClaim: baseSupportClaim,
        })
      }
      return buildFallbackHostKitInvocation({
        result: invocation.result,
        invocation,
        supportClaim: normalizeHostKitSupportClaim(baseSupportClaim, {
          level: "fallback",
          summary: invocation.unsupportedCase?.summary ?? baseSupportClaim.summary,
          fallbackAvailable: true,
          unsupportedFields: invocation.unsupportedFields,
          confidence: "medium",
        }),
      })
    },
  })
}
