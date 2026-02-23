import { useEffect, useState } from "react"
import { useUIClock } from "../clock/context.js"

export const useAnimationClock = (enabled: boolean, intervalMs = 150): number => {
  const clock = useUIClock()
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!enabled) {
      setTick(0)
      return
    }
    setTick(0)
    return clock.subscribeAnimationTick(intervalMs, setTick)
  }, [clock, enabled, intervalMs])

  return enabled ? tick : 0
}
