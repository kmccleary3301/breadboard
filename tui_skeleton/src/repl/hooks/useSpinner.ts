import chalk from "chalk"
import { useAnimationClock } from "./useAnimationClock.js"

const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"] as const
const INTERVAL_MS = 120
const COLOR = "#14b8a6"

export const useSpinner = (active: boolean, sharedTick?: number): string => {
  const internalTick = useAnimationClock(active && sharedTick === undefined, INTERVAL_MS)
  const tick = sharedTick ?? internalTick
  const frame = active ? FRAMES[tick % FRAMES.length] : "●"
  return chalk.hex(COLOR)(frame)
}
