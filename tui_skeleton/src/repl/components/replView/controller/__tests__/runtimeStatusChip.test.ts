import { describe, expect, it } from "vitest"
import { resolveRuntimeStatusChipTransition, type RuntimeStatusChip } from "../runtimeStatusChip.js"

const CHIP = (overrides: Partial<RuntimeStatusChip> = {}): RuntimeStatusChip => ({
  id: "thinking",
  label: "thinking",
  tone: "info",
  priority: 60,
  updatedAt: 1_000,
  ...overrides,
})

describe("runtimeStatusChip transitions", () => {
  it("returns noop when target matches current", () => {
    const current = CHIP()
    const target = CHIP()
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_050,
      hysteresisMs: 240,
    })
    expect(decision).toEqual({ kind: "noop" })
  })

  it("preserves updatedAt for same-id metadata updates", () => {
    const current = CHIP({ label: "thinking", tone: "info", updatedAt: 1_000 })
    const target = CHIP({ label: "responding", tone: "info", updatedAt: 1_150 })
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_150,
      hysteresisMs: 240,
    })
    expect(decision.kind).toBe("set")
    if (decision.kind !== "set") return
    expect(decision.chip.label).toBe("responding")
    expect(decision.chip.updatedAt).toBe(1_000)
  })

  it("schedules non-critical id transitions until hysteresis is satisfied", () => {
    const current = CHIP({ id: "run", label: "run", priority: 55, updatedAt: 1_000 })
    const target = CHIP({ id: "responding", label: "responding", priority: 65, updatedAt: 1_050 })
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_120,
      hysteresisMs: 240,
    })
    expect(decision.kind).toBe("schedule")
    if (decision.kind !== "schedule") return
    expect(decision.delayMs).toBe(120)
    expect(decision.chip.id).toBe("responding")
  })

  it("applies non-critical id transitions immediately after hysteresis", () => {
    const current = CHIP({ id: "run", label: "run", priority: 55, updatedAt: 1_000 })
    const target = CHIP({ id: "responding", label: "responding", priority: 65, updatedAt: 1_260 })
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_260,
      hysteresisMs: 240,
    })
    expect(decision.kind).toBe("set")
    if (decision.kind !== "set") return
    expect(decision.chip.id).toBe("responding")
  })

  it("applies critical transitions immediately even inside hysteresis", () => {
    const current = CHIP({ id: "responding", label: "responding", tone: "info", priority: 65, updatedAt: 1_000 })
    const target = CHIP({ id: "error", label: "error", tone: "error", priority: 95, updatedAt: 1_050 })
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_060,
      hysteresisMs: 240,
    })
    expect(decision.kind).toBe("set")
    if (decision.kind !== "set") return
    expect(decision.chip.id).toBe("error")
  })
})
