import { existsSync, readdirSync, readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

import {
  assertValid,
  type CheckpointMetadataV1,
  type EngineConformanceManifestV1,
  type KernelEventV1,
  type RunContextV1,
  type RunRequestV1,
  type SessionTranscriptV1Item,
  type SessionTranscriptV1,
  type TaskV1,
  type ToolCallV1,
  type ToolExecutionOutcomeV1,
  type ToolModelRenderV1,
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

export interface KernelConformanceSummary {
  schemaVersion: "bb.kernel_conformance_summary.v1"
  manifestRows: number
  comparatorClasses: string[]
  supportTiers: string[]
  fixtureFamilies: string[]
  fixtureCount: number
}

export function listKernelFixtureFamilies(repoRoot?: string): string[] {
  const root = resolveRepoRoot(repoRoot)
  const fixturesRoot = join(root, "conformance", "engine_fixtures")
  if (!existsSync(fixturesRoot)) {
    return []
  }
  return readdirSync(fixturesRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
}

export function buildConformanceSummary(repoRoot?: string): KernelConformanceSummary {
  const root = resolveRepoRoot(repoRoot)
  const manifest = loadEngineConformanceManifest(root)
  const fixtureFamilies = listKernelFixtureFamilies(root)
  let fixtureCount = 0
  for (const family of fixtureFamilies) {
    const familyRoot = join(root, "conformance", "engine_fixtures", family)
    fixtureCount += readdirSync(familyRoot, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .length
  }
  return {
    schemaVersion: "bb.kernel_conformance_summary.v1",
    manifestRows: manifest.rows.length,
    comparatorClasses: Array.from(new Set(manifest.rows.map((row) => row.comparatorClass))).sort(),
    supportTiers: Array.from(new Set(manifest.rows.map((row) => row.supportTier))).sort(),
    fixtureFamilies,
    fixtureCount,
  }
}

export interface StaticTextTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  executionMode?: string | null
  activeMode?: string | null
  assistantText: string
  startedAt?: string
}

export interface StaticTextTurnResult {
  runContext: RunContextV1
  events: KernelEventV1[]
  transcript: SessionTranscriptV1
}

export interface ScriptedToolTurnOptions {
  sessionId?: string
  engineFamily?: string
  engineRef?: string | null
  resolvedModel?: string | null
  resolvedProviderRoute?: string | null
  executionMode?: string | null
  activeMode?: string | null
  toolCall: ToolCallV1
  toolOutcome: ToolExecutionOutcomeV1
  toolRender: ToolModelRenderV1
  assistantText?: string | null
  startedAt?: string
}

export function buildRunContextFromRequest(
  request: RunRequestV1,
  options: Omit<StaticTextTurnOptions, "assistantText" | "startedAt"> = {},
): RunContextV1 {
  return {
    schema_version: "bb.run_context.v1",
    session_id: options.sessionId ?? `sess-${request.request_id}`,
    engine_family: options.engineFamily ?? "breadboard-ts",
    request_id: request.request_id,
    engine_ref: options.engineRef ?? "ts-kernel-core/v0",
    workspace_root: request.workspace_root ?? null,
    resolved_model: options.resolvedModel ?? request.requested_model ?? null,
    resolved_provider_route: options.resolvedProviderRoute ?? null,
    active_mode: options.activeMode ?? null,
    feature_flags: { ...(request.requested_features ?? {}) },
    execution_mode: options.executionMode ?? "static_text_turn",
    delegated_services: [],
    metadata: { source: "ts-kernel-core" },
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
