import { readFile } from "node:fs/promises"
import path from "node:path"
import { describe, expect, it } from "vitest"
import YAML from "yaml"

import { DEFAULT_CONFIG_PATH } from "../src/config/appConfig.js"
import { DEFAULT_REQUEST_CONFIG_PATH } from "../src/commands/invocationContext.js"
import { DEFAULT_REPL_CONFIG_PATH } from "../src/commands/repl/launchContext.js"

const FROZEN_CODEX_E4_CONFIG = "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"

describe("default Codex config contract", () => {
  it("uses the frozen Codex E4 dossier for product-facing defaults", () => {
    expect(DEFAULT_CONFIG_PATH).toBe(FROZEN_CODEX_E4_CONFIG)
    expect(DEFAULT_REQUEST_CONFIG_PATH).toBe(FROZEN_CODEX_E4_CONFIG)
    expect(DEFAULT_REPL_CONFIG_PATH).toBe(FROZEN_CODEX_E4_CONFIG)
  })

  it("keeps the default lane on Codex-compatible E4 primitives", async () => {
    const repoRoot = path.resolve(process.cwd(), "..")
    const raw = await readFile(path.join(repoRoot, FROZEN_CODEX_E4_CONFIG), "utf8")
    const config = YAML.parse(raw)

    expect(config.profile.name).toBe("codex-0.107.0-e4-2026-03-06")
    expect(config.providers.default_model).toBe("openai/gpt-5.4-mini")
    expect(config.provider_tools.use_native).toBe(true)
    expect(config.provider_tools.suppress_prompts).toBe(false)
    expect(config.provider_tools.api_variant).toBe("responses")
    expect(config.loop.turn_strategy.flow).toBe("assistant_continuation")
    expect(config.completion.allow_content_only_completion).toBe(true)
    expect(config.prompts.packs.base.system).toContain("config/e4_targets/codex/0.107.0")
    expect(config.tools.registry.include).toEqual(["shell_command", "apply_patch", "update_plan"])
  })
})
