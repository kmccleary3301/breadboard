import { describe, expect, it } from "vitest"
import { Option } from "effect"
import { DEFAULT_REPL_CONFIG_PATH, resolveReplLaunchContext } from "../src/commands/repl/launchContext"

describe("resolveReplLaunchContext", () => {
  it("resolves scrollback defaults and optional values", async () => {
    const context = await resolveReplLaunchContext({
      config: DEFAULT_REPL_CONFIG_PATH,
      workspace: null,
      model: "openai/gpt-5.4-mini",
      remoteStream: Option.some(true),
      permissionMode: "prompt",
      scriptPath: null,
      tui: null,
      tuiPreset: null,
      tuiConfigPath: null,
      tuiConfigStrict: null,
    })

    expect(context.frontend).toBe("scrollback")
    expect(context.modelValue).toBe("openai/gpt-5.4-mini")
    expect(context.permissionValue).toBe("prompt")
    expect(context.remotePreference).toBe(true)
    expect(context.resolvedConfigPath).toContain("agent_configs/codex_0-107-0_e4_3-6-2026.yaml")
    expect(context.resolvedWorkspace.length).toBeGreaterThan(0)
    expect(context.resolvedTuiConfig.meta.sources.length).toBeGreaterThan(0)
  })

  it("forces scrollback frontend for script mode even when cli asks for opentui", async () => {
    const context = await resolveReplLaunchContext({
      config: DEFAULT_REPL_CONFIG_PATH,
      scriptPath: "demo.json",
      tui: "opentui",
      remoteStream: Option.none(),
    })

    expect(context.frontend).toBe("scrollback")
    expect(context.scriptPath).toBe("demo.json")
    expect(context.remotePreference).toBeUndefined()
  })
})
