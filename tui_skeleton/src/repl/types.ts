import type { Block, DiffKind, TokenLineV1 } from "@stream-mdx/core/types"

export interface ConversationEntry {
  readonly id: string
  readonly speaker: "assistant" | "user" | "system"
  readonly text: string
  readonly phase: "final" | "streaming"
  readonly createdAt: number
  readonly richBlocks?: ReadonlyArray<Block>
  readonly markdownStreaming?: boolean
  readonly markdownError?: string | null
}

export type LiveSlotStatus = "pending" | "success" | "error"

export interface LiveSlotEntry {
  readonly id: string
  readonly text: string
  readonly color?: string
  readonly status: LiveSlotStatus
  readonly updatedAt: number
  readonly summary?: string
}

export interface StreamStats {
  eventCount: number
  toolCount: number
  lastTurn: number | null
  remote: boolean
  model: string
  usage?: UsageMetrics
}

export interface UsageMetrics {
  promptTokens?: number
  completionTokens?: number
  totalTokens?: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
  costUsd?: number
  latencyMs?: number
}

export interface QueuedAttachment {
  readonly id: string
  readonly mime: string
  readonly base64: string
  readonly size: number
}

export interface TranscriptPreferences {
  readonly collapseMode: "auto" | "none" | "all"
  readonly virtualization: "auto" | "compact"
  readonly richMarkdown: boolean
  readonly toolRail?: boolean
  readonly toolInline?: boolean
  readonly rawStream?: boolean
  readonly showReasoning?: boolean
  readonly diffLineNumbers?: boolean
}

export interface CompletionState {
  readonly completed: boolean
  readonly summary?: Record<string, unknown> | null
}

export type ActivityPrimary =
  | "idle"
  | "session"
  | "run"
  | "thinking"
  | "responding"
  | "tool_call"
  | "tool_result"
  | "permission_required"
  | "permission_resolved"
  | "reconnecting"
  | "compacting"
  | "completed"
  | "halted"
  | "cancelled"
  | "error"

export type ActivitySource = "runtime" | "event" | "user" | "system"

export interface ActivityDetail {
  readonly message?: string | null
  readonly eventType?: string | null
  readonly source?: ActivitySource | null
}

export interface ActivitySnapshot {
  readonly primary: ActivityPrimary
  readonly label: string
  readonly detail?: ActivityDetail | null
  readonly updatedAt: number
  readonly displayedAt: number
  readonly seq: number
}

export interface RuntimeTelemetry {
  readonly statusTransitions: number
  readonly suppressedTransitions: number
  readonly illegalTransitions: number
  readonly markdownFlushes: number
  readonly thinkingUpdates: number
  readonly adaptiveCadenceAdjustments: number
  readonly workgraphFlushes: number
  readonly workgraphEvents: number
  readonly workgraphMaxQueueDepth: number
}

export type ThinkingMode = "off" | "summary" | "full"

export interface ThinkingArtifact {
  readonly id: string
  readonly mode: ThinkingMode
  readonly startedAt: number
  readonly updatedAt: number
  readonly finalizedAt?: number | null
  readonly summary: string
  readonly rawText?: string | null
  readonly summaryTruncated?: boolean
  readonly rawTruncated?: boolean
  readonly sourceEventTypes?: ReadonlyArray<string>
}

export interface RuntimeBehaviorFlags {
  readonly activityEnabled: boolean
  readonly lifecycleToastsEnabled: boolean
  readonly thinkingEnabled: boolean
  readonly allowFullThinking: boolean
  readonly allowRawThinkingPeek: boolean
  readonly inlineThinkingBlockEnabled: boolean
  readonly markdownCoalescingEnabled: boolean
  readonly adaptiveMarkdownCadenceEnabled: boolean
  readonly transitionDebug: boolean
  readonly minDisplayMs: number
  readonly statusUpdateMs: number
  readonly thinkingMaxChars: number
  readonly thinkingMaxLines: number
  readonly adaptiveMarkdownMinChunkChars: number
  readonly adaptiveMarkdownMinCoalesceMs: number
  readonly adaptiveMarkdownBurstChars: number
  readonly subagentWorkGraphEnabled: boolean
  readonly subagentStripEnabled: boolean
  readonly subagentToastsEnabled: boolean
  readonly subagentTaskboardEnabled: boolean
  readonly subagentFocusEnabled: boolean
  readonly subagentCoalesceMs: number
  readonly subagentMaxWorkItems: number
  readonly subagentMaxStepsPerTask: number
}

