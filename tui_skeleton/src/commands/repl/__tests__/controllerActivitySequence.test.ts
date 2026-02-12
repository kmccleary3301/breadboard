import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

describe("ReplSessionController activity sequencing", () => {
  it("tracks core lifecycle transitions for run -> tool -> permission -> completion", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "turn_start"))
    controller.applyEvent(event(3, "assistant.message.start"))
    controller.applyEvent(event(4, "tool_call", { call_id: "c1", tool: "Write", tool_name: "Write" }))
    controller.applyEvent(event(5, "permission.request", { request_id: "p1", tool: "Write" }))
    controller.applyEvent(event(6, "permission.decision", { decision: "allow-once" }))
    controller.applyEvent(event(7, "completion", { completed: true }))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("completed")
    expect(state.runtimeTelemetry?.statusTransitions).toBeGreaterThan(0)
    expect(state.runtimeTelemetry?.illegalTransitions).toBe(0)
  })

  it("coalesces duplicate status updates inside update window", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }

    controller.runtimeFlags = { ...controller.runtimeFlags, statusUpdateMs: 10_000 }
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "run.start"))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("thinking")
    expect(state.runtimeTelemetry?.statusTransitions).toBe(1)
    expect(state.runtimeTelemetry?.suppressedTransitions).toBe(0)
  })

  it("applies hysteresis in event path and suppresses low-priority churn", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }

    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      minDisplayMs: 10_000,
      statusUpdateMs: 0,
    }
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "assistant.message.start"))
    controller.applyEvent(event(3, "turn_start"))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("responding")
    expect(state.runtimeTelemetry?.suppressedTransitions).toBeGreaterThan(0)
  })

  it("keeps first active state deterministic as thinking across run.start + turn_start bursts", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }

    controller.runtimeFlags = { ...controller.runtimeFlags, statusUpdateMs: 10_000 }
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "turn_start"))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("thinking")
    expect(state.runtimeTelemetry?.statusTransitions).toBe(1)
  })

  it("transitions to responding only once for an assistant delta burst", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }

    controller.runtimeFlags = { ...controller.runtimeFlags, statusUpdateMs: 10_000 }
    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "assistant.message.start"))
    controller.applyEvent(event(3, "assistant.message.delta", { delta: "a" }))
    controller.applyEvent(event(4, "assistant.message.delta", { delta: "b" }))
    controller.applyEvent(event(5, "assistant_delta", { text: "c" }))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("responding")
    expect(state.runtimeTelemetry?.statusTransitions).toBe(2)
  })

  it("maps permission allow/deny/timeout branches deterministically", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "permission.request", { request_id: "perm-1", tool: "Write" }))
    controller.applyEvent(event(3, "permission.decision", { decision: "allow-once" }))
    controller.applyEvent(event(4, "assistant.message.start"))
    let state = controller.getState()
    expect(state.activity?.primary).toBe("responding")

    controller.applyEvent(event(5, "permission.request", { request_id: "perm-2", tool: "Write" }))
    controller.applyEvent(event(6, "permission.decision", { decision: "deny" }))
    state = controller.getState()
    expect(state.activity?.primary).toBe("halted")

    controller.applyEvent(event(7, "permission.request", { request_id: "perm-3", tool: "Write" }))
    controller.applyEvent(event(8, "permission.timeout", { request_id: "perm-3" }))
    state = controller.getState()
    expect(state.activity?.primary).toBe("halted")
  })

  it("surfaces reconnecting and compacting states, then clears them deterministically", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "stream.gap"))
    let state = controller.getState()
    expect(state.activity?.primary).toBe("reconnecting")
    expect(state.hints.some((hint: string) => hint.includes("Stream gap detected"))).toBe(true)

    controller.applyEvent(event(2, "run.start"))
    state = controller.getState()
    expect(state.activity?.primary).toBe("thinking")

    controller.applyEvent(event(3, "conversation.compaction.start"))
    state = controller.getState()
    expect(state.activity?.primary).toBe("compacting")

    controller.applyEvent(event(4, "conversation.compaction.end"))
    state = controller.getState()
    expect(state.activity?.primary).toBe("session")
    expect(state.hints.some((hint: string) => hint.includes("Compaction started"))).toBe(true)
    expect(state.hints.some((hint: string) => hint.includes("Compaction complete"))).toBe(true)
  })

  it("keeps transcript behavior stable when activity runtime is disabled", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }

    controller.runtimeFlags = { ...controller.runtimeFlags, activityEnabled: false }
    controller.applyEvent(event(1, "assistant.message.start"))
    controller.applyEvent(event(2, "assistant.message.delta", { delta: "hello " }))
    controller.applyEvent(event(3, "assistant.message.delta", { delta: "world" }))
    controller.applyEvent(event(4, "assistant.message.end"))

    const state = controller.getState()
    const assistantText = state.conversation
      .filter((entry: any) => entry.speaker === "assistant")
      .map((entry: any) => entry.text)
      .join("")

    expect(state.activity?.primary).toBe("idle")
    expect(state.runtimeTelemetry?.statusTransitions ?? 0).toBe(0)
    expect(assistantText).toBe("hello world")
  })

  it("emits bounded lifecycle toast only when lifecycle-toasts flag is enabled", () => {
    const enabled = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    enabled.runtimeFlags = { ...enabled.runtimeFlags, lifecycleToastsEnabled: true }
    enabled.applyEvent(event(1, "run.start"))
    enabled.applyEvent(event(2, "stream.gap"))
    enabled.applyEvent(event(3, "conversation.compaction.start"))
    let state = enabled.getState()
    expect(state.liveSlots.some((slot: any) => String(slot.text).includes("[lifecycle]"))).toBe(true)

    const disabled = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    disabled.runtimeFlags = { ...disabled.runtimeFlags, lifecycleToastsEnabled: false }
    disabled.applyEvent(event(1, "run.start"))
    disabled.applyEvent(event(2, "stream.gap"))
    disabled.applyEvent(event(3, "conversation.compaction.start"))
    state = disabled.getState()
    expect(state.liveSlots.some((slot: any) => String(slot.text).includes("[lifecycle]"))).toBe(false)
  })

  it("suppresses status churn during repeated permission/tool bursts", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      statusUpdateMs: 10_000,
      minDisplayMs: 2_000,
    }
    controller.applyEvent(event(1, "run.start"))
    for (let i = 0; i < 3; i += 1) {
      const base = i * 10
      controller.applyEvent(event(base + 2, "permission.request", { request_id: `perm-${i}`, tool: "Write" }))
      controller.applyEvent(event(base + 3, "permission.decision", { decision: "allow-once" }))
      controller.applyEvent(event(base + 4, "tool_call", { call_id: `call-${i}`, tool_name: "Write" }))
      controller.applyEvent(event(base + 5, "tool.result", { call_id: `call-${i}`, tool_name: "Write" }))
    }
    controller.applyEvent(event(40, "completion", { completed: true }))

    const state = controller.getState()
    expect(state.activity?.primary).toBe("completed")
    expect(state.runtimeTelemetry?.suppressedTransitions).toBeGreaterThan(0)
    expect(state.runtimeTelemetry?.statusTransitions ?? 0).toBeLessThanOrEqual(12)
  })

  it("keeps inline thinking block disabled by default and avoids transcript leakage", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
    }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      inlineThinkingBlockEnabled: false,
      thinkingEnabled: true,
      allowFullThinking: false,
      allowRawThinkingPeek: false,
    }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.thought_summary.delta", { delta: "plan draft" }))
    controller.applyEvent(event(3, "assistant.reasoning.delta", { delta: "raw hidden" }))

    const state = controller.getState()
    const transcript = state.conversation.map((entry: any) => String(entry.text ?? "")).join("\n")
    expect(transcript.includes("[thinking-inline]")).toBe(false)
    expect(transcript.includes("raw hidden")).toBe(false)
  })

  it("emits inline thinking summary block only when explicitly enabled", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      runtimeFlags: Record<string, unknown>
      providerCapabilities: Record<string, unknown>
      viewPrefs: Record<string, unknown>
    }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      inlineThinkingBlockEnabled: true,
      thinkingEnabled: true,
      allowFullThinking: false,
      allowRawThinkingPeek: false,
    }
    controller.providerCapabilities = {
      ...controller.providerCapabilities,
      inlineThinkingBlock: true,
      thoughtSummaryEvents: true,
      reasoningEvents: true,
    }
    controller.viewPrefs = { ...controller.viewPrefs, showReasoning: false }
    controller.applyEvent(event(1, "turn_start"))
    controller.applyEvent(event(2, "assistant.thought_summary.delta", { delta: "plan draft" }))
    controller.applyEvent(event(3, "assistant.reasoning.delta", { delta: "raw hidden" }))

    const state = controller.getState()
    const thinkingEntry = state.conversation.find((entry: any) =>
      String(entry.text ?? "").startsWith("[thinking-inline]"),
    )
    expect(thinkingEntry).toBeTruthy()
    expect(String(thinkingEntry.text)).toContain("summary: plan draft")
    expect(String(thinkingEntry.text)).not.toContain("raw hidden")
  })
})
