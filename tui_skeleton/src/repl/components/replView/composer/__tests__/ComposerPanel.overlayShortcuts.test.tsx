import React from "react"
import { Text } from "ink"
import { describe, expect, it, vi } from "vitest"
import { render } from "ink-testing-library"
import { ComposerPanel } from "../ComposerPanel.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const buildContext = (overrides: Record<string, unknown> = {}) => ({
  claudeChrome: true,
  modelMenu: { status: "hidden" },
  pendingClaudeStatus: null,
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
  overlayActive: true,
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
  todosOpen: false,
  tasksOpen: false,
  todos: [],
  todoRows: [],
  todoScroll: 0,
  todoMaxScroll: 0,
  todoViewportRows: 0,
  suggestions: [],
  suggestionWindow: { start: 0, end: 0, lineCount: 0, hiddenAbove: 0, hiddenBelow: 0 },
  suggestionPrefix: "",
  suggestionLayout: { commandWidth: 20, totalWidth: 80, labelWidth: 60 },
  buildSuggestionLines: vi.fn(() => []),
  suggestIndex: 0,
  activeSlashQuery: null,
  hintNodes: [],
  shortcutHintNodes: [<Text key="shortcuts">  ? for shortcuts</Text>],
  ...overrides,
})

describe("ComposerPanel overlay shortcuts", () => {
  it("keeps shortcut hint line visible while todos overlay is open in claude chrome", async () => {
    const { lastFrame } = render(<ComposerPanel context={buildContext({ todosOpen: true })} />)
    await flush()
    expect(lastFrame() ?? "").toContain("? for shortcuts")
  })

  it("keeps shortcut hint line visible while tasks overlay is open in claude chrome", async () => {
    const { lastFrame } = render(<ComposerPanel context={buildContext({ tasksOpen: true })} />)
    await flush()
    expect(lastFrame() ?? "").toContain("? for shortcuts")
  })

  it("hides shortcut hint line for unrelated overlays", async () => {
    const { lastFrame } = render(<ComposerPanel context={buildContext()} />)
    await flush()
    expect(lastFrame() ?? "").not.toContain("? for shortcuts")
  })
})
