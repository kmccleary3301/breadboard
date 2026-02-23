import { describe, expect, it } from "vitest"
import { buildFooterV2Model } from "../FooterV2.js"

describe("buildFooterV2Model", () => {
  it("includes elapsed + interrupt cues while pending", () => {
    const model = buildFooterV2Model({
      pendingResponse: true,
      disconnected: false,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      status: "Assistant thinking…",
      runtimeStatusChips: [{ id: "thinking", label: "thinking", tone: "info" }],
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
      disconnected: true,
      overlayActive: true,
      overlayLabel: "Todos",
      keymap: "codex",
      status: "Reconnecting",
      runtimeStatusChips: [],
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
      disconnected: false,
      overlayActive: true,
      overlayLabel: "Tasks",
      keymap: "codex",
      status: "Ready",
      runtimeStatusChips: [],
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
      disconnected: false,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      status: "Starting session...",
      runtimeStatusChips: [],
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
      disconnected: false,
      overlayActive: false,
      overlayLabel: null,
      keymap: "claude",
      status: "Ready",
      runtimeStatusChips: [{ id: "responding", label: "Responding session...", tone: "info" }],
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
      disconnected: false,
      overlayActive: true,
      overlayLabel: "Tasks",
      keymap: "claude",
      status: "Assistant thinking…",
      runtimeStatusChips: [{ id: "thinking", label: "thinking", tone: "info" }],
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
})
