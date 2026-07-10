import { describe, expect, it } from "vitest"
import { buildOpenTuiLaunchSpec } from "../src/commands/repl/frontendLaunch.js"

describe("buildOpenTuiLaunchSpec", () => {
  it("builds the phaseB launch contract with bundle bypass", () => {
    const spec = buildOpenTuiLaunchSpec({
      configPath: "/tmp/config.yaml",
      workspace: "/tmp/ws",
      permissionMode: "ask",
    })

    expect(spec.command).toBe("bun")
    expect(spec.cwd.endsWith("opentui_slab")).toBe(true)
    expect(spec.args).toEqual([
      "run",
      "phaseB/controller.ts",
      "--config",
      "/tmp/config.yaml",
      "--workspace",
      "/tmp/ws",
      "--permission-mode",
      "ask",
    ])
    expect(spec.env.BREADBOARD_ENGINE_PREFER_BUNDLE).toBe("0")
  })

  it("omits optional flags when they are absent", () => {
    const spec = buildOpenTuiLaunchSpec({
      configPath: "/tmp/config.yaml",
      workspace: null,
      permissionMode: null,
    })

    expect(spec.args).toEqual(["run", "phaseB/controller.ts", "--config", "/tmp/config.yaml"])
  })
})
