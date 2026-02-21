import { afterEach, describe, expect, it, vi } from "vitest"
import {
  appendMarkdownDelta,
  applyMarkdownBlocks,
  ensureMarkdownStreamer,
  shouldStreamMarkdown,
} from "../controllerStateRender.js"

describe("controllerStateRender markdown cadence + fallback", () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it("flushes short deltas according to cadence policy", () => {
    vi.useFakeTimers()
    const appendSpy = vi.fn()
    const telemetrySpy = vi.fn()
    const markStreamingSpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 10 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-1",
      markdownStreams: new Map([
        [
          "entry-1",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: telemetrySpy,
      markEntryMarkdownStreaming: markStreamingSpy,
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "abc\n")
    const immediateCalls = appendSpy.mock.calls.length
    expect(immediateCalls).toBeLessThanOrEqual(1)
    expect(markStreamingSpy).toHaveBeenCalledWith("entry-1", true)

    if (immediateCalls === 0) {
      vi.advanceTimersByTime(11)
    }

    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(appendSpy).toHaveBeenCalledWith("abc\n")
    expect(telemetrySpy).toHaveBeenCalledWith("markdownFlushes")
  })

  it("flushes immediately when chunk threshold is reached", () => {
    const appendSpy = vi.fn()
    const telemetrySpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 50 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-2",
      markdownStreams: new Map([
        [
          "entry-2",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: telemetrySpy,
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, `${"x".repeat(48)}\n`)

    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(telemetrySpy).toHaveBeenCalledWith("markdownFlushes")
  })

  it("adapts cadence at markdown boundaries when adaptive mode is enabled", () => {
    vi.useFakeTimers()
    const appendSpy = vi.fn()
    const telemetrySpy = vi.fn()
    const controller: any = {
      runtimeFlags: {
        markdownCoalescingEnabled: true,
        statusUpdateMs: 60,
        adaptiveMarkdownCadenceEnabled: true,
        adaptiveMarkdownMinChunkChars: 4,
        adaptiveMarkdownMinCoalesceMs: 5,
        adaptiveMarkdownBurstChars: 20,
      },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-adaptive",
      markdownStreams: new Map([
        [
          "entry-adaptive",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: telemetrySpy,
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "abc\n")
    if (appendSpy.mock.calls.length === 0) {
      vi.advanceTimersByTime(6)
    }
    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(appendSpy).toHaveBeenCalledWith("abc\n")
    expect(telemetrySpy).toHaveBeenCalledWith("markdownFlushes")
  })

  it("applies adaptive burst threshold and flushes immediately when burst cap is reached", () => {
    const appendSpy = vi.fn()
    const telemetrySpy = vi.fn()
    const controller: any = {
      runtimeFlags: {
        markdownCoalescingEnabled: true,
        statusUpdateMs: 80,
        adaptiveMarkdownCadenceEnabled: true,
        adaptiveMarkdownMinChunkChars: 8,
        adaptiveMarkdownMinCoalesceMs: 10,
        adaptiveMarkdownBurstChars: 10,
      },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-burst",
      markdownStreams: new Map([
        [
          "entry-burst",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: telemetrySpy,
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "abcdefghijkl\n")
    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(telemetrySpy).toHaveBeenCalledWith("markdownFlushes")
  })

  it("degrades one markdown entry on parser error without global disable side effect", () => {
    const hintSpy = vi.fn()
    const emitSpy = vi.fn()
    const controller: any = {
      markdownGloballyDisabled: false,
      conversation: [
        {
          id: "assistant-1",
          speaker: "assistant",
          text: "raw text",
          phase: "streaming",
          createdAt: 0,
        },
      ],
      pushHint: hintSpy,
      eventsScheduled: false,
      emitChange: emitSpy,
    }

    applyMarkdownBlocks.call(controller, "assistant-1", [], "worker failed", false)

    expect(controller.markdownGloballyDisabled).toBe(false)
    expect(controller.conversation[0]?.markdownError).toBe("worker failed")
    expect(hintSpy).toHaveBeenCalledWith(expect.stringContaining("Rich markdown fallback on one entry"))
    expect(emitSpy).toHaveBeenCalled()
  })

  it("emits only stable markdown boundaries during streaming deltas", () => {
    const appendSpy = vi.fn()
    const telemetrySpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 0, stableTailFlushChars: 96 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-3",
      markdownStreams: new Map([
        [
          "entry-3",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: telemetrySpy,
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "list item")
    expect(appendSpy).not.toHaveBeenCalled()

    appendMarkdownDelta.call(controller, "\n")
    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(appendSpy).toHaveBeenCalledWith("list item\n")
    expect(telemetrySpy).toHaveBeenCalledWith("markdownFlushes")
  })

  it("holds unclosed fenced code blocks until the fence closes", () => {
    const appendSpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 0, stableTailFlushChars: 96 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-fence",
      markdownStreams: new Map([
        [
          "entry-fence",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: vi.fn(),
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "```ts\nconst a = 1")
    expect(appendSpy).not.toHaveBeenCalled()

    appendMarkdownDelta.call(controller, "\n```\nnext\n")
    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(String(appendSpy.mock.calls[0]?.[0] ?? "")).toContain("```ts")
    expect(String(appendSpy.mock.calls[0]?.[0] ?? "")).toContain("const a = 1")
  })

  it("waits for newline before flushing partial markdown table rows", () => {
    const appendSpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 0, stableTailFlushChars: 96 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-table",
      markdownStreams: new Map([
        [
          "entry-table",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: vi.fn(),
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(controller, "| col | val |")
    expect(appendSpy).not.toHaveBeenCalled()

    appendMarkdownDelta.call(controller, "\n| --- | --- |\n")
    expect(appendSpy).toHaveBeenCalledTimes(1)
    expect(appendSpy).toHaveBeenCalledWith("| col | val |\n| --- | --- |\n")
  })

  it("soft-flushes long plain text tails without waiting for newline", () => {
    const appendSpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 0, stableTailFlushChars: 96 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-soft-tail",
      markdownStreams: new Map([
        [
          "entry-soft-tail",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: vi.fn(),
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(
      controller,
      "this is a long plain sentence that should stream before newline because otherwise the scrollback feels latent during long model prose chunks",
    )
    expect(appendSpy).toHaveBeenCalledTimes(1)
    const chunk = String(appendSpy.mock.calls[0]?.[0] ?? "")
    expect(chunk.length).toBeGreaterThanOrEqual(96)
    expect(chunk.endsWith("\n")).toBe(false)
  })

  it("does not soft-flush markdown-control tails before a stable boundary", () => {
    const appendSpy = vi.fn()
    const controller: any = {
      runtimeFlags: { markdownCoalescingEnabled: true, statusUpdateMs: 0 },
      viewPrefs: { richMarkdown: true },
      streamingEntryId: "entry-soft-tail-guard",
      markdownStreams: new Map([
        [
          "entry-soft-tail-guard",
          {
            streamer: { append: appendSpy },
            lastText: "",
          },
        ],
      ]),
      markdownStableState: new Map(),
      markdownPendingDeltas: new Map(),
      bumpRuntimeTelemetry: vi.fn(),
      markEntryMarkdownStreaming: vi.fn(),
      shouldStreamMarkdown() {
        return shouldStreamMarkdown.call(this)
      },
      ensureMarkdownStreamer(entryId: string) {
        return ensureMarkdownStreamer.call(this, entryId)
      },
    }

    appendMarkdownDelta.call(
      controller,
      "| markdown | control | tail | that | intentionally | lacks | newline | but | includes | table | pipes |",
    )
    expect(appendSpy).not.toHaveBeenCalled()
  })
})
