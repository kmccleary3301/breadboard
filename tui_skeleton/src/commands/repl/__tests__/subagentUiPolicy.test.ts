import { describe, expect, it } from "vitest"
import { resolveRuntimeBehaviorFlags } from "../controllerActivityRuntime.js"
import { resolveSubagentUiPolicy } from "../subagentUiPolicy.js"

describe("subagentUiPolicy", () => {
  it("preserves baseline routing when v2 is disabled", () => {
    const flags = resolveRuntimeBehaviorFlags({})
    const policy = resolveSubagentUiPolicy(flags, {})
    expect(policy.v2Enabled).toBe(false)
    expect(policy.routeTaskEventsToToolRail).toBe(true)
    expect(policy.routeTaskEventsToLiveSlots).toBe(false)
  })

  it("routes task events away from tool rail when v2 is enabled", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
    })
    const policy = resolveSubagentUiPolicy(flags, {})
    expect(policy.v2Enabled).toBe(true)
    expect(policy.routeTaskEventsToToolRail).toBe(false)
    expect(policy.routeTaskEventsToLiveSlots).toBe(true)
    expect(policy.stripEnabled).toBe(true)
  })

  it("supports explicit tool-rail override and bounded ttl values", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
    })
    const policy = resolveSubagentUiPolicy(flags, {
      BREADBOARD_SUBAGENTS_TASK_EVENTS_TOOL_RAIL: "1",
      BREADBOARD_SUBAGENTS_TOAST_TTL_MS: "99",
      BREADBOARD_SUBAGENTS_TOAST_ERROR_TTL_MS: "999999",
      BREADBOARD_SUBAGENTS_TOAST_MERGE_MS: "40000",
    })
    expect(policy.routeTaskEventsToToolRail).toBe(true)
    expect(policy.toastTtlMs).toBe(250)
    expect(policy.toastErrorTtlMs).toBe(60_000)
    expect(policy.toastMergeWindowMs).toBe(30_000)
  })
})
