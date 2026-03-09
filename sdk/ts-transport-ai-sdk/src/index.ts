import type { BackboneTurnResult } from "@breadboard/backbone"

export type AiSdkTransportFrame =
  | {
      type: "start"
      messageId: string
      supportLevel: BackboneTurnResult["supportClaim"]["level"]
      projectionProfile: string
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
      type: "finish"
      messageId: string
      stopReason: string
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
  options: { messageId?: string; stopReason?: string } = {},
): AiSdkTransportFrame[] {
  const messageId = options.messageId ?? result.runContextId
  const frames: AiSdkTransportFrame[] = [
    {
      type: "start",
      messageId,
      supportLevel: result.supportClaim.level,
      projectionProfile: result.projectionProfile.id,
    },
  ]
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
