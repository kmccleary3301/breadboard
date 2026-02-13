import { afterEach, describe, expect, it } from "vitest"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { resolveTuiConfig } from "../load.js"

const envKeys = [
  "HOME",
  "BREADBOARD_TUI_PRESET",
  "BREADBOARD_TUI_PROMPT_PREFIX",
  "BREADBOARD_TUI_SHIKI_THEME",
  "BREADBOARD_TUI_DIFF_PREVIEW_MAX_LINES",
  "BREADBOARD_TUI_SUBAGENTS_ENABLED",
  "BREADBOARD_TUI_SUBAGENTS_COALESCE_MS",
  "BREADBOARD_TUI_SUBAGENTS_FOCUS_MODE",
  "BREADBOARD_TUI_CONFIG_STRICT",
  "NO_COLOR",
]

const snapshotEnv = () => new Map(envKeys.map((key) => [key, process.env[key]]))

const restoreEnv = (snapshot: Map<string, string | undefined>) => {
  for (const key of envKeys) {
    const value = snapshot.get(key)
    if (value == null) delete process.env[key]
    else process.env[key] = value
  }
}

afterEach(() => {
  for (const key of envKeys) delete process.env[key]
})

describe("resolveTuiConfig", () => {
  it("applies deterministic precedence defaults->preset->repo->user->cli->env", async () => {
    const env = snapshotEnv()
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-"))
    const workspace = path.join(root, "workspace")
    const home = path.join(root, "home")
    await fs.mkdir(path.join(workspace, ".breadboard"), { recursive: true })
    await fs.mkdir(path.join(home, ".config", "breadboard"), { recursive: true })
    await fs.writeFile(
      path.join(workspace, ".breadboard", "tui.yaml"),
      [
        "landing:",
        "  variant: split",
        "markdown:",
        "  shikiTheme: github-dark",
        "diff:",
        "  previewMaxLines: 18",
        "composer:",
        "  promptPrefix: repo-prefix",
      ].join("\n"),
      "utf8",
    )
    await fs.writeFile(
      path.join(home, ".config", "breadboard", "tui.yaml"),
      [
        "composer:",
        "  promptPrefix: user-prefix",
      ].join("\n"),
      "utf8",
    )
    const cliConfigPath = path.join(root, "cli.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "markdown:",
        "  shikiTheme: nord",
        "diff:",
        "  maxTokenizedLines: 222",
        "  colors:",
        "    addText: '#22ee88'",
        "composer:",
        "  promptPrefix: cli-prefix",
      ].join("\n"),
      "utf8",
    )

    process.env.HOME = home
    process.env.BREADBOARD_TUI_PROMPT_PREFIX = "env-prefix"
    process.env.BREADBOARD_TUI_SHIKI_THEME = "andromeeda"
    process.env.BREADBOARD_TUI_DIFF_PREVIEW_MAX_LINES = "31"
    const resolved = await resolveTuiConfig({
      workspace,
      cliConfigPath,
      cliPreset: "breadboard_default",
      cliStrict: false,
      colorAllowed: false,
    })

    expect(resolved.composer.promptPrefix).toBe("env-prefix")
    expect(resolved.landing.variant).toBe("split")
    expect(resolved.markdown.shikiTheme).toBe("andromeeda")
    expect(resolved.diff.previewMaxLines).toBe(31)
    expect(resolved.diff.maxTokenizedLines).toBe(222)
    expect(resolved.diff.colors.addText).toBe("#22ee88")
    expect(resolved.subagents.enabled).toBe(false)
    expect(resolved.subagents.coalesceMs).toBe(125)
    expect(resolved.subagents.maxWorkItems).toBe(200)
    expect(resolved.subagents.focusMode).toBe("lane")
    expect(resolved.meta.sources.some((value) => value.startsWith("repo:"))).toBe(true)
    expect(resolved.meta.sources.some((value) => value.startsWith("cli-config:"))).toBe(true)

    restoreEnv(env)
  })

  it("raises actionable path errors for unknown keys in strict mode", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-strict-"))
    const cliConfigPath = path.join(root, "strict.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "composer:",
        "  promptPrefix: strict-prefix",
        "  unsupportedField: nope",
      ].join("\n"),
      "utf8",
    )

    await expect(
      resolveTuiConfig({
        workspace: root,
        cliConfigPath,
        cliStrict: true,
      }),
    ).rejects.toThrow(/composer\.unsupportedField/)
  })

  it("validates numeric diff knobs as positive integers", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-diff-int-"))
    const cliConfigPath = path.join(root, "invalid-diff.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "diff:",
        "  previewMaxLines: 0",
      ].join("\n"),
      "utf8",
    )
    await expect(
      resolveTuiConfig({
        workspace: root,
        cliConfigPath,
        cliStrict: true,
      }),
    ).rejects.toThrow(/diff\.previewMaxLines/)
  })

  it("enforces NO_COLOR precedence over configured display.colorMode", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-nocolor-"))
    const cliConfigPath = path.join(root, "nocolor.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "display:",
        "  colorMode: truecolor",
      ].join("\n"),
      "utf8",
    )
    process.env.NO_COLOR = "1"
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliConfigPath,
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.display.colorMode).toBe("none")
  })

  it("loads codex_cli_like preset defaults", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-codex-preset-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliPreset: "codex_cli_like",
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("codex_cli_like")
    expect(resolved.composer.promptPrefix).toBe("›")
    expect(resolved.composer.showBottomRule).toBe(false)
    expect(resolved.statusLine.completionTemplate).toBe("• Worked for {duration}")
  })

  it("loads claude_like_subagents preset defaults", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-claude-subagents-preset-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliPreset: "claude_like_subagents",
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("claude_like_subagents")
    expect(resolved.subagents.enabled).toBe(true)
    expect(resolved.subagents.taskboardEnabled).toBe(true)
    expect(resolved.subagents.focusEnabled).toBe(true)
    expect(resolved.subagents.focusMode).toBe("lane")
    expect(resolved.subagents.maxWorkItems).toBe(300)
  })

  it("loads opencode_like_subagents preset defaults", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-opencode-subagents-preset-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliPreset: "opencode_like_subagents",
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("opencode_like_subagents")
    expect(resolved.composer.promptPrefix).toBe(">")
    expect(resolved.subagents.enabled).toBe(true)
    expect(resolved.subagents.focusMode).toBe("lane")
    expect(resolved.subagents.maxStepsPerTask).toBe(64)
  })

  it("loads claude_like_subagents_swap preset defaults", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-claude-subagents-swap-preset-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliPreset: "claude_like_subagents_swap",
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("claude_like_subagents_swap")
    expect(resolved.subagents.enabled).toBe(true)
    expect(resolved.subagents.focusEnabled).toBe(true)
    expect(resolved.subagents.focusMode).toBe("swap")
    expect(resolved.subagents.maxWorkItems).toBe(320)
  })

  it("loads codex_like_subagents_dense preset defaults", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-codex-subagents-dense-preset-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliPreset: "codex_like_subagents_dense",
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("codex_like_subagents_dense")
    expect(resolved.composer.promptPrefix).toBe("›")
    expect(resolved.subagents.enabled).toBe(true)
    expect(resolved.subagents.focusMode).toBe("lane")
    expect(resolved.subagents.maxStepsPerTask).toBe(120)
  })

  it("keeps default profile unchanged when no subagent preset is selected", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-default-compat-"))
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliStrict: true,
      colorAllowed: true,
    })
    expect(resolved.preset).toBe("breadboard_default")
    expect(resolved.subagents.enabled).toBe(false)
    expect(resolved.subagents.taskboardEnabled).toBe(false)
    expect(resolved.subagents.focusEnabled).toBe(false)
    expect(resolved.subagents.focusMode).toBe("lane")
  })

  it("resolves subagents settings across yaml and env layers", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-subagents-"))
    const cliConfigPath = path.join(root, "subagents.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "subagents:",
        "  enabled: true",
        "  stripEnabled: true",
        "  toastsEnabled: false",
        "  taskboardEnabled: true",
        "  focusEnabled: false",
        "  focusMode: swap",
        "  coalesceMs: 70",
        "  maxWorkItems: 333",
        "  maxStepsPerTask: 44",
      ].join("\n"),
      "utf8",
    )
    process.env.BREADBOARD_TUI_SUBAGENTS_ENABLED = "false"
    process.env.BREADBOARD_TUI_SUBAGENTS_COALESCE_MS = "10"
    process.env.BREADBOARD_TUI_SUBAGENTS_FOCUS_MODE = "lane"
    const resolved = await resolveTuiConfig({
      workspace: root,
      cliConfigPath,
      cliStrict: true,
    })
    expect(resolved.subagents.enabled).toBe(false)
    expect(resolved.subagents.stripEnabled).toBe(true)
    expect(resolved.subagents.taskboardEnabled).toBe(true)
    expect(resolved.subagents.focusMode).toBe("lane")
    expect(resolved.subagents.coalesceMs).toBe(10)
    expect(resolved.subagents.maxWorkItems).toBe(333)
    expect(resolved.subagents.maxStepsPerTask).toBe(44)
  })

  it("validates subagents numeric knobs", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-subagents-int-"))
    const cliConfigPath = path.join(root, "invalid-subagents.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "subagents:",
        "  coalesceMs: -1",
      ].join("\n"),
      "utf8",
    )
    await expect(
      resolveTuiConfig({
        workspace: root,
        cliConfigPath,
        cliStrict: true,
      }),
    ).rejects.toThrow(/subagents\.coalesceMs/)
  })

  it("validates subagents focusMode enum", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-tui-config-subagents-focus-mode-"))
    const cliConfigPath = path.join(root, "invalid-subagents-focus-mode.yaml")
    await fs.writeFile(
      cliConfigPath,
      [
        "subagents:",
        "  focusMode: not-real",
      ].join("\n"),
      "utf8",
    )
    await expect(
      resolveTuiConfig({
        workspace: root,
        cliConfigPath,
        cliStrict: true,
      }),
    ).rejects.toThrow(/subagents\.focusMode/)
  })
})
