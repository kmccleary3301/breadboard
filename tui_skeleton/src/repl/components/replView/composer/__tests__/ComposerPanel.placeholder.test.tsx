import React from "react"
import { describe, expect, it, vi } from "vitest"
import { render } from "ink-testing-library"
import { ComposerPanel } from "../ComposerPanel.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const countBlankRowsBeforeReadyFooter = (frame: string): number => {
  const rows = frame.split("\n")
  const footerIndex = rows.findIndex((row) => row.includes("[ready]"))
  if (footerIndex < 0) return -1
  let blankRows = 0
  for (let index = footerIndex - 1; index >= 0; index -= 1) {
    if (rows[index].trim().length > 0) break
    blankRows += 1
  }
  return blankRows
}

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
  footerV2Enabled: true,
  keymap: "claude",
  pendingResponse: false,
  phaseLineState: { id: "ready", label: "ready", tone: "muted" },
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

describe("ComposerPanel placeholder activity", () => {
  it("shows the idle Claude placeholder only when the composer is truly ready", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          composerPlaceholderClaude: 'Try "refactor <filepath>"',
        })}
      />,
    )
    await flush()
    expect(lastFrame() ?? "").toContain('Try "refactor <filepath>"')
  })

  it("suppresses the idle Claude placeholder while a response is still active", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          composerPlaceholderClaude: 'Try "refactor <filepath>"',
          pendingResponse: true,
          phaseLineState: { id: "responding", label: "responding", tone: "info" },
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).not.toContain('Try "refactor <filepath>"')
    expect(frame).toContain("[responding]")
  })

  it("keeps the empty idle composer row visible after scrollback has committed history", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          scrollbackMode: true,
          conversationLength: 1,
          scrollbackSuppressIdlePlaceholder: true,
          composerPlaceholderClassic: "Type your request…",
          composerPlaceholderClaude: 'Try "refactor <filepath>"',
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).not.toContain('Try "refactor <filepath>"')
    expect(frame).toContain("Type your request")
    expect(frame).toContain(">")
  })

  it("keeps FooterV2 visible after scrollback has committed history", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          scrollbackMode: true,
          conversationLength: 1,
          scrollbackSuppressIdlePlaceholder: true,
          footerV2Enabled: true,
          composerPlaceholderClassic: "Type your request…",
          composerPlaceholderClaude: 'Try "refactor <filepath>"',
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).toContain(">")
    expect(frame).toContain("Type your request")
    expect(frame).toContain("[ready]")
    expect(frame).toContain("/sessions recent")
  })

  it("keeps the empty composer visible after an ordinary model error", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          scrollbackMode: true,
          conversationLength: 1,
          scrollbackSuppressIdlePlaceholder: true,
          composerPlaceholderClassic: "Type your request…",
          composerPlaceholderClaude: 'Try "refactor <filepath>"',
          status: "Error received",
          phaseLineState: { id: "ready", label: "ready", tone: "muted" },
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).toContain(">")
    expect(frame).toContain("Type your request")
    expect(frame).toContain("[ready]")
  })

  it("suppresses separator rules in scrollback mode so rules cannot collide with the prompt", async () => {
    const { lastFrame } = render(
      <ComposerPanel
        context={buildContext({
          scrollbackMode: true,
          composerShowTopRule: true,
          composerShowBottomRule: true,
        })}
      />,
    )
    await flush()
    const frame = lastFrame() ?? ""
    expect(frame).toContain('Try "refactor <filepath>"')
    expect(frame).not.toContain("----------------------------------------")
  })
})
