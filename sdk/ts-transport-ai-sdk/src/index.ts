import type { BackboneTurnResult } from "@breadboard/backbone"

export type AiSdkTransportFrame =
  | {
      type: "start"
      messageId: string
      supportLevel: BackboneTurnResult["supportClaim"]["level"]
      projectionProfile: string
      executionProfileId: string
    }
  | {
      type: "resume"
      messageId: string
      patchId: string
      postStateDigest: string
    }
  | {
      type: "text-delta"
      messageId: string
      text: string
    }
  | {
      type: "tool"
      messageId: string
      preview: string
    }
  | {
      type: "continuation-patch"
      messageId: string
      patchId: string
      appendedMessageCount: number
      appendedToolEventCount: number
      postStateDigest: string
      lossinessFlags: string[]
    }
  | {
      type: "finish"
      messageId: string
      stopReason: string
    }

export interface AiSdkTransportState {
  readonly lastMessageId: string
  readonly transcriptDigest: string | null
  readonly turnCount: number
}

function extractAssistantText(result: BackboneTurnResult): string {
  const lastAssistant = [...result.transcript.items]
    .reverse()
    .find((item) => item.kind === "assistant_message" && item.visibility === "model")
  return ((lastAssistant?.content ?? {}) as { text?: string }).text ?? ""
}

function extractToolPreview(result: BackboneTurnResult): string | null {
  const toolResult = [...result.transcript.items]
    .reverse()
    .find((item) => item.kind === "tool_result")
  if (!toolResult) return null
  const parts = ((toolResult.content ?? {}) as { parts?: Array<{ preview?: string }> }).parts ?? []
  const preview = parts
    .map((part) => part.preview)
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .join("\n")
  return preview.length > 0 ? preview : null
}

export function projectBackboneTurnToAiSdkFrames(
  result: BackboneTurnResult,
  options: { messageId?: string; stopReason?: string; resumeFrom?: AiSdkTransportState | null } = {},
): AiSdkTransportFrame[] {
  const messageId = options.messageId ?? result.runContextId
  const frames: AiSdkTransportFrame[] = []
  const continuationPatch = result.providerTurn?.transcriptContinuationPatch
  if (options.resumeFrom && continuationPatch) {
    frames.push({
      type: "resume",
      messageId,
      patchId: continuationPatch.patch_id,
      postStateDigest: options.resumeFrom.transcriptDigest ?? continuationPatch.post_state_digest,
    })
  }
  frames.push(
    {
      type: "start",
      messageId,
      supportLevel: result.supportClaim.level,
      projectionProfile: result.projectionProfile.id,
      executionProfileId: result.supportClaim.executionProfileId,
    },
  )
  if (continuationPatch) {
    frames.push({
      type: "continuation-patch",
      messageId,
      patchId: continuationPatch.patch_id,
      appendedMessageCount: continuationPatch.appended_messages.length,
      appendedToolEventCount: continuationPatch.appended_tool_events?.length ?? 0,
      postStateDigest: continuationPatch.post_state_digest,
      lossinessFlags: [...(continuationPatch.lossiness_flags ?? [])],
    })
  }
  const text = extractAssistantText(result)
  if (text.length > 0) {
    frames.push({ type: "text-delta", messageId, text })
  }
  const toolPreview = extractToolPreview(result)
  if (toolPreview) {
    frames.push({ type: "tool", messageId, preview: toolPreview })
  }
  frames.push({ type: "finish", messageId, stopReason: options.stopReason ?? "stop" })
  return frames
}

export function deriveAiSdkTransportState(
  result: BackboneTurnResult,
  options: { messageId?: string; previousState?: AiSdkTransportState | null } = {},
): AiSdkTransportState {
  const messageId = options.messageId ?? result.runContextId
  const continuationPatch = result.providerTurn?.transcriptContinuationPatch
  return {
    lastMessageId: messageId,
    transcriptDigest: continuationPatch?.post_state_digest ?? null,
    turnCount: (options.previousState?.turnCount ?? 0) + 1,
  }
}

export interface AiSdkTransportProjectTurnResult {
  readonly frames: AiSdkTransportFrame[]
  readonly state: AiSdkTransportState
}

export interface AiSdkTransportSession {
  readonly state: AiSdkTransportState | null
  projectTurn(
    result: BackboneTurnResult,
    options?: { messageId?: string; stopReason?: string },
  ): AiSdkTransportProjectTurnResult
  projectResumedTurn(
    result: BackboneTurnResult,
    options?: { messageId?: string; stopReason?: string },
  ): AiSdkTransportProjectTurnResult
}

export function projectBackboneTurnToAiSdkTransport(
  result: BackboneTurnResult,
  options: { messageId?: string; stopReason?: string; previousState?: AiSdkTransportState | null } = {},
): AiSdkTransportProjectTurnResult {
  const frames = projectBackboneTurnToAiSdkFrames(result, {
    messageId: options.messageId,
    stopReason: options.stopReason,
    resumeFrom: options.previousState,
  })
  return {
    frames,
    state: deriveAiSdkTransportState(result, {
      messageId: options.messageId,
      previousState: options.previousState,
    }),
  }
}

/**
 * Create a resumable transport-session helper for thin hosts that want to project Backbone turns
 * onto AI SDK-style frames without making transport state the source of kernel truth.
 */
export function createAiSdkTransportSession(
  initialState: AiSdkTransportState | null = null,
): AiSdkTransportSession {
  let state = initialState
  return {
    get state(): AiSdkTransportState | null {
      return state
    },
    projectTurn(
      result: BackboneTurnResult,
      options: { messageId?: string; stopReason?: string } = {},
    ): AiSdkTransportProjectTurnResult {
      const projection = projectBackboneTurnToAiSdkTransport(result, {
        messageId: options.messageId,
        stopReason: options.stopReason,
      })
      state = projection.state
      return projection
    },
    projectResumedTurn(
      result: BackboneTurnResult,
      options: { messageId?: string; stopReason?: string } = {},
    ): AiSdkTransportProjectTurnResult {
      const projection = projectBackboneTurnToAiSdkTransport(result, {
        messageId: options.messageId,
        stopReason: options.stopReason,
        previousState: state,
      })
      state = projection.state
      return projection
    },
  }
}
