import { describe, expect, it } from "vitest"
import {
  ACTION_KEYMAP,
  matchesActionBinding,
  normalizeKeyProfile,
  type ActionId,
  type KeyProfile,
} from "../actionKeymap.js"

const key = (seed: { ctrl?: boolean; shift?: boolean; meta?: boolean } = {}) =>
  ({
    ctrl: seed.ctrl ?? false,
    shift: seed.shift ?? false,
    meta: seed.meta ?? false,
  }) as any

describe("actionKeymap", () => {
  it("normalizes configured keymap profile labels", () => {
    expect(normalizeKeyProfile("claude")).toBe("claude")
    expect(normalizeKeyProfile("codex")).toBe("codex")
    expect(normalizeKeyProfile("unknown-profile")).toBe("breadboard")
  })

  it("matches canonical ctrl bindings for core actions", () => {
    expect(matchesActionBinding("claude", "toggle_todos_panel", "t", key({ ctrl: true }))).toBe(true)
    expect(matchesActionBinding("codex", "toggle_tasks_panel", "b", key({ ctrl: true }))).toBe(true)
    expect(matchesActionBinding("breadboard", "toggle_ctree_panel", "y", key({ ctrl: true }))).toBe(true)
    expect(matchesActionBinding("claude", "toggle_transcript_viewer", "t", key({ ctrl: true, shift: true }))).toBe(
      true,
    )
  })

  it("rejects mismatched modifier combinations", () => {
    expect(matchesActionBinding("claude", "toggle_todos_panel", "t", key())).toBe(false)
    expect(matchesActionBinding("claude", "toggle_transcript_viewer", "t", key({ ctrl: true }))).toBe(false)
    expect(matchesActionBinding("claude", "clear_screen", "l", key({ ctrl: true, meta: true }))).toBe(false)
  })

  it("matches ctrl actions from raw control-byte input", () => {
    expect(matchesActionBinding("claude", "toggle_todos_panel", "\u0014", key())).toBe(true)
    expect(matchesActionBinding("claude", "toggle_tasks_panel", "\u0002", key())).toBe(true)
  })

  it("matches ctrl actions from CSI-u escape encoding", () => {
    expect(matchesActionBinding("claude", "toggle_todos_panel", "\u001b[116;5u", key())).toBe(true)
    expect(matchesActionBinding("claude", "toggle_transcript_viewer", "\u001b[116;6u", key())).toBe(true)
  })

  it("matches ctrl actions from xterm modifyOtherKeys encoding", () => {
    expect(matchesActionBinding("claude", "toggle_tasks_panel", "\u001b[27;5;98~", key())).toBe(true)
    expect(matchesActionBinding("claude", "toggle_ctree_panel", "\u001b[27;5;121~", key())).toBe(true)
  })

  it("matches ctrl actions from key.name fallback when char is absent", () => {
    expect(matchesActionBinding("claude", "toggle_todos_panel", "", ({ ctrl: true, name: "t" } as any))).toBe(true)
    expect(matchesActionBinding("claude", "toggle_tasks_panel", "", ({ ctrl: true, name: "b" } as any))).toBe(true)
  })

  it("keeps every profile mapped for every action id", () => {
    const actions = Object.keys(ACTION_KEYMAP.breadboard) as ActionId[]
    const profiles = Object.keys(ACTION_KEYMAP) as KeyProfile[]
    for (const profile of profiles) {
      for (const action of actions) {
        expect(ACTION_KEYMAP[profile][action].length).toBeGreaterThan(0)
      }
    }
  })
})
