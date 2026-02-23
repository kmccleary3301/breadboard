import type { Block } from "@stream-mdx/core/types"
import { MarkdownStreamer } from "../../markdown/streamer.js"
import { scanStableBoundary } from "../../repl/markdown/stableBoundaryScanner.js"
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
const DEFAULT_MARKDOWN_MIN_CHUNK_CHARS = 24
const DEFAULT_STABLE_TAIL_FLUSH_CHARS = 96
const ENV_MARKDOWN_COALESCE_MS = Number(process.env.BREADBOARD_MARKDOWN_COALESCE_MS ?? "")
const ENV_MARKDOWN_MIN_CHUNK = Number(process.env.BREADBOARD_MARKDOWN_MIN_CHUNK_CHARS ?? "")
const ENV_STABLE_TAIL_FLUSH = Number(process.env.BREADBOARD_MARKDOWN_STABLE_TAIL_FLUSH_CHARS ?? "")

export function shouldStreamMarkdown(this: any): boolean {
  const coalescingEnabled =
    this.runtimeFlags?.markdownCoalescingEnabled === undefined
      ? true
      : Boolean(this.runtimeFlags?.markdownCoalescingEnabled)
  return STREAM_MARKDOWN && coalescingEnabled && this.viewPrefs.richMarkdown
}

const LIVE_TOKENIZATION = process.env.BREADBOARD_STREAM_MDX_LIVE_TOKENS === "1"

const resolveCadence = (controller: any): { coalesceMs: number; minChunkChars: number } => {
  const fromFlags = Number(controller.runtimeFlags?.statusUpdateMs ?? 120)
  const coalesceMs = Number.isFinite(ENV_MARKDOWN_COALESCE_MS)
    ? Math.max(0, Math.round(ENV_MARKDOWN_COALESCE_MS))
    : Math.max(0, Math.round(fromFlags))
  const minChunkChars = Number.isFinite(ENV_MARKDOWN_MIN_CHUNK)
    ? Math.max(1, Math.round(ENV_MARKDOWN_MIN_CHUNK))
    : DEFAULT_MARKDOWN_MIN_CHUNK_CHARS
  return { coalesceMs, minChunkChars }
}

const resolveStableTailFlushChars = (controller: any): number => {
  const runtimeOverride = Number(controller?.runtimeFlags?.stableTailFlushChars)
  if (Number.isFinite(runtimeOverride)) {
    return Math.max(0, Math.round(runtimeOverride))
  }
  if (!Number.isFinite(ENV_STABLE_TAIL_FLUSH)) return DEFAULT_STABLE_TAIL_FLUSH_CHARS
  return Math.max(0, Math.round(ENV_STABLE_TAIL_FLUSH))
}

const SOFT_BOUNDARY_PUNCTUATION = [". ", "! ", "? ", "; ", ": ", ", "]

const hasMarkdownControlMarkers = (text: string): boolean => {
  if (!text) return false
  if (text.includes("```") || text.includes("~~~")) return true
  if (text.includes("|")) return true
  if (/\n\s*[-*+]\s/.test(text)) return true
  return false
}

const resolveSoftBoundary = (
  pending: string,
  threshold: number,
): number | null => {
  if (pending.length < threshold) return null
  const search = pending.slice(0, Math.min(pending.length, threshold + 64))
  let best = -1
  for (const token of SOFT_BOUNDARY_PUNCTUATION) {
    const idx = search.lastIndexOf(token)
    if (idx > best) {
      best = idx + token.length
    }
  }
  if (best < Math.floor(threshold * 0.66)) {
    const whitespace = Math.max(search.lastIndexOf(" "), search.lastIndexOf("\t"))
    if (whitespace > best) best = whitespace + 1
  }
  if (best >= Math.floor(threshold * 0.5)) return best
  return threshold
}

const hasMarkdownBoundary = (delta: string): boolean => {
  if (!delta) return false
  if (delta.includes("\n")) return true
  if (delta.includes("```") || delta.includes("~~~")) return true
  if (/\|\s*[-:]/.test(delta)) return true
  if (/^\s*[-*+]\s/m.test(delta)) return true
  return false
}

const resolveAdaptiveCadence = (
  controller: any,
  base: { coalesceMs: number; minChunkChars: number },
  delta: string,
): { coalesceMs: number; minChunkChars: number; adjusted: boolean } => {
  if (controller.runtimeFlags?.adaptiveMarkdownCadenceEnabled !== true) {
    return { ...base, adjusted: false }
  }
  const minChunkFloor = Math.max(1, Number(controller.runtimeFlags?.adaptiveMarkdownMinChunkChars ?? 8))
  const minCoalesceFloor = Math.max(0, Number(controller.runtimeFlags?.adaptiveMarkdownMinCoalesceMs ?? 12))
  const burstChars = Math.max(1, Number(controller.runtimeFlags?.adaptiveMarkdownBurstChars ?? 48))
  const boundaryBoost = hasMarkdownBoundary(delta)
  const burstBoost = delta.length >= burstChars
  let minChunkChars = base.minChunkChars
  let coalesceMs = base.coalesceMs
  if (boundaryBoost) {
    minChunkChars = Math.min(minChunkChars, minChunkFloor)
    coalesceMs = Math.min(coalesceMs, minCoalesceFloor)
  }
  if (burstBoost) {
    minChunkChars = Math.min(minChunkChars, Math.max(minChunkFloor, Math.floor(base.minChunkChars / 2)))
    coalesceMs = Math.min(coalesceMs, minCoalesceFloor)
  }
  const adjusted = minChunkChars !== base.minChunkChars || coalesceMs !== base.coalesceMs
  return { coalesceMs, minChunkChars, adjusted }
}

