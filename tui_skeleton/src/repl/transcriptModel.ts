import type { Block } from "@stream-mdx/core/types"
import type { LiveSlotStatus, ToolDisplayPayload, ToolLogKind } from "./types.js"

export type TranscriptId = string

export type TranscriptItemKind = "message" | "tool" | "system"

export type TranscriptItemSource = "conversation" | "tool" | "system" | "raw"

export interface TranscriptItemBase {
  readonly id: TranscriptId
  readonly kind: TranscriptItemKind
  readonly createdAt: number
  readonly source: TranscriptItemSource
  readonly streaming?: boolean
}

export interface TranscriptMessageItem extends TranscriptItemBase {
  readonly kind: "message"
  readonly speaker: "assistant" | "user" | "system"
  readonly text: string
  readonly phase: "final" | "streaming"
  readonly richBlocks?: ReadonlyArray<Block>
  readonly markdownStreaming?: boolean
  readonly markdownError?: string | null
}

export interface TranscriptToolItem extends TranscriptItemBase {
  readonly kind: "tool"
  readonly toolKind: ToolLogKind
  readonly text: string
  readonly status?: LiveSlotStatus
  readonly callId?: string | null
  readonly display?: ToolDisplayPayload | null
}

export interface TranscriptSystemItem extends TranscriptItemBase {
  readonly kind: "system"
  readonly systemKind: ToolLogKind | "notice" | "warning" | "log" | "usage"
  readonly text: string
  readonly status?: LiveSlotStatus
}

export type TranscriptItem = TranscriptMessageItem | TranscriptToolItem | TranscriptSystemItem

export const makeTranscriptId = (prefix: string, id: string): TranscriptId => `${prefix}:${id}`
