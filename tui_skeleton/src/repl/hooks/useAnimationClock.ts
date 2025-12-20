import { useEffect, useState } from "react"

interface ClockState {
  readonly interval: number
  tick: number
  timer: NodeJS.Timeout | null
  readonly listeners: Set<(tick: number) => void>
}

const clockMap = new Map<number, ClockState>()

const getClock = (interval: number): ClockState => {
  let clock = clockMap.get(interval)
  if (!clock) {
    clock = { interval, tick: 0, timer: null, listeners: new Set() }
    clockMap.set(interval, clock)
  }
  return clock
}

const startClock = (clock: ClockState) => {
  if (clock.timer) return
  clock.timer = setInterval(() => {
    clock.tick += 1
    for (const listener of clock.listeners) {
      listener(clock.tick)
    }
  }, clock.interval)
}

const stopClock = (interval: number) => {
  const clock = clockMap.get(interval)
  if (!clock) return
  if (clock.listeners.size === 0 && clock.timer) {
    clearInterval(clock.timer)
    clock.timer = null
    clockMap.delete(interval)
  }
}

export const useAnimationClock = (enabled: boolean, intervalMs = 150): number => {
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!enabled) {
      setTick(0)
      return
    }
    const clock = getClock(intervalMs)
    clock.listeners.add(setTick)
    startClock(clock)
    return () => {
      clock.listeners.delete(setTick)
      stopClock(intervalMs)
    }
  }, [enabled, intervalMs])

  return enabled ? tick : 0
}
