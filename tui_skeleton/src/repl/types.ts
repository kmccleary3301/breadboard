import type { Block } from "@stream-mdx/core/types"

export interface ConversationEntry {
  readonly id: string
  readonly speaker: "assistant" | "user" | "system"
  readonly text: string
  readonly phase: "final" | "streaming"
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
}

export interface CompletionState {
  readonly completed: boolean
  readonly summary?: Record<string, unknown> | null
}

export type ToolLogKind = "command" | "status" | "call" | "result" | "reward" | "error" | "completion"

export interface ToolLogEntry {
  readonly id: string
  readonly kind: ToolLogKind
  readonly text: string
  readonly status?: LiveSlotStatus
  readonly createdAt: number
}

export interface TodoItem {
  readonly id: string
  readonly title: string
  readonly status: string
  readonly priority?: string | number | null
  readonly metadata?: Record<string, unknown> | null
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
