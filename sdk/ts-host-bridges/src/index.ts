import type {
  ExecutionCapabilityV1,
  ExecutionPlacementV1,
  KernelEventV1,
  ProviderExchangeV1,
  RunRequestV1,
  SessionTranscriptV1,
  SessionTranscriptV1Item,
  UnsupportedCaseV1,
} from "@breadboard/kernel-contracts"
import {
  buildExecutionCapabilityFromRunRequest,
  buildExecutionPlacement,
  buildUnsupportedCase,
  executeProviderTextContinuationTurn,
  executeProviderTextTurn,
  type ProviderTextTurnResult,
} from "@breadboard/kernel-core"

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

export type OpenClawBridgeInvocation =
  | {
      mode: "breadboard"
      runRequest: RunRequestV1
      executionCapability: ExecutionCapabilityV1
      executionPlacement: ExecutionPlacementV1
      result: OpenClawEmbeddedRunResult
      unsupportedFields: string[]
      providerTurn?: ProviderTextTurnResult
      transcriptPostState?: SessionTranscriptV1
      unsupportedCase?: UnsupportedCaseV1
    }
  | {
      mode: "fallback"
      result: OpenClawEmbeddedRunResult
      unsupportedFields: string[]
      unsupportedCase?: UnsupportedCaseV1
    }

export class UnsupportedOpenClawEmbeddedRunError extends Error {
  readonly unsupportedFields: string[]

  constructor(message: string, unsupportedFields: string[]) {
    super(message)
    this.name = "UnsupportedOpenClawEmbeddedRunError"
    this.unsupportedFields = unsupportedFields
  }
}

export function findUnsupportedOpenClawFields(params: OpenClawEmbeddedRunParams): string[] {
  const unsupported: string[] = []
  if ((params.images?.length ?? 0) > 0) unsupported.push("images")
  if ((params.clientTools?.length ?? 0) > 0) unsupported.push("clientTools")
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

export async function runOpenClawEmbeddedViaBreadboard(
  params: OpenClawEmbeddedRunParams,
  options: {
    executeBreadboard?: OpenClawBreadboardExecutor
    nativeFallback?: OpenClawNativeFallback
    existingTranscript?: SessionTranscriptV1 | Array<Record<string, unknown> | SessionTranscriptV1Item>
  } = {},
): Promise<OpenClawBridgeInvocation> {
  const unsupportedFields = findUnsupportedOpenClawFields(params)
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
  const executionCapability = buildOpenClawExecutionCapability(params)
  const executionPlacement = buildOpenClawExecutionPlacement(executionCapability, params)
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
  const providerTurn = options.existingTranscript
    ? executeProviderTextContinuationTurn(runRequest, {
        sessionId: params.sessionKey ?? params.sessionId,
        executionMode: "openclaw_embedded_bridge",
        activeMode: "embedded",
        providerExchange,
        assistantText: output.assistantText,
        existingTranscript: options.existingTranscript,
      })
    : executeProviderTextTurn(runRequest, {
        sessionId: params.sessionKey ?? params.sessionId,
        executionMode: "openclaw_embedded_bridge",
        activeMode: "embedded",
        providerExchange,
        assistantText: output.assistantText,
      })

  await emitKernelEventsToOpenClawCallbacks(params, providerTurn.events)
  await emitBridgeOutputToOpenClawCallbacks(params, output)

  return {
    mode: "breadboard",
    runRequest,
    executionCapability,
    executionPlacement,
    unsupportedFields,
    providerTurn,
    transcriptPostState: providerTurn.transcript,
    unsupportedCase: undefined,
    result: buildOpenClawResult(params, output),
  }
}
