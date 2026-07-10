import type { ToolLogEntry } from "./types.js"
import type { TranscriptItem } from "./transcriptModel.js"
import type { NormalizedEvent } from "./transcript/normalizedEvent.js"

export const isLogLinkText = (text: string | null | undefined): boolean => {
  const value = String(text ?? "").trim()
  return /^\[log\]\s+(?:file:\/\/)?logging\/\S+/.test(value) || /^(?:file:\/\/)?logging\/\S+/.test(value)
}

export const isLogTranscriptArtifactToolEntry = (entry: Pick<ToolLogEntry, "kind" | "text">): boolean =>
  entry.kind === "status" && isLogLinkText(entry.text)

export const isLogTranscriptArtifactEvent = (event: Pick<NormalizedEvent, "type" | "textDelta">): boolean =>
  event.type === "log_link" && isLogLinkText(event.textDelta)

export const isLogTranscriptArtifactItem = (item: TranscriptItem): boolean =>
  item.kind === "system" && item.systemKind === "log" && isLogLinkText(item.text)
