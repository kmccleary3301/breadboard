import React from "react"
import { describe, expect, it, vi } from "vitest"
import { render } from "ink-testing-library"
import { ComposerPanel } from "../ComposerPanel.js"
import { buildTodoPreviewModel } from "../todoPreview.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe("ComposerPanel todo preview", () => {
  it("renders todo preview above the input when enabled (claudeChrome)", async () => {
    const preview = buildTodoPreviewModel([{ id: "t1", title: "Write tests", status: "todo" }], { maxItems: 7 })
    const { lastFrame } = render(
      <ComposerPanel
        context={{
          claudeChrome: true,
          modelMenu: { status: "hidden" },
          pendingClaudeStatus: null,
          todoPreviewModel: preview,
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
        }}
      />,
    )
    await flush()

    const frame = lastFrame() ?? ""
    expect(frame).toContain("TODOs: 0/1")
    expect(frame).toContain("[ ] Write tests")
  })
})

