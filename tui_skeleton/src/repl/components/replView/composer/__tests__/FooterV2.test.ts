import { describe, expect, it } from "vitest"
import { buildFooterV2Model } from "../FooterV2.js"

describe("buildFooterV2Model", () => {
  it("includes elapsed + interrupt cues while pending", () => {
    const model = buildFooterV2Model({
      pendingResponse: true,
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
      width: 128,
    })

    expect(model.phaseLine).toContain("thinking")
    expect(model.phaseLine).toContain("elapsed")
    expect(model.phaseLine).toContain("esc interrupt")
    expect(model.summaryLine).toContain("ctrl+b tasks")
    expect(model.summaryLine).toContain("ctrl+t todos")
    expect(model.summaryLine).toContain("mdl gpt-5-codex")
    expect(model.summaryLine).toContain("net local")
    expect(model.summaryLine).toContain("todo 1/1")
    expect(model.summaryLine).toContain("ops r1/f0/e42")
  })

  it("surfaces disconnected state with overlay controls", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
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
    const order = ["/ commands", "@ files", "ctrl+b tasks", "ctrl+t todos", "ctrl+k model", "? shortcuts"]
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
        width: 120,
      })
      expect(model.summaryLine).toContain("cmd+b tasks")
      expect(model.summaryLine).toContain("cmd+t todos")
      expect(model.summaryLine).toContain("cmd+k model")
    } finally {
      if (previous == null) delete process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM
      else process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM = previous
    }
  })

  it("collapses non-critical idle hints under narrow widths", () => {
    const model = buildFooterV2Model({
      pendingResponse: false,
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
    expect(model.summaryLine).toContain("ctrl+b tasks")
    expect(model.summaryLine).toContain("ctrl+t todos")
    expect(model.summaryLine).toContain("ctrl+k model")
    expect(model.summaryLine).toContain("?")
    expect(model.summaryLine).not.toContain("/ commands")
    expect(model.summaryLine).not.toContain("@ files")
  })

  it("drops overlay focus hints after overlay closes", () => {
    const openModel = buildFooterV2Model({
      pendingResponse: false,
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
})
