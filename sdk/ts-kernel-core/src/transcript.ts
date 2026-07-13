import type {
  CheckpointMetadataV1,
  KernelEventV1,
  KernelEventV2,
  SessionTranscriptV1Item,
  SessionTranscriptV2,
  TaskV1,
} from "@breadboard/kernel-contracts"

type SessionTranscriptV2Item = SessionTranscriptV2["items"][number]
type TranscriptVisibilityV2 = SessionTranscriptV2Item["visibility"]
type TranscriptProvenanceSource = NonNullable<SessionTranscriptV2Item["provenance"]>["source"]

const CANONICAL_TRANSCRIPT_SOURCES: Record<TranscriptProvenanceSource, true> = {
  live: true,
  replay: true,
  import: true,
  compaction: true,
  fixture: true,
}

export function buildKernelEventId(runId: string, seq: number): string {
  return `${runId}:evt:${seq}`
}

export function cloneTranscriptItem<T>(item: T): T {
  return JSON.parse(JSON.stringify(item)) as T
}

export function eventBelongsToSession(event: KernelEventV1 | KernelEventV2, sessionId: string): boolean {
  return "session_id" in event ? event.session_id === sessionId : event.sessionId === sessionId
}

function normalizeTranscriptVisibility(visibility: unknown): TranscriptVisibilityV2 {
  if (visibility && typeof visibility === "object") {
    const value = visibility as Partial<TranscriptVisibilityV2>
    const redactionState = value.redaction_state === "redacted"
      || value.redaction_state === "summarized"
      || value.redaction_state === "elided"
      ? value.redaction_state
      : "none"
    return {
      model_visible: value.model_visible === true,
      provider_visible: value.provider_visible === true,
      host_visible: value.host_visible === true,
      redaction_state: redactionState,
    }
  }
  if (visibility === "model") {
    return { model_visible: true, provider_visible: true, host_visible: true, redaction_state: "none" }
  }
  return { model_visible: false, provider_visible: false, host_visible: true, redaction_state: "none" }
}

function normalizeCanonicalTranscriptItem(item: Record<string, unknown>): SessionTranscriptV2Item {
  const provenance = item.provenance && typeof item.provenance === "object"
    ? item.provenance as Record<string, unknown>
    : null
  const source = provenance?.source
  const canonicalSource = typeof source === "string" && source in CANONICAL_TRANSCRIPT_SOURCES
  const normalized: SessionTranscriptV2Item = {
    kind: typeof item.kind === "string" && item.kind.length > 0 ? item.kind : "annotation",
    visibility: normalizeTranscriptVisibility(item.visibility),
    content: "content" in item ? item.content : null,
    content_schema_version:
      typeof item.content_schema_version === "string" ? item.content_schema_version : null,
    provenance: {
      source: canonicalSource
        ? source as TranscriptProvenanceSource
        : "import",
      source_ref:
        typeof provenance?.source_ref === "string"
          ? provenance.source_ref
          : typeof source === "string"
            ? source
            : null,
    },
  }
  const callId = typeof item.call_id === "string" ? item.call_id : item.callId
  if (typeof callId === "string") normalized.call_id = callId
  const eventId = typeof item.event_id === "string" ? item.event_id : (item.eventId ?? provenance?.eventId)
  if (typeof eventId === "string") normalized.event_id = eventId
  if (typeof item.seq === "number") normalized.seq = item.seq
  if (item.metadata && typeof item.metadata === "object") {
    normalized.metadata = cloneTranscriptItem(item.metadata as Record<string, unknown>)
  }
  return normalized
}

export function normalizeTranscriptContractItem(item: Record<string, unknown>): SessionTranscriptV2Item {
  if ("kind" in item && "visibility" in item) {
    return normalizeCanonicalTranscriptItem(item)
  }

  const entries = Object.entries(item)
  if (entries.length === 1) {
    const [key, value] = entries[0]
    const mapping: Record<string, { kind: string; modelVisible: boolean }> = {
      assistant: { kind: "assistant_message", modelVisible: true },
      user: { kind: "user_message", modelVisible: true },
      tool_execution_plan: { kind: "tool_execution_plan", modelVisible: false },
      completion_analysis: { kind: "completion_analysis", modelVisible: false },
      turn_context: { kind: "turn_context", modelVisible: false },
      loop_detection: { kind: "loop_detection", modelVisible: false },
      context_window: { kind: "context_window", modelVisible: false },
      stream_policy: { kind: "stream_policy", modelVisible: false },
      stop_requested: { kind: "stop_requested", modelVisible: false },
      provider_response: { kind: "provider_response", modelVisible: false },
    }
    const mapped = mapping[key] ?? { kind: key, modelVisible: false }
    return {
      kind: mapped.kind,
      visibility: normalizeTranscriptVisibility(mapped.modelVisible ? "model" : "host"),
      content: value,
      content_schema_version: null,
      provenance: { source: "import", source_ref: `legacy_transcript_entry:${key}` },
    }
  }

  return {
    kind: "annotation",
    visibility: normalizeTranscriptVisibility("host"),
    content: cloneTranscriptItem(item),
    content_schema_version: null,
    provenance: { source: "import", source_ref: "legacy_transcript_entry:multi_key" },
  }
}

export function normalizeTranscriptContractItems(
  items: Array<Record<string, unknown> | SessionTranscriptV1Item | SessionTranscriptV2Item>,
): SessionTranscriptV2Item[] {
  return items.map((item) => normalizeTranscriptContractItem(item as Record<string, unknown>))
}

export function buildTaskLineage(task: TaskV1, tasksById: Record<string, TaskV1>): string[] {
  const lineage: string[] = [task.task_id]
  let cursor = task.parent_task_id
  while (typeof cursor === "string" && cursor.length > 0) {
    lineage.unshift(cursor)
    cursor = tasksById[cursor]?.parent_task_id
  }
  return lineage
}

export function cloneCheckpointMetadata<T extends CheckpointMetadataV1>(item: T): T {
  return JSON.parse(JSON.stringify(item)) as T
}
