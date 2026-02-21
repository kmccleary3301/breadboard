import { describe, expect, it } from "vitest"
import {
  activityPriority,
  appendThinkingArtifact,
  appendThinkingPreviewState,
  closeThinkingPreviewState,
  createActivitySnapshot,
  createRuntimeTelemetry,
  createThinkingArtifact,
  createThinkingPreviewState,
  finalizeThinkingArtifact,
  isLegalActivityTransition,
  normalizeThinkingSignal,
  pruneThinkingPreviewState,
  reduceActivityTransition,
  resolveRuntimeBehaviorFlags,
} from "../controllerActivityRuntime.js"

describe("controllerActivityRuntime", () => {
  it("resolves runtime flag defaults", () => {
    const flags = resolveRuntimeBehaviorFlags({})
    expect(flags.activityEnabled).toBe(true)
    expect(flags.lifecycleToastsEnabled).toBe(false)
    expect(flags.thinkingEnabled).toBe(true)
    expect(flags.thinkingPreviewEnabled).toBe(true)
    expect(flags.allowFullThinking).toBe(false)
    expect(flags.allowRawThinkingPeek).toBe(false)
    expect(flags.inlineThinkingBlockEnabled).toBe(false)
    expect(flags.markdownCoalescingEnabled).toBe(true)
    expect(flags.adaptiveMarkdownCadenceEnabled).toBe(false)
    expect(flags.subagentWorkGraphEnabled).toBe(false)
    expect(flags.subagentStripEnabled).toBe(false)
    expect(flags.subagentToastsEnabled).toBe(false)
    expect(flags.subagentTaskboardEnabled).toBe(false)
    expect(flags.subagentFocusEnabled).toBe(false)
    expect(flags.subagentCoalesceMs).toBeGreaterThanOrEqual(0)
    expect(flags.subagentMaxWorkItems).toBeGreaterThanOrEqual(1)
    expect(flags.subagentMaxStepsPerTask).toBeGreaterThanOrEqual(1)
    // Locked phase4/phase5 cadence defaults used by replay/stress gates.
    expect(flags.minDisplayMs).toBe(200)
    expect(flags.statusUpdateMs).toBe(120)
    expect(flags.eventCoalesceMs).toBe(80)
    expect(flags.eventCoalesceMaxBatch).toBe(128)
    expect(flags.thinkingPreviewMaxLines).toBe(5)
    expect(flags.thinkingPreviewTtlMs).toBe(1400)
  })

  it("applies env overrides for runtime flags", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_ACTIVITY_ENABLED: "0",
      BREADBOARD_ACTIVITY_LIFECYCLE_TOASTS: "1",
      BREADBOARD_THINKING_ENABLED: "false",
      BREADBOARD_THINKING_PREVIEW_ENABLED: "1",
      BREADBOARD_THINKING_FULL_OPT_IN: "1",
      BREADBOARD_THINKING_PEEK_RAW_ALLOWED: "1",
      BREADBOARD_THINKING_INLINE_COLLAPSIBLE: "1",
      BREADBOARD_MARKDOWN_COALESCING_ENABLED: "off",
      BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE: "1",
      BREADBOARD_ACTIVITY_TRANSITION_DEBUG: "1",
      BREADBOARD_ACTIVITY_MIN_DISPLAY_MS: "333",
      BREADBOARD_STATUS_UPDATE_MS: "77",
      BREADBOARD_EVENT_COALESCE_MS: "45",
      BREADBOARD_EVENT_COALESCE_MAX_BATCH: "64",
      BREADBOARD_THINKING_MAX_CHARS: "1234",
      BREADBOARD_THINKING_MAX_LINES: "9",
      BREADBOARD_THINKING_PREVIEW_MAX_LINES: "4",
      BREADBOARD_THINKING_PREVIEW_TTL_MS: "2200",
      BREADBOARD_MARKDOWN_ADAPTIVE_MIN_CHUNK_CHARS: "5",
      BREADBOARD_MARKDOWN_ADAPTIVE_MIN_COALESCE_MS: "11",
      BREADBOARD_MARKDOWN_ADAPTIVE_BURST_CHARS: "42",
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "true",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "64",
      BREADBOARD_SUBAGENTS_MAX_WORK_ITEMS: "250",
      BREADBOARD_SUBAGENTS_MAX_STEPS_PER_TASK: "75",
    })
    expect(flags.activityEnabled).toBe(false)
    expect(flags.lifecycleToastsEnabled).toBe(true)
    expect(flags.thinkingEnabled).toBe(false)
    expect(flags.thinkingPreviewEnabled).toBe(true)
    expect(flags.allowFullThinking).toBe(true)
    expect(flags.allowRawThinkingPeek).toBe(true)
    expect(flags.inlineThinkingBlockEnabled).toBe(true)
    expect(flags.markdownCoalescingEnabled).toBe(false)
    expect(flags.adaptiveMarkdownCadenceEnabled).toBe(true)
    expect(flags.transitionDebug).toBe(true)
    expect(flags.minDisplayMs).toBe(333)
    expect(flags.statusUpdateMs).toBe(77)
    expect(flags.eventCoalesceMs).toBe(45)
    expect(flags.eventCoalesceMaxBatch).toBe(64)
    expect(flags.thinkingMaxChars).toBe(1234)
    expect(flags.thinkingMaxLines).toBe(9)
    expect(flags.thinkingPreviewMaxLines).toBe(4)
    expect(flags.thinkingPreviewTtlMs).toBe(2200)
    expect(flags.adaptiveMarkdownMinChunkChars).toBe(5)
    expect(flags.adaptiveMarkdownMinCoalesceMs).toBe(11)
    expect(flags.adaptiveMarkdownBurstChars).toBe(42)
    expect(flags.subagentWorkGraphEnabled).toBe(true)
    expect(flags.subagentStripEnabled).toBe(true)
    expect(flags.subagentToastsEnabled).toBe(true)
    expect(flags.subagentTaskboardEnabled).toBe(true)
    expect(flags.subagentFocusEnabled).toBe(true)
    expect(flags.subagentCoalesceMs).toBe(64)
    expect(flags.subagentMaxWorkItems).toBe(250)
    expect(flags.subagentMaxStepsPerTask).toBe(75)
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

  it("normalizes provider-shaped thinking payloads", () => {
    const openai = normalizeThinkingSignal(
      "assistant.reasoning.delta",
      { summary: [{ text: "openai summary line" }] },
      { provider: "openai" },
    )
    const anthropic = normalizeThinkingSignal(
      "assistant.reasoning.delta",
      { content_block: { thinking: "anthropic thought block" } },
      { provider: "anthropic" },
    )
    expect(openai?.text).toContain("openai summary")
    expect(anthropic?.text).toContain("anthropic thought")
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

  it("builds, closes, and expires bounded thinking previews", () => {
    const flags = resolveRuntimeBehaviorFlags({
      BREADBOARD_THINKING_PREVIEW_MAX_LINES: "2",
      BREADBOARD_THINKING_PREVIEW_TTL_MS: "100",
    })
    let preview = createThinkingPreviewState("summary", 1000, "preview-1")
    preview = appendThinkingPreviewState(
      preview,
      { kind: "summary", text: "line1\nline2\nline3", eventType: "assistant.thought_summary.delta", provider: "openai" },
      flags,
      1100,
    )
    expect(preview.lines).toHaveLength(2)
    const closed = closeThinkingPreviewState(preview, 1200)
    expect(closed.lifecycle).toBe("closed")
    expect(pruneThinkingPreviewState(closed, flags, 1250)).not.toBeNull()
    expect(pruneThinkingPreviewState(closed, flags, 1401)).toBeNull()
  })

  it("initializes telemetry counters at zero", () => {
    const telemetry = createRuntimeTelemetry()
    expect(telemetry.statusTransitions).toBe(0)
    expect(telemetry.suppressedTransitions).toBe(0)
    expect(telemetry.illegalTransitions).toBe(0)
    expect(telemetry.statusCommits).toBe(0)
    expect(telemetry.statusCoalesced).toBe(0)
    expect(telemetry.markdownFlushes).toBe(0)
    expect(telemetry.thinkingUpdates).toBe(0)
    expect(telemetry.thinkingPreviewOpened).toBe(0)
    expect(telemetry.thinkingPreviewClosed).toBe(0)
    expect(telemetry.thinkingPreviewExpired).toBe(0)
    expect(telemetry.adaptiveCadenceAdjustments).toBe(0)
    expect(telemetry.eventFlushes).toBe(0)
    expect(telemetry.eventCoalesced).toBe(0)
    expect(telemetry.eventMaxQueueDepth).toBe(0)
    expect(telemetry.optimisticToolRows).toBe(0)
    expect(telemetry.optimisticToolReconciled).toBe(0)
    expect(telemetry.optimisticDiffRows).toBe(0)
    expect(telemetry.optimisticDiffReconciled).toBe(0)
    expect(telemetry.workgraphFlushes).toBe(0)
    expect(telemetry.workgraphEvents).toBe(0)
    expect(telemetry.workgraphMaxQueueDepth).toBe(0)
  })
})
