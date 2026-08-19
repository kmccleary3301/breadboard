import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const previousForceColor = process.env.FORCE_COLOR
const previousNoColor = process.env.NO_COLOR

const trueColorSequence = (hex: string, text: string): string => {
  const normalized = hex.slice(1)
  const red = Number.parseInt(normalized.slice(0, 2), 16)
  const green = Number.parseInt(normalized.slice(2, 4), 16)
  const blue = Number.parseInt(normalized.slice(4, 6), 16)
  return `\u001b[38;2;${red};${green};${blue}m${text}\u001b[39m`
}

const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  process.env.FORCE_COLOR = "3"
  delete process.env.NO_COLOR
  vi.resetModules()
})

afterEach(() => {
  if (previousForceColor == null) delete process.env.FORCE_COLOR
  else process.env.FORCE_COLOR = previousForceColor
  if (previousNoColor == null) delete process.env.NO_COLOR
  else process.env.NO_COLOR = previousNoColor
  vi.resetModules()
})

const buildComposerContext = (overrides: Record<string, unknown> = {}) => ({
  claudeChrome: true,
  scrollbackMode: false,
  todosOpen: false,
  tasksOpen: false,
  todos: [],
  todoRows: [],
  todoScroll: 0,
  todoMaxScroll: 0,
  todoViewportRows: 8,
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
  handleEditorKeys: vi.fn(),
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
  mainFollowTail: true,
  conversationLength: 0,
  phaseLineState: { id: "ready", label: "ready", tone: "muted" },
  disconnected: false,
  status: "ready",
  overlayLabel: null,
  spinner: ".",
  pendingStartedAtMs: null,
  lastDurationMs: null,
  clockNowMs: 0,
  tasks: [],
  stats: { model: "gpt-5", remote: false, eventCount: 0, toolCount: 0, usage: null, lastTurn: null },
  transcriptViewerOpen: false,
  transcriptSearchOpen: false,
  scrollbackSuppressIdlePlaceholder: false,
  queuedPrompt: null,
  shortcutsOpen: false,
  ...overrides,
})

describe("todo status presentation wiring", () => {
  it("renders Claude todo headers with the panel color", async () => {
    const [{ render }, React, { ComposerPanel }, { SEMANTIC_COLORS }] = await Promise.all([
      import("ink-testing-library"),
      import("react"),
      import("../ComposerPanel.js"),
      import("../../../../designSystem.js"),
    ])
    const view = render(
      React.createElement(ComposerPanel, {
        context: buildComposerContext({
          todosOpen: true,
          todos: [{ id: "1", title: "Working", status: "in_progress" }],
          todoRows: [{ kind: "header", label: "In Progress", status: "in_progress" }],
        }),
      }),
    )
    await flush()

    expect(view.lastFrame()).toContain(trueColorSequence(SEMANTIC_COLORS.info, "In Progress"))
    view.unmount()
  })

  it("renders classic todo headers with the panel color", async () => {
    const [{ render }, React, { buildModalStack }, { SEMANTIC_COLORS }] = await Promise.all([
      import("ink-testing-library"),
      import("react"),
      import("../../overlays/buildModalStack.js"),
      import("../../../../designSystem.js"),
    ])
    const hidden = { status: "hidden" }
    const modal = buildModalStack({
      confirmState: hidden,
      shortcutsOpen: false,
      claudeChrome: false,
      isBreadboardProfile: false,
      columnWidth: 80,
      rowCount: 40,
      scrollbackMode: false,
      PANEL_WIDTH: 60,
      shortcutLines: [],
      paletteState: hidden,
      modelMenu: hidden,
      skillsMenu: hidden,
      rewindMenu: hidden,
      inspectMenu: hidden,
      todosOpen: true,
      recentSessionsOpen: false,
      todoScroll: 0,
      todoMaxScroll: 0,
      todoRows: [{ kind: "header", label: "In Progress", status: "in_progress" }],
      todoViewportRows: 8,
      todos: [{ id: "1", title: "Working", status: "in_progress" }],
      tasks: [],
    }).find((entry) => entry.id === "todos")
    expect(modal).toBeDefined()
    const view = render(React.createElement(React.Fragment, null, modal?.render()))
    await flush()

    expect(view.lastFrame()).toContain(trueColorSequence(SEMANTIC_COLORS.info, "In Progress"))
    view.unmount()
  })

  it("renders preview items with the preview color", async () => {
    const [{ render }, React, { TodoPreviewWidget }, { SEMANTIC_COLORS }] = await Promise.all([
      import("ink-testing-library"),
      import("react"),
      import("../TodoPreviewWidget.js"),
      import("../../../../designSystem.js"),
    ])
    const view = render(
      React.createElement(TodoPreviewWidget, {
        model: {
          header: "",
          headerLine: "",
          doneCount: 0,
          totalCount: 1,
          items: [{ id: "1", label: "☐ Working", status: "in_progress" }],
          style: "minimal",
          hint: null,
          frameWidth: null,
        },
      }),
    )
    await flush()

    expect(view.lastFrame()).toContain(trueColorSequence(SEMANTIC_COLORS.warning, "☐ Working"))
    view.unmount()
  })
})
