import { describe, expect, it } from "vitest"
import { buildFooterV2Model } from "../FooterV2.js"

const shortcutPrefix = () => (process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM === "darwin" || process.platform === "darwin" ? "cmd" : "ctrl")
const transcriptShortcut = () => `${shortcutPrefix()}+o transcript`
const modelShortcut = () => `${shortcutPrefix()}+k model`
const tasksShortcut = () => `${shortcutPrefix()}+b tasks`
const todosShortcut = () => `${shortcutPrefix()}+t todos`

describe("buildFooterV2Model", () => {
  it("includes elapsed + interrupt cues while pending", () => {
    const model = buildFooterV2Model({
      pendingResponse: true,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "thinking", label: "thinking", tone: "info" },
      spinner: "*",
      pendingStartedAtMs: 1_000,
      lastDurationMs: null,
      nowMs: 3_100,
      todos: [{ id: "1", title: "A", status: "in_progress" }],
      tasks: [{ id: "t1", status: "running", updatedAt: 1_000 }],
      stats: {
        eventCount: 42,
        toolCount: 3,
        lastTurn: 2,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 176,
    })

    expect(model.phaseLine).toContain("thinking")
    expect(model.phaseLine).toContain("elapsed")
    expect(model.phaseLine).toContain("esc interrupt")
    expect(model.summaryLine).toContain(transcriptShortcut())
    expect(model.summaryLine).toContain(tasksShortcut())
    expect(model.summaryLine).toContain(todosShortcut())
    expect(model.summaryLine).toContain("mdl gpt-5-codex")
    expect(model.summaryLine).toContain("turn 2")
    expect(model.summaryLine).toContain("net local")
    expect(model.summaryLine).toContain("todo 1/1")
    expect(model.summaryLine).toContain("ops r1/f0/e42")
  })

  it("surfaces disconnected state with overlay controls", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: true,
      overlayLabel: "Todos",
      keymap: "codex",
      phaseLineState: { id: "disconnected", label: "disconnected", tone: "error" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: 2_200,
      nowMs: 5_000,
      todos: [],
      tasks: [{ id: "t1", status: "failed", updatedAt: 1_000 }],
      stats: {
        eventCount: 7,
        toolCount: 0,
        lastTurn: null,
        remote: true,
        model: "gpt-5-codex",
      },
      width: 64,
    })

    expect(model.tone).toBe("error")
    expect(model.phaseLine).toContain("disconnected")
    expect(model.phaseLine).toContain("esc close todos")
    expect(model.summaryLine).toContain("FOCUS Todos")
    expect(model.summaryLine).toContain("esc close todos")
  })

  it("keeps overlay focus cues visible under narrow widths", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: true,
      overlayLabel: "Tasks",
      keymap: "codex",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 5_000,
      todos: [{ id: "1", title: "A", status: "todo" }],
      tasks: [{ id: "t1", status: "failed", updatedAt: 1_000 }],
      stats: {
        eventCount: 7,
        toolCount: 0,
        lastTurn: null,
        remote: true,
        model: "gpt-5-codex",
      },
      width: 48,
    })

    expect(model.summaryLine).toContain("FOCUS Tasks")
    expect(model.summaryLine).toContain("esc close tasks")
  })

  it("normalizes startup/fallback phase labels to canonical values", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "starting", label: "starting", tone: "info" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 5_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 0,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 100,
    })

    expect(model.phaseLine).toContain("[starting]")
    expect(model.tone).toBe("info")
  })

  it("prefers canonical chip-id labels over freeform chip label text", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "responding", label: "responding", tone: "info" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 5_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 0,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 100,
    })

    expect(model.phaseLine).toContain("[responding]")
    expect(model.phaseLine).not.toContain("responding session")
  })

  it("uses overlay close copy in phase line when overlay is open even if pending", () => {
    const model = buildFooterV2Model({
      pendingResponse: true,
      mainFollowTail: true,
      overlayActive: true,
      overlayLabel: "Tasks",
      keymap: "claude",
      phaseLineState: { id: "thinking", label: "thinking", tone: "info" },
      spinner: "*",
      pendingStartedAtMs: 1_000,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 100,
    })

    expect(model.phaseLine).toContain("esc close tasks")
    expect(model.summaryLine).toContain("esc close tasks")
    expect(model.summaryLine).toContain("FOCUS Tasks")
  })

  it("keeps command rail ordering deterministic in idle mode", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 220,
    })
    const order = ["/sessions recent", "@ attach", transcriptShortcut(), modelShortcut(), "? shortcuts"]
    let cursor = -1
    for (const token of order) {
      const index = model.summaryLine.indexOf(token)
      expect(index).toBeGreaterThan(cursor)
      cursor = index
    }
  })

  it("switches ctrl shortcut labels to cmd when shortcut platform is mac", () => {
    const previous = process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM
    process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM = "darwin"
    try {
      const model = buildFooterV2Model({
        pendingResponse: false,
      mainFollowTail: true,
        overlayActive: false,
        overlayLabel: null,
        keymap: "claude",
        phaseLineState: { id: "ready", label: "ready", tone: "muted" },
        spinner: "*",
        pendingStartedAtMs: null,
        lastDurationMs: null,
        nowMs: 2_000,
        todos: [],
        tasks: [],
        stats: {
          eventCount: 5,
          toolCount: 0,
          lastTurn: null,
          remote: false,
          model: "gpt-5-codex",
        },
        width: 176,
      })
      expect(model.summaryLine).toContain("/sessions recent")
      expect(model.summaryLine).toContain("@ attach")
      expect(model.summaryLine).toContain("cmd+o transcript")
      expect(model.summaryLine).toContain("cmd+k model")
    } finally {
      if (previous == null) delete process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM
      else process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM = previous
    }
  })

  it("collapses non-critical idle hints under narrow widths", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 90,
    })
    expect(model.summaryLine).toContain("/sessions recent")
    expect(model.summaryLine).toContain(transcriptShortcut())
    expect(model.summaryLine).toContain("@ attach")
    expect(model.summaryLine).not.toContain(todosShortcut())
    expect(model.summaryLine).not.toContain(tasksShortcut())
    expect(model.summaryLine).not.toContain(modelShortcut())
  })

  it("preserves turn continuity when active-session stats are compacted", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: 900,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 66,
        toolCount: 9,
        lastTurn: 1,
        remote: false,
        model: "dev",
      },
      sessionId: "session-abcdef12",
      width: 90,
    })

    expect(model.summaryLine).toContain("mdl dev")
    expect(model.summaryLine).toContain("turn 1")
  })

  it("promotes continuation cues once a session is active", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: 900,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: 4,
        remote: false,
        model: "gpt-5-codex",
      },
      sessionId: "session-abcdef12",
      width: 176,
    })

    expect(model.summaryLine).toContain("resume /sessions")
    expect(model.summaryLine).toContain("@ attach")
    expect(model.summaryLine).toContain(transcriptShortcut())
    expect(model.summaryLine).toContain(modelShortcut())
  })

  it("surfaces transcript and session continuity in idle mode", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: 900,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: 4,
        remote: false,
        model: "gpt-5-codex",
      },
      sessionId: "session-abcdef12",
      width: 160,
    })

    expect(model.summaryLine).toContain(transcriptShortcut())
    expect(model.summaryLine).toContain("turn 4")
    expect(model.summaryLine).toContain("sess abcdef12")
  })

  it("drops overlay focus hints after overlay closes", () => {
    const openModel = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: true,
      overlayLabel: "Tasks",
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 120,
    })
    const closedModel = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: "Tasks",
      keymap: "claude",
      phaseLineState: { id: "ready", label: "ready", tone: "muted" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 120,
    })

    expect(openModel.summaryLine).toContain("FOCUS Tasks")
    expect(openModel.summaryLine).toContain("esc close tasks")
    expect(closedModel.summaryLine).not.toContain("FOCUS Tasks")
    expect(closedModel.summaryLine).not.toContain("esc close tasks")
  })

  it("renders interrupted phase as error-severity runtime state", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
      mainFollowTail: true,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "interrupted", label: "interrupted", tone: "error" },
      spinner: "*",
      pendingStartedAtMs: null,
      lastDurationMs: 1_900,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 120,
    })
    expect(model.tone).toBe("error")
    expect(model.phaseLine).toContain("[interrupted]")
    expect(model.phaseLine).toContain("last 2s")
  })
  it("shows paused follow state while a run is frozen", () => {
    const model = buildFooterV2Model({
      pendingResponse: true,
      mainFollowTail: false,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      phaseLineState: { id: "thinking", label: "thinking", tone: "info" },
      spinner: "*",
      pendingStartedAtMs: 1_000,
      lastDurationMs: null,
      nowMs: 2_000,
      todos: [],
      tasks: [],
      stats: {
        eventCount: 5,
        toolCount: 0,
        lastTurn: null,
        remote: false,
        model: "gpt-5-codex",
      },
      width: 120,
    })

    expect(model.phaseLine).toContain("follow paused")
    expect(model.summaryLine).toContain("/follow resume")
  })

})
