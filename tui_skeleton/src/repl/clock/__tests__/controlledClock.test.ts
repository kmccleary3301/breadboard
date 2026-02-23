import { describe, expect, it, vi } from "vitest"
import { ControlledClock } from "../controlledClock.js"

describe("ControlledClock", () => {
  it("fires timeout callbacks only after deterministic advance", () => {
    const clock = new ControlledClock(1_000)
    const callback = vi.fn()
    clock.setTimeout(callback, 500)

    clock.advance(499)
    expect(callback).not.toHaveBeenCalled()

    clock.advance(1)
    expect(callback).toHaveBeenCalledTimes(1)
    expect(clock.now()).toBe(1_500)
  })

  it("drives shared animation ticks through manual advance", () => {
    const clock = new ControlledClock(0)
    const seenTicks: number[] = []
    const unsubscribe = clock.subscribeAnimationTick(120, (tick) => {
      seenTicks.push(tick)
    })

    clock.advance(119)
    expect(seenTicks).toEqual([])

    clock.advance(1)
    expect(seenTicks).toEqual([1])

    clock.advance(240)
    expect(seenTicks).toEqual([1, 2, 3])

    unsubscribe()
    clock.advance(360)
    expect(seenTicks).toEqual([1, 2, 3])
  })
})

