import { describe, expect, it } from "vitest"
import { resolveStressCaseConfigPath } from "../scripts/harness/caseConfigPath.ts"

describe("resolveStressCaseConfigPath", () => {
  it("uses the default config for non-live cases without a case override", () => {
    expect(
      resolveStressCaseConfigPath({
        defaultConfigPath: "agent_configs/default.yaml",
      }),
    ).toBe("agent_configs/default.yaml")
  })

  it("uses the case config for non-live cases with a case override", () => {
    expect(
      resolveStressCaseConfigPath({
        caseConfigPath: "agent_configs/case.yaml",
        defaultConfigPath: "agent_configs/default.yaml",
      }),
    ).toBe("agent_configs/case.yaml")
  })

  it("preserves a live case config instead of replacing it with the live default", () => {
    expect(
      resolveStressCaseConfigPath({
        caseConfigPath: "agent_configs/live-case.yaml",
        requiresLive: true,
        defaultConfigPath: "agent_configs/default.yaml",
        liveConfigPath: "agent_configs/live-default.yaml",
      }),
    ).toBe("agent_configs/live-case.yaml")
  })

  it("uses the live default for live cases when no case config is pinned", () => {
    expect(
      resolveStressCaseConfigPath({
        requiresLive: true,
        defaultConfigPath: "agent_configs/default.yaml",
        liveConfigPath: "agent_configs/live-default.yaml",
      }),
    ).toBe("agent_configs/live-default.yaml")
  })

  it("falls back to the default config for live cases without any live or case override", () => {
    expect(
      resolveStressCaseConfigPath({
        requiresLive: true,
        defaultConfigPath: "agent_configs/default.yaml",
        liveConfigPath: null,
      }),
    ).toBe("agent_configs/default.yaml")
  })
})
