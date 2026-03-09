import { existsSync, readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

import {
  assertValid,
  type CheckpointMetadataV1,
  type EngineConformanceManifestV1,
  type KernelEventV1,
  type SessionTranscriptV1Item,
  type TaskV1,
} from "@breadboard/kernel-contracts"

const MODULE_DIR = dirname(fileURLToPath(import.meta.url))

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

export function resolveRepoRoot(explicitRoot?: string): string {
  const candidates = [
    explicitRoot,
    process.env.BREADBOARD_REPO_ROOT,
    join(MODULE_DIR, "../../../../"),
    join(MODULE_DIR, "../../../../../"),
  ].filter((value): value is string => Boolean(value))
  for (const candidate of candidates) {
    const normalized = resolve(candidate)
    if (existsSync(join(normalized, "contracts", "kernel", "schemas"))) {
      return normalized
    }
  }
  throw new Error("Unable to resolve BreadBoard repo root for ts-kernel-core")
}

export function loadTrackedJson<T = unknown>(repoRelativePath: string, repoRoot?: string): T {
  const root = resolveRepoRoot(repoRoot)
  return JSON.parse(readFileSync(join(root, repoRelativePath), "utf8")) as T
}

export function loadEngineConformanceManifest(repoRoot?: string): EngineConformanceManifestV1 {
  const manifest = loadTrackedJson<unknown>("conformance/engine_fixtures/python_reference_manifest_v1.json", repoRoot)
  return assertValid<EngineConformanceManifestV1>("engineConformanceManifest", manifest)
}

export function loadKernelFixture<T = unknown>(fixtureRelativePath: string, repoRoot?: string): T {
  return loadTrackedJson<T>(join("conformance/engine_fixtures", fixtureRelativePath), repoRoot)
}
