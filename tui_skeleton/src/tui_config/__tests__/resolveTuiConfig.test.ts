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
  "BREADBOARD_TUI_CONFIG_STRICT",
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
})
