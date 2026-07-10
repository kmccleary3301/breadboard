import type { Block } from "@stream-mdx/core/types"
import type { LiveSlotStatus, ToolDisplayPayload, ToolLogKind } from "./types.js"

export type TranscriptId = string

export type TranscriptItemKind = "message" | "tool" | "system"

export type TranscriptItemSource = "conversation" | "tool" | "system" | "raw"

export type TranscriptCellLifecycle = "live" | "committed" | "frozen" | "ephemeral"

export type TranscriptRenderMode = "rich" | "raw" | "viewer"

export type TranscriptCellRole =
  | "user-request"
  | "assistant-message"
  | "tool-summary"
  | "tool-call"
  | "tool-result"
  | "tool-error"
  | "diff"
  | "approval"
  | "interrupted"
  | "status"
  | "landing"
  | "command-result"
  | "system"

export interface TranscriptItemBase {
  readonly id: TranscriptId
  readonly kind: TranscriptItemKind
  readonly createdAt: number
  readonly source: TranscriptItemSource
  readonly streaming?: boolean
  readonly lifecycle?: TranscriptCellLifecycle
  readonly renderModes?: ReadonlyArray<TranscriptRenderMode>
  readonly cellRole?: TranscriptCellRole
  readonly dedupeKey?: string | null
}

export interface TranscriptMessageItem extends TranscriptItemBase {
  readonly kind: "message"
  readonly speaker: "assistant" | "user" | "system"
  readonly text: string
  readonly phase: "final" | "streaming"
  readonly activePreviewLines?: number
  readonly richBlocks?: ReadonlyArray<Block>
  readonly markdownStreaming?: boolean
  readonly markdownFinalized?: boolean
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
  readonly systemKind: ToolLogKind | "notice" | "warning" | "log" | "usage" | "command-result"
  readonly text: string
  readonly status?: LiveSlotStatus
}

export type TranscriptItem = TranscriptMessageItem | TranscriptToolItem | TranscriptSystemItem

export const makeTranscriptId = (prefix: string, id: string): TranscriptId => `${prefix}:${id}`

export interface TranscriptCellRecord {
  readonly id: TranscriptId
  readonly kind: TranscriptItemKind
  readonly role: TranscriptCellRole
  readonly lifecycle: TranscriptCellLifecycle
  readonly source: TranscriptItemSource
  readonly createdAt: number
  readonly streaming: boolean
  readonly renderModes: ReadonlyArray<TranscriptRenderMode>
  readonly dedupeKey: string | null
  readonly textPreview: string
  readonly speaker?: TranscriptMessageItem["speaker"]
  readonly status?: LiveSlotStatus
}

const DEFAULT_RENDER_MODES: ReadonlyArray<TranscriptRenderMode> = ["rich", "raw", "viewer"]

const previewText = (value: string, limit = 160): string => {
  const normalized = value.replace(/\s+/g, " ").trim()
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, Math.max(0, limit - 1))}…`
}

export const resolveTranscriptLifecycle = (item: TranscriptItem): TranscriptCellLifecycle => {
  if (item.lifecycle) return item.lifecycle
  if (item.streaming) return "live"
  if (item.kind === "system" && item.systemKind === "usage") return "ephemeral"
  return "committed"
}

export const resolveTranscriptRenderModes = (item: TranscriptItem): ReadonlyArray<TranscriptRenderMode> => {
  if (item.renderModes?.length) return item.renderModes
  return DEFAULT_RENDER_MODES
}

export const resolveTranscriptCellRole = (item: TranscriptItem): TranscriptCellRole => {
  if (item.cellRole) return item.cellRole
  if (item.kind === "message" && item.speaker === "user") return "user-request"
  if (item.kind === "message" && item.speaker === "assistant") return "assistant-message"
  if (item.kind === "tool") {
    const normalizedText = item.text.trim().toLowerCase()
    if (normalizedText.startsWith("[interrupt]") || normalizedText.startsWith("[cancel]")) return "interrupted"
    const hasDiffBlocks = Array.isArray(item.display?.diff_blocks) && item.display.diff_blocks.length > 0
    if (hasDiffBlocks) return "diff"
    if (item.toolKind === "error" || item.status === "error") return "tool-error"
    if (item.toolKind === "result" || item.status === "success") return "tool-result"
    if (item.toolKind === "call" || item.status === "pending") return "tool-call"
    return "tool-summary"
  }
  if (item.kind === "system") {
    if (item.systemKind === "command-result") return "command-result"
    const normalizedText = item.text.trim().toLowerCase()
    if (normalizedText.startsWith("[permission]")) return "approval"
    if (normalizedText.startsWith("[interrupt]") || normalizedText.startsWith("[cancel]")) return "interrupted"
    if (item.systemKind === "status" || item.systemKind === "log" || item.systemKind === "usage") return "status"
    return "system"
  }
  return "system"
}

export const resolveTranscriptDedupeKey = (item: TranscriptItem): string | null => {
  if (item.dedupeKey !== undefined) return item.dedupeKey
  const role = resolveTranscriptCellRole(item)
  if (role !== "status") return null
  if (item.kind === "system") {
    return `${item.systemKind}:${item.text.trim()}`
  }
  return `${role}:${item.id}`
}

export const toTranscriptCellRecord = (item: TranscriptItem): TranscriptCellRecord => {
  const base = {
    id: item.id,
    kind: item.kind,
    role: resolveTranscriptCellRole(item),
    lifecycle: resolveTranscriptLifecycle(item),
    source: item.source,
    createdAt: item.createdAt,
    streaming: item.streaming === true,
    renderModes: resolveTranscriptRenderModes(item),
    dedupeKey: resolveTranscriptDedupeKey(item),
    textPreview: previewText(item.text),
  }
  if (item.kind === "message") {
    return {
      ...base,
      speaker: item.speaker,
    }
  }
  if (item.kind === "tool" || item.kind === "system") {
    return {
      ...base,
      status: item.status,
    }
  }
  return base
}

export const dumpTranscriptCellRecords = (items: ReadonlyArray<TranscriptItem>): TranscriptCellRecord[] =>
  items.map(toTranscriptCellRecord)
