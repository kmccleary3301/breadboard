import type {
  CheckpointMetadataV1,
  KernelEventV1,
  SessionTranscriptV1Item,
  TaskV1,
} from "@breadboard/kernel-contracts"

export function buildKernelEventId(runId: string, seq: number): string {
  return `${runId}:evt:${seq}`
}

export function cloneTranscriptItem<T extends SessionTranscriptV1Item>(item: T): T {
  return JSON.parse(JSON.stringify(item)) as T
}

export function eventBelongsToSession(event: KernelEventV1, sessionId: string): boolean {
  return event.sessionId === sessionId
}

export function normalizeTranscriptContractItem(item: Record<string, unknown>): SessionTranscriptV1Item {
  if ("kind" in item && "visibility" in item && "content" in item) {
    return cloneTranscriptItem(item as unknown as SessionTranscriptV1Item)
  }

  const entries = Object.entries(item)
  if (entries.length === 1) {
    const [key, value] = entries[0]
    const mapping: Record<string, { kind: string; visibility: "model" | "host" | "audit" }> = {
      assistant: { kind: "assistant_message", visibility: "model" },
      user: { kind: "user_message", visibility: "model" },
      tool_execution_plan: { kind: "tool_execution_plan", visibility: "host" },
      completion_analysis: { kind: "completion_analysis", visibility: "host" },
      turn_context: { kind: "turn_context", visibility: "host" },
      loop_detection: { kind: "loop_detection", visibility: "host" },
      context_window: { kind: "context_window", visibility: "host" },
      stream_policy: { kind: "stream_policy", visibility: "host" },
      stop_requested: { kind: "stop_requested", visibility: "host" },
      provider_response: { kind: "provider_response", visibility: "host" },
    }
    const mapped = mapping[key] ?? { kind: key, visibility: "host" as const }
    return {
      kind: mapped.kind,
      visibility: mapped.visibility,
      content: value,
      provenance: { source: "legacy_transcript_entry", legacy_key: key },
    }
  }

  return {
    kind: "annotation",
    visibility: "host",
    content: item,
    provenance: { source: "legacy_transcript_entry", legacy_shape: "multi_key" },
  }
}

export function normalizeTranscriptContractItems(
  items: Array<Record<string, unknown> | SessionTranscriptV1Item>,
): SessionTranscriptV1Item[] {
  return items.map((item) =>
    "kind" in item && "visibility" in item && "content" in item
      ? cloneTranscriptItem(item as SessionTranscriptV1Item)
      : normalizeTranscriptContractItem(item as Record<string, unknown>),
  )
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
