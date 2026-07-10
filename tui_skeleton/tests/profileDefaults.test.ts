import { afterEach, describe, expect, it } from "vitest"
import { loadChromeMode } from "../src/repl/chrome.js"
import { loadKeymapConfig } from "../src/repl/keymap.js"
import { loadProfileConfig } from "../src/repl/profile.js"

const ENV_KEYS = [
  "BREADBOARD_TUI_PROFILE",
  "BREADBOARD_PROFILE",
  "BREADBOARD_TUI_KEYMAP",
  "BREADBOARD_KEYMAP",
  "BREADBOARD_TUI_CHROME",
] as const

const originalEnv = new Map<string, string | undefined>()
for (const key of ENV_KEYS) originalEnv.set(key, process.env[key])

afterEach(() => {
  for (const key of ENV_KEYS) {
    const value = originalEnv.get(key)
    if (value == null) delete process.env[key]
    else process.env[key] = value
  }
})

describe("BreadBoard profile defaults", () => {
  it("defaults plain bb to the Codex profile/keymap/chrome", () => {
    for (const key of ENV_KEYS) delete process.env[key]

    expect(loadProfileConfig()).toEqual({ name: "codex_v1", keymap: "codex", chrome: "codex" })
    expect(loadKeymapConfig()).toBe("codex")
    expect(loadChromeMode("claude")).toBe("codex")
  })

  it("preserves explicit Claude profile overrides", () => {
    for (const key of ENV_KEYS) delete process.env[key]
    process.env.BREADBOARD_TUI_PROFILE = "claude"

    expect(loadProfileConfig()).toEqual({ name: "claude_v1", keymap: "claude", chrome: "claude" })
    expect(loadKeymapConfig()).toBe("claude")
    expect(loadChromeMode("codex")).toBe("claude")
  })
})