export interface ProviderCapabilitiesSnapshot {
  readonly provider: string
  readonly model: string | null
  readonly reasoningEvents: boolean
  readonly thoughtSummaryEvents: boolean
  readonly contextUsage: boolean
  readonly activitySurface: boolean
  readonly rawThinkingPeek: boolean
  readonly inlineThinkingBlock: boolean
  readonly warnings?: ReadonlyArray<string>
}

export type ToolLogKind = "command" | "status" | "call" | "result" | "reward" | "error" | "completion"

export interface DiffBlock {
  readonly kind: "diff"
  readonly filePath?: string | null
  readonly unified?: string | null
  readonly additions?: number | null
  readonly deletions?: number | null
  readonly language?: string | null
  readonly toolCallId?: string | null
  readonly title?: string | null
}

export interface TuiToken {
  readonly content: string
  readonly color?: string | null
  readonly fontStyle?: number | null
}

export type TuiTokenLine = ReadonlyArray<TuiToken>

export type TuiDiffLineKind = "meta" | "hunk" | "add" | "del" | "context"

export interface TuiDiffLine {
  readonly kind: TuiDiffLineKind
  readonly marker: string
  readonly text: string
  readonly oldNo?: number | null
  readonly newNo?: number | null
  readonly tokens?: TuiTokenLine | null
}

export interface MarkdownCodeLine {
  readonly text: string
  readonly tokens?: TokenLineV1 | null
  readonly diffKind?: DiffKind | null
  readonly oldNo?: number | null
  readonly newNo?: number | null
}

export interface ToolDisplayPayload {
  readonly title?: string | null
  readonly summary?: string | string[] | null
  readonly detail?: string | string[] | null
  readonly detail_truncated?: {
    readonly hidden?: number | null
    readonly tail?: number | null
    readonly mode?: string | null
    readonly hint?: string | null
  } | null
  readonly category?: string | null
  readonly diff_blocks?: DiffBlock[] | null
}

export interface ToolLogEntry {
  readonly id: string
  readonly kind: ToolLogKind
  readonly text: string
  readonly status?: LiveSlotStatus
  readonly callId?: string | null
  readonly createdAt: number
  readonly display?: ToolDisplayPayload | null
}

export interface TodoItem {
  readonly id: string
  readonly title: string
  readonly status: string
  readonly priority?: string | number | null
  readonly metadata?: Record<string, unknown> | null
}

export type WorkStatus = "pending" | "running" | "blocked" | "completed" | "failed" | "cancelled"

export type WorkMode = "sync" | "async" | "unknown"

export type WorkStepKind = "tool" | "note" | "message"

export interface WorkStep {
  readonly stepId: string
  readonly kind: WorkStepKind
  readonly label: string
  readonly status: WorkStatus
  readonly startedAt?: number
  readonly endedAt?: number
  readonly attempt?: number
  readonly detail?: string
}

export interface WorkCounters {
  readonly completed: number
  readonly running: number
  readonly failed: number
  readonly total: number
}

export interface WorkItem {
  readonly workId: string
  readonly laneId: string
  readonly laneLabel: string
  readonly title: string
  readonly mode: WorkMode
  readonly status: WorkStatus
  readonly createdAt: number
  readonly updatedAt: number
  readonly parentWorkId?: string | null
  readonly treePath?: string | null
  readonly depth?: number | null
  readonly artifactPaths?: ReadonlyArray<string>
  readonly lastSafeExcerpt?: string | null
  readonly steps: ReadonlyArray<WorkStep>
  readonly counters: WorkCounters
}

export type LaneKind = "main" | "subagent" | "background_task"

export interface LaneStatusSummary {
  readonly running: number
  readonly failed: number
  readonly blocked: number
}

export interface Lane {
  readonly laneId: string
  readonly label: string
  readonly kind: LaneKind
  readonly statusSummary: LaneStatusSummary
  readonly artifact?: {
    readonly jsonl?: string | null
    readonly metaJson?: string | null
  } | null
}

export interface WorkGraphState {
  readonly itemsById: Record<string, WorkItem>
  readonly itemOrder: ReadonlyArray<string>
  readonly lanesById: Record<string, Lane>
  readonly laneOrder: ReadonlyArray<string>
  readonly processedEventKeys: ReadonlyArray<string>
  readonly lastSeq: number
}

export interface TaskEntry {
  readonly id: string
  readonly sessionId?: string | null
  readonly description?: string | null
  readonly subagentType?: string | null
  readonly status?: string | null
  readonly kind?: string | null
  readonly outputExcerpt?: string | null
  readonly artifactPath?: string | null
  readonly error?: string | null
  readonly ctreeNodeId?: string | null
  readonly ctreeSnapshot?: CTreeSnapshot | null
  readonly updatedAt: number
}

