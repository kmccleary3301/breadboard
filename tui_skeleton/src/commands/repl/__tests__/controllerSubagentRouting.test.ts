import { afterEach, describe, expect, it, vi } from "vitest"
import { ReplSessionController } from "../controller.js"
import { ControlledClock } from "../../../repl/clock/controlledClock.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  timestamp: seq,
  payload,
})

describe("ReplSessionController subagent routing", () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("keeps task_event tool-rail lines in baseline mode", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-1", status: "running", description: "Index" }))
    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(true)
  })

  it("routes task_event away from tool rail when subagent v2 is enabled", () => {
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
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: false,
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-2", status: "running", description: "Plan" }))
    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(false)
    expect(state.workGraph.itemOrder).toContain("task-2")
  })

  it("emits subagent toast slots when enabled", () => {
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
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    controller.applyEvent(event(1, "task_event", { task_id: "task-3", status: "running", description: "Research" }))
    const state = controller.getState()
    expect(state.liveSlots.some((slot: any) => String(slot.text).includes("[subagent]"))).toBe(true)
  })

  it("sanitizes and truncates subagent toast preview text", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    const slotSpy = vi.fn()
    controller.upsertLiveSlot = slotSpy

    const noisy = `\u001b[31mVERY LOUD\u001b[0m \u0007 ${"x".repeat(220)}`
    controller.applyEvent(event(1, "task_event", { task_id: "task-4", status: "running", description: noisy }))

    expect(slotSpy).toHaveBeenCalledTimes(1)
    const rendered = String(slotSpy.mock.calls[0]?.[1] ?? "")
    expect(rendered).not.toContain("\u001b")
    expect(rendered).not.toContain("\u0007")
    expect(rendered).toContain("...")
  })

  it("dedupes repeated status toasts inside merge window", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-02-13T00:00:00Z"))
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    const slotSpy = vi.fn()
    controller.upsertLiveSlot = slotSpy

    controller.applyEvent(event(1, "task_event", { task_id: "task-5", status: "running", description: "Index" }))
    controller.applyEvent(event(2, "task_event", { task_id: "task-5", status: "running", description: "Index" }))
    expect(slotSpy).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(800)
    controller.applyEvent(event(3, "task_event", { task_id: "task-5", status: "running", description: "Index" }))
    expect(slotSpy).toHaveBeenCalledTimes(2)
  })

  it("uses longer TTL for failed subagent toasts", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    const slotSpy = vi.fn()
    controller.upsertLiveSlot = slotSpy

    controller.applyEvent(event(1, "task_event", { task_id: "task-6", status: "running", description: "A" }))
    controller.applyEvent(event(2, "task_event", { task_id: "task-7", status: "failed", description: "B" }))

    expect(slotSpy).toHaveBeenCalledTimes(2)
    const runningTtl = Number(slotSpy.mock.calls[0]?.[4])
    const failedTtl = Number(slotSpy.mock.calls[1]?.[4])
    expect(runningTtl).toBe(1800)
    expect(failedTtl).toBe(4200)
  })

  it("captures async completion flow with toast + no task tool-rail spam", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    const slotSpy = vi.fn()
    controller.upsertLiveSlot = slotSpy

    controller.applyEvent(
      event(1, "task_event", {
        task_id: "task-8",
        mode: "async",
        status: "running",
        description: "Resolve dependencies",
      }),
    )
    controller.applyEvent(
      event(2, "task_event", {
        task_id: "task-8",
        mode: "async",
        status: "completed",
        description: "Resolve dependencies",
      }),
    )

    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(false)
    expect(state.workGraph.itemsById["task-8"]?.status).toBe("completed")
    expect(slotSpy).toHaveBeenCalledTimes(2)
    const finalToast = String(slotSpy.mock.calls[1]?.[1] ?? "")
    expect(finalToast).toContain("[subagent] completed")
  })

  it("keeps failed toast sticky before expiry and removes it after ttl", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-02-13T00:00:00Z"))
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }

    controller.applyEvent(event(1, "task_event", { task_id: "task-9", status: "running", description: "Parse logs" }))
    controller.applyEvent(event(2, "task_event", { task_id: "task-9", status: "failed", description: "Parse logs" }))
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(true)

    vi.advanceTimersByTime(2_000)
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(true)

    vi.advanceTimersByTime(2_300)
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(false)
  })

  it("expires subagent toast deterministically with controlled clock", () => {
    const clock = new ControlledClock(10_000)
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
      clock,
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }

    controller.applyEvent(event(1, "task_event", { task_id: "task-10", status: "failed", description: "Parse logs" }))
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(true)

    clock.advance(2_000)
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(true)

    clock.advance(2_300)
    expect(controller.getState().liveSlots.some((slot: any) => String(slot.text).includes("[subagent] failed"))).toBe(false)
  })

  it("absorbs concurrent async task bursts with deterministic workgraph caps", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/test_simple_native.yaml",
      workspace: ".",
    }) as any
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: false,
      subagentMaxWorkItems: 12,
    }

    for (let i = 1; i <= 20; i += 1) {
      controller.applyEvent(
        event(i, "task_event", {
          task_id: `task-${i}`,
          mode: "async",
          status: "running",
          description: `fanout-${i}`,
        }),
      )
    }

    const state = controller.getState()
    expect(state.toolEvents.some((entry: any) => String(entry.text).startsWith("[task]"))).toBe(false)
    expect(state.workGraph.itemOrder.length).toBe(12)
    expect(state.workGraph.itemOrder).toContain("task-20")
    expect(state.workGraph.itemOrder).not.toContain("task-1")
    expect(state.workGraph.itemOrder).not.toContain("task-2")
  })
})
