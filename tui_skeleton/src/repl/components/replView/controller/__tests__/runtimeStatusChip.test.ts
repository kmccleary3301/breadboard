import { describe, expect, it } from "vitest"
import {
  mapActivityToRuntimeChip,
  mapRuntimePhaseLineState,
  resolveRuntimeStatusChipTransition,
  type RuntimeStatusChip,
} from "../runtimeStatusChip.js"

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

  it("holds transition away from critical states until hysteresis passes", () => {
    const current = CHIP({ id: "halted", label: "halted", tone: "error", priority: 96, updatedAt: 1_000 })
    const target = CHIP({ id: "responding", label: "responding", tone: "info", priority: 65, updatedAt: 1_020 })
    const decision = resolveRuntimeStatusChipTransition({
      current,
      target,
      nowMs: 1_050,
      hysteresisMs: 240,
    })
    expect(decision.kind).toBe("schedule")
    if (decision.kind !== "schedule") return
    expect(decision.delayMs).toBe(190)
  })
})

describe("mapRuntimePhaseLineState", () => {
  it("prioritizes disconnected and returns error tone", () => {
    const phase = mapRuntimePhaseLineState({
      disconnected: true,
      pendingResponse: false,
      status: "Ready",
      runtimeStatusChip: CHIP({ id: "responding", label: "responding", tone: "info" }),
    })
    expect(phase).toEqual({ id: "disconnected", label: "disconnected", tone: "error" })
  })

  it("uses canonical runtime chip ids over freeform chip labels", () => {
    const phase = mapRuntimePhaseLineState({
      disconnected: false,
      pendingResponse: false,
      status: "Ready",
      runtimeStatusChip: CHIP({ id: "responding", label: "Responding session...", tone: "info" }),
    })
    expect(phase).toEqual({ id: "responding", label: "responding", tone: "info" })
  })

  it("falls back to pendingResponse=thinking when no chip is present", () => {
    const phase = mapRuntimePhaseLineState({
      disconnected: false,
      pendingResponse: true,
      status: "Ready",
      runtimeStatusChip: null,
    })
    expect(phase).toEqual({ id: "thinking", label: "thinking", tone: "info" })
  })

  it("maps status strings to canonical labels when chip is absent", () => {
    const phase = mapRuntimePhaseLineState({
      disconnected: false,
      pendingResponse: false,
      status: "Starting session...",
      runtimeStatusChip: null,
    })
    expect(phase).toEqual({ id: "starting", label: "starting", tone: "info" })
  })

  it("maps interruption copy to interrupted phase", () => {
    const phase = mapRuntimePhaseLineState({
      disconnected: false,
      pendingResponse: false,
      status: "Interrupted by user",
      runtimeStatusChip: null,
    })
    expect(phase).toEqual({ id: "interrupted", label: "interrupted", tone: "error" })
  })
})

describe("mapActivityToRuntimeChip", () => {
  it("maps cancelled activity to interrupted phase chip", () => {
    const chip = mapActivityToRuntimeChip(
      {
        primary: "cancelled",
        label: "cancelled",
        updatedAt: 100,
        displayedAt: 100,
        seq: 1,
      },
      false,
      false,
      1_500,
    )
    expect(chip?.id).toBe("interrupted")
    expect(chip?.tone).toBe("error")
  })
})
