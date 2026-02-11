import type { Block } from "@stream-mdx/core/types"
import { MarkdownStreamer } from "../../markdown/streamer.js"
import type {
  ConversationEntry,
  LiveSlotEntry,
  LiveSlotStatus,
  TranscriptPreferences,
} from "../../repl/types.js"

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const STREAM_MARKDOWN = parseBoolEnv(process.env.BREADBOARD_MARKDOWN_STREAM, true)

export function shouldStreamMarkdown(this: any): boolean {
  return STREAM_MARKDOWN && this.viewPrefs.richMarkdown && !this.markdownGloballyDisabled
}

const LIVE_TOKENIZATION = process.env.BREADBOARD_STREAM_MDX_LIVE_TOKENS === "1"

export function ensureMarkdownStreamer(
  this: any,
  entryId: string,
): { streamer: MarkdownStreamer; lastText: string } | null {
  if (!this.shouldStreamMarkdown()) return null
  const existing = this.markdownStreams.get(entryId)
  if (existing) return existing
  const streamer = new MarkdownStreamer({
    docPlugins: {
      codeHighlighting: LIVE_TOKENIZATION ? "incremental" : "final",
      outputMode: "tokens",
      emitHighlightTokens: true,
      emitDiffBlocks: true,
      liveTokenization: LIVE_TOKENIZATION,
      liveCodeHighlighting: false,
    },
  })
  streamer.subscribe((blocks, meta) => this.applyMarkdownBlocks(entryId, blocks, meta?.error ?? null, meta?.finalized ?? false))
  streamer.initialize()
  const state = { streamer, lastText: "" }
  this.markdownStreams.set(entryId, state)
  return state
}

export function appendMarkdownChunk(this: any, text: string): void {
  if (!this.streamingEntryId || !this.shouldStreamMarkdown()) return
  const entryId = this.streamingEntryId
  const state = this.ensureMarkdownStreamer(entryId)
  if (!state) return
  const previous = state.lastText
  const delta = text.startsWith(previous) ? text.slice(previous.length) : text
  state.lastText = previous + delta
  state.streamer.append(delta)
  this.markEntryMarkdownStreaming(entryId, true)
}

export function appendMarkdownDelta(this: any, delta: string): void {
  if (!this.streamingEntryId || !this.shouldStreamMarkdown()) return
  if (!delta) return
  const entryId = this.streamingEntryId
  const state = this.ensureMarkdownStreamer(entryId)
  if (!state) return
  state.lastText += delta
  state.streamer.append(delta)
  this.markEntryMarkdownStreaming(entryId, true)
}

export function finalizeMarkdown(this: any, entryId: string | null): void {
  if (!entryId) return
  const state = this.markdownStreams.get(entryId)
  if (!state) return
  state.streamer.finalize()
  const start = Date.now()
  const maxWaitMs = 1500
  const poll = () => {
    const active = this.markdownStreams.get(entryId)
    if (!active || active !== state) return
    const blocks = [...active.streamer.getBlocks()]
    const error = active.streamer.getError()
    const allFinal = blocks.length > 0 && blocks.every((block) => block.isFinalized)
    if (error || allFinal || Date.now() - start >= maxWaitMs) {
      active.streamer.dispose()
      this.markdownStreams.delete(entryId)
      this.applyMarkdownBlocks(entryId, blocks, error ?? null, true)
      return
    }
    setTimeout(poll, 50)
  }
  setTimeout(poll, 50)
}

export function disposeAllMarkdown(this: any): void {
  for (const [entryId, state] of this.markdownStreams.entries()) {
    const blocks = [...state.streamer.getBlocks()]
    const error = state.streamer.getError()
    state.streamer.dispose()
    this.applyMarkdownBlocks(entryId, blocks, error ?? null, true)
  }
  this.markdownStreams.clear()
}

