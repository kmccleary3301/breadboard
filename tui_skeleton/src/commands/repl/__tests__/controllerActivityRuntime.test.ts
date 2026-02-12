import { describe, expect, it } from "vitest"
import {
  activityPriority,
  appendThinkingArtifact,
  createActivitySnapshot,
  createRuntimeTelemetry,
  createThinkingArtifact,
  finalizeThinkingArtifact,
  isLegalActivityTransition,
  normalizeThinkingSignal,
  reduceActivityTransition,
  resolveRuntimeBehaviorFlags,
} from "../controllerActivityRuntime.js"

describe("controllerActivityRuntime", () => {
  it("resolves runtime flag defaults", () => {
    const flags = resolveRuntimeBehaviorFlags({})
    expect(flags.activityEnabled).toBe(true)
    expect(flags.lifecycleToastsEnabled).toBe(false)
    expect(flags.thinkingEnabled).toBe(true)
    expect(flags.allowFullThinking).toBe(false)
    expect(flags.allowRawThinkingPeek).toBe(false)
    expect(flags.inlineThinkingBlockEnabled).toBe(false)
    expect(flags.markdownCoalescingEnabled).toBe(true)
    expect(flags.adaptiveMarkdownCadenceEnabled).toBe(false)
    expect(flags.minDisplayMs).toBeGreaterThanOrEqual(0)
    expect(flags.statusUpdateMs).toBeGreaterThanOrEqual(0)
  })

  it("applies env overrides for runtime flags", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_ENABLED: "0",
      BREADBOARD_ACTIVITY_LIFECYCLE_TOASTS: "1",
      BREADBOARD_THINKING_ENABLED: "false",
      BREADBOARD_THINKING_FULL_OPT_IN: "1",
      BREADBOARD_THINKING_PEEK_RAW_ALLOWED: "1",
      BREADBOARD_THINKING_INLINE_COLLAPSIBLE: "1",
      BREADBOARD_MARKDOWN_COALESCING_ENABLED: "off",
      BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE: "1",
      BREADBOARD_ACTIVITY_TRANSITION_DEBUG: "1",
      BREADBOARD_ACTIVITY_MIN_DISPLAY_MS: "333",
      BREADBOARD_STATUS_UPDATE_MS: "77",
      BREADBOARD_THINKING_MAX_CHARS: "1234",
      BREADBOARD_THINKING_MAX_LINES: "9",
      BREADBOARD_MARKDOWN_ADAPTIVE_MIN_CHUNK_CHARS: "5",
      BREADBOARD_MARKDOWN_ADAPTIVE_MIN_COALESCE_MS: "11",
      BREADBOARD_MARKDOWN_ADAPTIVE_BURST_CHARS: "42",
    })
    expect(flags.activityEnabled).toBe(false)
    expect(flags.lifecycleToastsEnabled).toBe(true)
    expect(flags.thinkingEnabled).toBe(false)
    expect(flags.allowFullThinking).toBe(true)
    expect(flags.allowRawThinkingPeek).toBe(true)
    expect(flags.inlineThinkingBlockEnabled).toBe(true)
    expect(flags.markdownCoalescingEnabled).toBe(false)
    expect(flags.adaptiveMarkdownCadenceEnabled).toBe(true)
    expect(flags.transitionDebug).toBe(true)
    expect(flags.minDisplayMs).toBe(333)
    expect(flags.statusUpdateMs).toBe(77)
    expect(flags.thinkingMaxChars).toBe(1234)
    expect(flags.thinkingMaxLines).toBe(9)
    expect(flags.adaptiveMarkdownMinChunkChars).toBe(5)
    expect(flags.adaptiveMarkdownMinCoalesceMs).toBe(11)
    expect(flags.adaptiveMarkdownBurstChars).toBe(42)
  })

  it("enforces legality matrix", () => {
    expect(isLegalActivityTransition("run", "responding")).toBe(true)
    expect(isLegalActivityTransition("permission_required", "completed")).toBe(true)
    expect(isLegalActivityTransition("completed", "tool_call")).toBe(false)
  })

  it("suppresses lower-priority transitions inside hysteresis window", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_MIN_DISPLAY_MS: "500",
      BREADBOARD_ACTIVITY_ENABLED: "1",
    })
    const base = createActivitySnapshot("responding", 1_000, 1)
    const next = reduceActivityTransition(base, { to: "thinking", now: 1_100 }, flags)
    expect(next.applied).toBe(false)
    expect(next.reason).toBe("hysteresis")
    expect(next.snapshot.primary).toBe("responding")
  })

  it("allows higher-priority transitions inside hysteresis window", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_MIN_DISPLAY_MS: "500",
      BREADBOARD_ACTIVITY_ENABLED: "1",
    })
    const base = createActivitySnapshot("responding", 1_000, 1)
    const next = reduceActivityTransition(base, { to: "error", now: 1_100 }, flags)
    expect(activityPriority("error")).toBeGreaterThan(activityPriority("responding"))
    expect(next.applied).toBe(true)
    expect(next.reason).toBe("applied")
    expect(next.snapshot.primary).toBe("error")
    expect(next.snapshot.seq).toBe(2)
  })

  it("returns illegal for disallowed transitions", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_MIN_DISPLAY_MS: "0",
      BREADBOARD_ACTIVITY_ENABLED: "1",
    })
    const base = createActivitySnapshot("completed", 1_000, 10)
    const next = reduceActivityTransition(base, { to: "tool_call", now: 1_050 }, flags)
    expect(next.applied).toBe(false)
    expect(next.reason).toBe("illegal")
    expect(next.snapshot).toBe(base)
  })

  it("suppresses transitions when activity runtime is disabled", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_ENABLED: "0",
    })
    const base = createActivitySnapshot("run", 1_000, 1)
    const next = reduceActivityTransition(base, { to: "responding", now: 1_050 }, flags)
    expect(next.applied).toBe(false)
    expect(next.reason).toBe("disabled")
    expect(next.snapshot).toBe(base)
  })

  it("normalizes reasoning/thought-summary deltas", () => {
    const reasoning = normalizeThinkingSignal("assistant.reasoning.delta", { delta: "Analyzing..." })
    const summary = normalizeThinkingSignal("assistant.thought_summary.delta", { text: "Plan ready." })
    expect(reasoning?.kind).toBe("delta")
    expect(summary?.kind).toBe("summary")
    expect(reasoning?.text).toContain("Analyzing")
    expect(summary?.text).toContain("Plan")
  })

  it("builds and finalizes bounded thinking artifacts", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_THINKING_MAX_CHARS: "20",
      BREADBOARD_THINKING_MAX_LINES: "2",
    })
    let artifact = createThinkingArtifact("summary", 1000, "thinking-test")
    const signal1 = normalizeThinkingSignal("assistant.reasoning.delta", { delta: "line1" })
    const signal2 = normalizeThinkingSignal("assistant.reasoning.delta", { delta: "line2\nline3" })
    expect(signal1).toBeTruthy()
    expect(signal2).toBeTruthy()
    artifact = appendThinkingArtifact(artifact, signal1!, flags, 1100)
    artifact = appendThinkingArtifact(artifact, signal2!, flags, 1200)
    expect(artifact.summary.length).toBeLessThanOrEqual(flags.thinkingMaxChars)
    expect((artifact.summary.match(/\n/g) ?? []).length).toBeLessThanOrEqual(1)
    const done = finalizeThinkingArtifact(artifact, 1300)
    expect(done.finalizedAt).toBe(1300)
  })

  it("initializes telemetry counters at zero", () => {
    const telemetry = createRuntimeTelemetry()
    expect(telemetry.statusTransitions).toBe(0)
    expect(telemetry.suppressedTransitions).toBe(0)
    expect(telemetry.illegalTransitions).toBe(0)
    expect(telemetry.markdownFlushes).toBe(0)
    expect(telemetry.thinkingUpdates).toBe(0)
    expect(telemetry.adaptiveCadenceAdjustments).toBe(0)
  })
})