const clearMarkdownQueueTimer = (controller: any, entryId: string): void => {
  const state = controller.markdownPendingDeltas.get(entryId)
  if (state?.timer) {
    clearTimeout(state.timer)
    state.timer = null
  }
}

const flushMarkdownQueue = (controller: any, entryId: string): void => {
  const pending = controller.markdownPendingDeltas.get(entryId)
  if (!pending || !pending.buffer) return
  const stream = controller.markdownStreams.get(entryId)
  if (!stream) {
    pending.buffer = ""
    return
  }
  const chunk = pending.buffer
  pending.buffer = ""
  stream.streamer.append(chunk)
  controller.bumpRuntimeTelemetry?.("markdownFlushes")
}

const queueMarkdownDelta = (controller: any, entryId: string, delta: string): void => {
  const baseCadence = resolveCadence(controller)
  const cadence = resolveAdaptiveCadence(controller, baseCadence, delta)
  if (cadence.adjusted) {
    controller.bumpRuntimeTelemetry?.("adaptiveCadenceAdjustments")
  }
  const pending =
    controller.markdownPendingDeltas.get(entryId) ??
    (() => {
      const seed = { buffer: "", timer: null as NodeJS.Timeout | null }
      controller.markdownPendingDeltas.set(entryId, seed)
      return seed
    })()
  pending.buffer += delta
  if (pending.buffer.length >= cadence.minChunkChars || cadence.coalesceMs <= 0) {
    clearMarkdownQueueTimer(controller, entryId)
    flushMarkdownQueue(controller, entryId)
    return
  }
  if (!pending.timer) {
    pending.timer = setTimeout(() => {
      clearMarkdownQueueTimer(controller, entryId)
      flushMarkdownQueue(controller, entryId)
    }, cadence.coalesceMs)
  }
}

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
  this.appendMarkdownDelta(delta)
}

export function appendMarkdownDelta(this: any, delta: string): void {
  if (!this.streamingEntryId || !this.shouldStreamMarkdown()) return
  if (!delta) return
  const entryId = this.streamingEntryId
  const state = this.ensureMarkdownStreamer(entryId)
  if (!state) return
  const stableSeed =
    this.markdownStableState.get(entryId) ??
    ({
      fullText: state.lastText ?? "",
      emittedLen: (state.lastText ?? "").length,
      stableBoundaryLen: (state.lastText ?? "").length,
      state: { inFence: false, fenceMarker: null, inList: false, inTable: false },
    } as const)
  const fullText = `${stableSeed.fullText}${delta}`
  const scan = scanStableBoundary(fullText, stableSeed.state)
  let emitTarget = Math.max(stableSeed.emittedLen, scan.stableBoundaryLen)
  if (emitTarget === stableSeed.emittedLen) {
    const softThreshold = resolveStableTailFlushChars(this)
    const pending = fullText.slice(stableSeed.emittedLen)
    const allowSoftBoundary =
      softThreshold > 0 &&
      pending.length >= softThreshold &&
      !scan.state.inFence &&
      !scan.state.inTable &&
      !scan.state.inList &&
      !hasMarkdownControlMarkers(pending)
    if (allowSoftBoundary) {
      const softBoundary = resolveSoftBoundary(pending, softThreshold)
      if (softBoundary && softBoundary > 0) {
        emitTarget = stableSeed.emittedLen + softBoundary
      }
    }
  }
  const stableDelta = emitTarget > stableSeed.emittedLen ? fullText.slice(stableSeed.emittedLen, emitTarget) : ""
  this.markdownStableState.set(entryId, {
    fullText,
    emittedLen: emitTarget,
    stableBoundaryLen: scan.stableBoundaryLen,
    state: scan.state,
  })
  state.lastText = fullText.slice(0, emitTarget)
  if (stableDelta) queueMarkdownDelta(this, entryId, stableDelta)
  this.markEntryMarkdownStreaming(entryId, true)
}

export function finalizeMarkdown(this: any, entryId: string | null): void {
  if (!entryId) return
  const stable = this.markdownStableState.get(entryId)
  if (stable && stable.fullText.length > stable.emittedLen) {
    const tailDelta = stable.fullText.slice(stable.emittedLen)
    if (tailDelta) queueMarkdownDelta(this, entryId, tailDelta)
  }
  clearMarkdownQueueTimer(this, entryId)
  flushMarkdownQueue(this, entryId)
  this.markdownPendingDeltas.delete(entryId)
  this.markdownStableState.delete(entryId)
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
  for (const [entryId, stable] of this.markdownStableState.entries()) {
    if (stable.fullText.length > stable.emittedLen) {
      const tailDelta = stable.fullText.slice(stable.emittedLen)
      if (tailDelta) queueMarkdownDelta(this, entryId, tailDelta)
    }
  }
  for (const [entryId] of this.markdownPendingDeltas.entries()) {
    clearMarkdownQueueTimer(this, entryId)
    flushMarkdownQueue(this, entryId)
  }
  this.markdownPendingDeltas.clear()
  this.markdownStableState.clear()
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
    this.pushHint(`Rich markdown fallback on one entry: ${error}`)
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
  const now = this.clock?.now?.() ?? Date.now()
  const entry: LiveSlotEntry = { id, text, color, status, updatedAt: now, summary }
  this.liveSlots.set(id, entry)
  if (stickyMs && stickyMs > 0) {
    const schedule = this.clock?.setTimeout?.bind(this.clock) ?? setTimeout
    const timer = schedule(() => {
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
    const clear = this.clock?.clearTimeout?.bind(this.clock) ?? clearTimeout
    clear(timer)
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
  const now = this.clock?.now?.() ?? Date.now()
  this.guardrailNotice = {
    id: `guard-${now.toString(36)}`,
    summary,
    detail,
    timestamp: now,
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
