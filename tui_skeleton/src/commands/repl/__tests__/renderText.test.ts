import { describe, it, expect } from "vitest"
import type { ReplState } from "../controller.js"
import { renderStateToText } from "../renderText.js"

const baseState = (): ReplState => ({
  sessionId: "session-123",
  status: "Ready",
  pendingResponse: false,
  mode: "build",
  permissionMode: "prompt",
  conversation: [
    { id: "conv-1", speaker: "user", text: "Hello world", phase: "final", createdAt: 0 },
    { id: "conv-2", speaker: "assistant", text: "Hi there!", phase: "final", createdAt: 1 },
  ],
  toolEvents: [
    { id: "tool-1", kind: "call", text: "[call] example-tool", status: "pending", createdAt: 0 },
    { id: "tool-2", kind: "completion", text: "[completion] status completed", status: "success", createdAt: 1 },
  ],
  hints: ["Use /help for commands."],
  stats: { eventCount: 4, toolCount: 1, lastTurn: 2, remote: false, model: "test/model" },
  modelMenu: { status: "hidden" },
  skillsMenu: { status: "hidden" },
  inspectMenu: { status: "hidden" },
  liveSlots: [],
  rawEvents: [],
  completionReached: true,
  completionSeen: true,
  lastCompletion: { completed: true, summary: null },
  disconnected: false,
  viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: false },
  rewindMenu: { status: "hidden" },
  todos: [],
  tasks: [],
  ctreeSnapshot: null,
  ctreeTree: null,
  ctreeTreeStatus: "idle",
  ctreeTreeError: null,
  ctreeStage: "FROZEN",
  ctreeIncludePreviews: false,
  ctreeSource: "auto",
  ctreeUpdatedAt: null,
})

describe("renderStateToText", () => {
  it("renders core sections without colors by default", () => {
    const snapshot = renderStateToText(baseState())
    expect(snapshot).toContain("session-123")
    expect(snapshot).toContain("Ready")
    expect(snapshot).toContain("USER")
    expect(snapshot).toContain("ASSISTANT")
    expect(snapshot).toContain("tools 1")
    expect(snapshot).toContain("Use /help for commands.")
  })

  it("includes model menu entries when visible", () => {
    const state: ReplState = {
      ...baseState(),
      modelMenu: {
        status: "ready",
        items: [
          { label: "Model A", value: "a", provider: "openrouter", isDefault: true, isCurrent: true },
          { label: "Model B", value: "b", provider: "openai" },
        ],
      },
    }
    const snapshot = renderStateToText(state, { includeModelMenu: true })
    expect(snapshot).toContain("=== Model Menu ===")
    expect(snapshot).toContain("Model A")
    expect(snapshot).toContain("Model B")
  })

  it("omits hints when includeHints is false", () => {
    const snapshot = renderStateToText(baseState(), { includeHints: false })
    expect(snapshot).not.toContain("Use /help for commands.")
  })

  it("adds streaming entry when present", () => {
    const state: ReplState = {
      ...baseState(),
      conversation: [
        { id: "conv-1", speaker: "user", text: "Hello world", phase: "final", createdAt: 0 },
        { id: "conv-2", speaker: "assistant", text: "Typing...", phase: "streaming", createdAt: 1 },
      ],
    }
    const snapshot = renderStateToText(state)
    expect(snapshot).toContain("Typing...")
  })

  it("can suppress streaming entry when includeStreamingTail is false", () => {
    const state: ReplState = {
      ...baseState(),
      conversation: [{ id: "conv-1", speaker: "assistant", text: "Typing...", phase: "streaming", createdAt: 0 }],
    }
    const snapshot = renderStateToText(state, { includeStreamingTail: false })
    expect(snapshot).toContain("No conversation yet")
  })

  it("uses ASCII ellipsis when asciiOnly is true", () => {
    const state: ReplState = {
      ...baseState(),
      conversation: [],
      pendingResponse: true,
    }
    const snapshot = renderStateToText(state, { includeHeader: false, includeStatus: false, colors: false, asciiOnly: true })
    expect(snapshot).toContain("Assistant is thinking...")
  })

  it("shows compact transcript hint when virtualization active", () => {
    const state: ReplState = {
      ...baseState(),
      viewPrefs: { collapseMode: "auto", virtualization: "compact", richMarkdown: false },
      conversation: Array.from({ length: 30 }).map((_, idx) => ({
        id: `conv-${idx}`,
        speaker: "assistant",
        text: `Line ${idx}`,
        phase: "final" as const,
        createdAt: idx,
      })),
    }
    const snapshot = renderStateToText(state, { includeHeader: false, includeStatus: false })
    expect(snapshot).toContain("Compact transcript mode active")
  })

  it("renders rich markdown blocks when enabled", () => {
    const state: ReplState = {
      ...baseState(),
      viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: true },
      conversation: [
        {
          id: "conv-1",
          speaker: "assistant",
          text: "fallback",
          phase: "final",
          createdAt: 0,
          richBlocks: [
            { id: "b1", type: "heading", isFinalized: true, payload: { raw: "Sample Title", meta: { level: 2 } } },
            { id: "b2", type: "paragraph", isFinalized: true, payload: { raw: "Paragraph **text**" } },
            { id: "b3", type: "code", isFinalized: true, payload: { raw: "```diff\n+add\n-remove\n```", meta: { lang: "diff" } } },
          ],
        },
      ],
    }
    const snapshot = renderStateToText(state, { includeHeader: false, includeStatus: false, colors: false })
    expect(snapshot).toContain("## Sample Title")
    expect(snapshot).toContain("Paragraph **text**")
    expect(snapshot).toContain("+add")
    expect(snapshot).toContain("-remove")
  })

  it("shows markdown error banner on assistant entry", () => {
    const state: ReplState = {
      ...baseState(),
      viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: true },
      conversation: [
        {
          id: "conv-1",
          speaker: "assistant",
          text: "fallback",
          phase: "final",
          createdAt: 0,
          markdownError: "worker failed",
        },
      ],
    }
    const snapshot = renderStateToText(state, { includeHeader: false, includeStatus: false, colors: false })
    expect(snapshot).toContain("rich markdown disabled: worker failed")
  })
})
