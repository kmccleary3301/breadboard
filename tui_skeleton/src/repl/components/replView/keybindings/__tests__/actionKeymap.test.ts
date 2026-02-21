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