export type SkillType = "prompt" | "graph"

export interface SkillEntry {
  readonly id: string
  readonly type: SkillType
  readonly version: string
  readonly label?: string | null
  readonly group?: string | null
  readonly description?: string | null
  readonly long_description?: string | null
  readonly tags?: string[] | null
  readonly defaults?: Record<string, unknown> | null
  readonly dependencies?: string[] | null
  readonly conflicts?: string[] | null
  readonly deprecated?: boolean | null
  readonly provider_constraints?: Record<string, unknown> | null
  readonly slot?: "system" | "developer" | "user" | "per_turn" | null
  readonly steps?: number | null
  readonly determinism?: string | null
  readonly enabled?: boolean | null
}

export interface SkillSelection {
  readonly mode?: "allowlist" | "blocklist"
  readonly allowlist?: string[]
  readonly blocklist?: string[]
  readonly profile?: string | null
}

export interface SkillCatalog {
  readonly catalog_version?: string
  readonly selection?: SkillSelection | null
  readonly skills?: SkillEntry[]
  readonly prompt_skills?: Array<Record<string, unknown>>
  readonly graph_skills?: Array<Record<string, unknown>>
}

export interface SkillCatalogSources {
  readonly config_path?: string | null
  readonly workspace?: string | null
  readonly plugin_count?: number | null
  readonly skill_paths?: string[] | null
}

export type InspectMenuState =
  | { readonly status: "hidden" }
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | {
      readonly status: "ready"
      readonly session?: Record<string, unknown> | null
      readonly skills?: Record<string, unknown> | null
      readonly ctree?: Record<string, unknown> | null
    }

export type SkillsMenuState =
  | { readonly status: "hidden" }
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | {
      readonly status: "ready"
      readonly catalog: SkillCatalog
      readonly selection: SkillSelection | null
      readonly sources?: SkillCatalogSources | null
    }

export interface CTreeSnapshot {
  readonly snapshot?: Record<string, unknown> | null
  readonly compiler?: Record<string, unknown> | null
  readonly collapse?: Record<string, unknown> | null
  readonly runner?: Record<string, unknown> | null
  readonly hash_summary?: Record<string, unknown> | null
  readonly last_node?: Record<string, unknown> | null
}

export interface ModelMenuItem {
  readonly label: string
  readonly value: string
  readonly provider: string
  readonly detail?: string
  readonly isDefault?: boolean
  readonly isCurrent?: boolean
  readonly contextTokens?: number | null
  readonly priceInPerM?: number | null
  readonly priceOutPerM?: number | null
}

export type ModelMenuState =
  | { readonly status: "hidden" }
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "ready"; readonly items: ReadonlyArray<ModelMenuItem> }

export interface GuardrailNotice {
  readonly id: string
  readonly summary: string
  readonly detail?: string
  readonly timestamp: number
  readonly expanded: boolean
}

export type PermissionRuleScope = "session" | "project" | "global"

type PermissionDecisionNote = { readonly note?: string | null }

export type PermissionDecision =
  | ({ readonly kind: "allow-once" } & PermissionDecisionNote)
  | ({ readonly kind: "allow-always"; readonly scope: PermissionRuleScope; readonly rule?: string | null } & PermissionDecisionNote)
  | ({ readonly kind: "deny-once" } & PermissionDecisionNote)
  | ({ readonly kind: "deny-always"; readonly scope: PermissionRuleScope; readonly rule?: string | null } & PermissionDecisionNote)
  | ({ readonly kind: "deny-stop" } & PermissionDecisionNote)

export interface PermissionRequest {
  readonly requestId: string
  readonly tool: string
  readonly kind: string
  readonly rewindable: boolean
  readonly summary: string
  readonly diffText?: string | null
  readonly ruleSuggestion?: string | null
  readonly defaultScope: PermissionRuleScope
  readonly createdAt: number
}

export interface CheckpointSummary {
  readonly checkpointId: string
  readonly createdAt: number
  readonly preview: string
  readonly trackedFiles?: number | null
  readonly additions?: number | null
  readonly deletions?: number | null
  readonly hasUntrackedChanges?: boolean | null
}

export type RewindMenuState =
  | { readonly status: "hidden" }
  | { readonly status: "loading"; readonly checkpoints: ReadonlyArray<CheckpointSummary> }
  | { readonly status: "ready"; readonly checkpoints: ReadonlyArray<CheckpointSummary> }
  | { readonly status: "error"; readonly message: string; readonly checkpoints: ReadonlyArray<CheckpointSummary> }
