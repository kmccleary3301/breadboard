import type { TranscriptItem } from "../../../transcriptModel.js"

export interface TranscriptMemoryBoundsOptions {
  readonly enabled: boolean
  readonly maxItems: number
  readonly maxBytes: number
  readonly includeCompactionMarker: boolean
}

export interface TranscriptMemoryBoundsSummary {
  readonly droppedCount: number
  readonly droppedBytesEstimate: number
  readonly keptCount: number
  readonly keptBytesEstimate: number
}

export interface TranscriptMemoryBoundsResult {
  readonly items: TranscriptItem[]
  readonly summary: TranscriptMemoryBoundsSummary | null
}

const encoder = new TextEncoder()

const estimateTranscriptItemBytes = (item: TranscriptItem): number => {
  const payload = {
    id: item.id,
    kind: item.kind,
    source: item.source,
    createdAt: item.createdAt,
    streaming: item.streaming ?? false,
    text: "text" in item ? item.text : "",
  }
  return encoder.encode(JSON.stringify(payload)).length
}

const buildCompactionMarker = (kept: TranscriptItem[], summary: TranscriptMemoryBoundsSummary): TranscriptItem => {
  const firstKeptId = kept[0]?.id ?? "none"
  const markerCreatedAt = kept[0]?.createdAt ?? 0
  return {
    id: `system:transcript-compacted:${firstKeptId}:${summary.droppedCount}`,
    kind: "system",
    source: "system",
    createdAt: markerCreatedAt,
    systemKind: "notice",
    text: `[transcript_compacted] dropped=${summary.droppedCount} dropped_bytes_est=${summary.droppedBytesEstimate} kept=${summary.keptCount} kept_bytes_est=${summary.keptBytesEstimate}`,
    status: "pending",
  }
}

export const applyTranscriptMemoryBounds = (
  entries: readonly TranscriptItem[],
  options: TranscriptMemoryBoundsOptions,
): TranscriptMemoryBoundsResult => {
  if (!options.enabled) {
    return { items: [...entries], summary: null }
  }

  const maxItems = Math.max(1, Math.floor(options.maxItems))
  const maxBytes = Math.max(256, Math.floor(options.maxBytes))

  const keptReversed: TranscriptItem[] = []
  let keptBytes = 0
  let droppedBytes = 0
  let droppedCount = 0

  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]
    const size = estimateTranscriptItemBytes(entry)
    const wouldExceedItems = keptReversed.length >= maxItems
    const wouldExceedBytes = keptReversed.length > 0 && keptBytes + size > maxBytes
    if (wouldExceedItems || wouldExceedBytes) {
      droppedCount += 1
      droppedBytes += size
      continue
    }
    keptReversed.push(entry)
    keptBytes += size
  }

  const kept = keptReversed.reverse()
  if (droppedCount === 0) {
    return { items: kept, summary: null }
  }

  const summary: TranscriptMemoryBoundsSummary = {
    droppedCount,
    droppedBytesEstimate: droppedBytes,
    keptCount: kept.length,
    keptBytesEstimate: keptBytes,
  }

  if (!options.includeCompactionMarker) {
    return { items: kept, summary }
  }

  const marker = buildCompactionMarker(kept, summary)
  return { items: [marker, ...kept], summary }
}