export function applyMarkdownBlocks(
  this: any,
  entryId: string,
  blocks: ReadonlyArray<Block>,
  error: string | null,
  finalized: boolean,
): void {
  const index = this.conversation.findIndex((entry: ConversationEntry) => entry.id === entryId)
  if (index === -1) return
  const existing = this.conversation[index]
  const next: ConversationEntry = {
    ...existing,
    richBlocks: [...blocks],
    markdownError: error,
    markdownStreaming: existing.phase === "streaming" && !finalized,
  }
  this.conversation[index] = next
  if (error) {
    this.markdownGloballyDisabled = true
    this.pushHint(`Rich markdown disabled: ${error}`)
  }
  if (!this.eventsScheduled) this.emitChange()
}

export function markEntryMarkdownStreaming(this: any, entryId: string, streaming: boolean): void {
  const index = this.conversation.findIndex((entry: ConversationEntry) => entry.id === entryId)
  if (index === -1) return
  const existing = this.conversation[index]
  if (existing.markdownStreaming === streaming) return
  this.conversation[index] = { ...existing, markdownStreaming: streaming }
}

export function upsertLiveSlot(
  this: any,
  id: string,
  text: string,
  color?: string,
  status: LiveSlotStatus = "pending",
  stickyMs?: number,
  summary?: string,
): void {
  this.clearLiveSlotTimer(id)
  const existing = this.liveSlots.get(id)
  if (
    existing &&
    existing.text === text &&
    existing.color === color &&
    existing.status === status &&
    existing.summary === summary
  )
    return
  const entry: LiveSlotEntry = { id, text, color, status, updatedAt: Date.now(), summary }
  this.liveSlots.set(id, entry)
  if (stickyMs && stickyMs > 0) {
    const timer = setTimeout(() => {
      const current = this.liveSlots.get(id)
      if (current && current.updatedAt === entry.updatedAt) {
        this.removeLiveSlot(id)
      }
    }, stickyMs)
    this.liveSlotTimers.set(id, timer)
  }
  if (!this.eventsScheduled) this.emitChange()
}

export function finalizeLiveSlot(
  this: any,
  id: string,
  status: LiveSlotStatus,
  fallbackText?: string,
  fallbackColor?: string,
  summary?: string,
): void {
  const existing = this.liveSlots.get(id)
  const text = fallbackText ?? existing?.text ?? "Tool completed"
  const color = fallbackColor ?? existing?.color
  this.upsertLiveSlot(id, text, color, status, 1200, summary ?? existing?.summary)
}

export function clearLiveSlotTimer(this: any, id: string): void {
  const timer = this.liveSlotTimers.get(id)
  if (timer) {
    clearTimeout(timer)
    this.liveSlotTimers.delete(id)
  }
}

export function removeLiveSlot(this: any, id: string): void {
  this.clearLiveSlotTimer(id)
  if (this.liveSlots.delete(id) && !this.eventsScheduled) {
    this.emitChange()
  }
}

export function setGuardrailNotice(this: any, summary: string, detail?: string): void {
  this.guardrailNotice = {
    id: `guard-${Date.now().toString(36)}`,
    summary,
    detail,
    timestamp: Date.now(),
    expanded: false,
  }
  if (!this.eventsScheduled) this.emitChange()
}

export function clearGuardrailNotice(this: any): void {
  if (this.guardrailNotice) {
    this.guardrailNotice = null
    if (!this.eventsScheduled) this.emitChange()
  }
}

export function toggleGuardrailNotice(this: any): void {
  if (!this.guardrailNotice) return
  this.guardrailNotice = { ...this.guardrailNotice, expanded: !this.guardrailNotice.expanded }
  this.addTool("status", `[guardrail] ${this.guardrailNotice.expanded ? "expanded" : "collapsed"}`, "error")
  this.emitChange()
}

export function dismissGuardrailNotice(this: any): void {
  if (this.guardrailNotice) {
    this.addTool("status", "[guardrail] dismissed", "error")
  }
  this.clearGuardrailNotice()
}

export function updateViewPrefs(
  this: any,
  update: Partial<TranscriptPreferences>,
  message?: string,
): void {
  this.viewPrefs = { ...this.viewPrefs, ...update }
  if (message) {
    this.pushHint(message)
  } else {
    this.emitChange()
  }
}
