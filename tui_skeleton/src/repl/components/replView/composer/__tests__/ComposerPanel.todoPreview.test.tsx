import React from "react"
import { describe, expect, it, vi } from "vitest"
import { render } from "ink-testing-library"
import { ComposerPanel } from "../ComposerPanel.js"
import { buildTodoPreviewModel } from "../todoPreview.js"
import { buildThinkingPreviewModel } from "../thinkingPreview.js"
import { ASCII_ONLY } from "../../theme.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const buildContext = (overrides: Record<string, unknown> = {}) => ({
  claudeChrome: true,
  modelMenu: { status: "hidden" },
  pendingClaudeStatus: null,
  runtimeStatusChips: [],
  thinkingPreviewModel: null,
  todoPreviewModel: null,
  promptRule: "----------------------------------------",
  composerPromptPrefix: ">",
  composerPlaceholderClassic: "",
  composerPlaceholderClaude: "",
  composerShowTopRule: false,
  composerShowBottomRule: false,
  input: "",
  cursor: 0,
  inputLocked: false,
  attachments: [],
  fileMentions: [],
  inputMaxVisibleLines: 6,
  handleLineEditGuarded: vi.fn(),
  handleLineSubmit: vi.fn(),
  handleAttachment: vi.fn(),
  overlayActive: false,
  filePickerActive: false,
  fileIndexMeta: { fileCount: 0, dirCount: 0, scannedDirs: 0, queuedDirs: 0, status: "idle", truncated: false },
  fileMenuMode: "fuzzy",
  filePicker: { status: "hidden" },
  fileMenuRows: [],
  fileMenuHasLarge: false,
  fileMenuWindow: { start: 0, end: 0, lineCount: 0, hiddenAbove: 0, hiddenBelow: 0, items: [] },
  fileMenuIndex: 0,
  fileMenuNeedlePending: false,
  filePickerQueryParts: { cwd: ".", raw: "" },
  filePickerConfig: { mode: "fuzzy", maxResults: 20, maxQueryParts: 4 },
  fileMentionConfig: { maxInlineBytesPerFile: 64_000, maxTotalInlineBytes: 1_000_000, mode: "inline" },
  selectedFileIsLarge: false,
  columnWidth: 80,
  suggestions: [],
  suggestionWindow: { start: 0, end: 0, lineCount: 0, hiddenAbove: 0, hiddenBelow: 0 },
  suggestionPrefix: "",
  suggestionLayout: { commandWidth: 20, totalWidth: 80, labelWidth: 60 },
  buildSuggestionLines: vi.fn(() => []),
  suggestIndex: 0,
  activeSlashQuery: null,
  hintNodes: [],
  shortcutHintNodes: [],
  footerV2Enabled: false,
  keymap: "claude",
  pendingResponse: false,
  disconnected: false,
  status: "ready",
  overlayLabel: null,
  spinner: ".",
  pendingStartedAtMs: null,
  lastDurationMs: null,
  clockNowMs: 0,
  todos: [],
  tasks: [],
  stats: { model: "gpt-5", remote: false, eventCount: 0, toolCount: 0, usage: null, lastTurn: null },
  ...overrides,
})

describe("ComposerPanel todo preview", () => {
  it("renders todo preview above the input when enabled (claudeChrome)", async () => {
    const preview = buildTodoPreviewModel(
      { revision: 1, updatedAt: 0, itemsById: { t1: { id: "t1", title: "Write tests", status: "todo" } }, order: ["t1"] },
      { maxItems: 7 },
    )
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          todoPreviewModel: preview,
        })}
      />,
    )
    await flush()

    const frame = lastFrame() ?? ""
    expect(frame).toContain("TODOs: 0/1")
    expect(frame).toContain(`${ASCII_ONLY ? "[ ]" : "☐"} Write tests`)
  })

  it("renders thinking preview + status chip above input", async () => {
    const thinkingPreview = buildThinkingPreviewModel(
      {
        id: "tp-1",
        mode: "summary",
        lifecycle: "open",
        startedAt: 0,
        updatedAt: 0,
        closedAt: null,
        eventCount: 2,
        lines: ["map tasks", "verify output"],
        truncated: false,
        sourceEventTypes: ["assistant.thought_summary.delta"],
      },
      null,
      { maxLines: 5, showWhenClosed: true, cols: 80 },
    )
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          runtimeStatusChips: [{ id: "thinking", label: "thinking", tone: "info" }],
          thinkingPreviewModel: thinkingPreview,
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).toContain("[thinking]")
    expect(frame).toContain("[task tree] thinking")
    expect(frame).toContain("map tasks")
  })

  it("suppresses duplicate status chip line when FooterV2 is enabled", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          footerV2Enabled: true,
          runtimeStatusChips: [{ id: "responding", label: "responding", tone: "info" }],
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect((frame.match(/\[responding\]/g) ?? []).length).toBe(1)
  })
})
