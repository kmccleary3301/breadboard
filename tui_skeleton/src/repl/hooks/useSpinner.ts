import chalk from "chalk"
import { useAnimationClock } from "./useAnimationClock.js"
import { BRAND_COLORS } from "../designSystem.js"
import {
  BRAND_COLORS,
  STATUS_SPINNER_FRAMES,
  resolveAsciiOnly,
  resolveColorMode,
  resolveIcons,
} from "../designSystem.js"

const ASCII_ONLY = resolveAsciiOnly()
const ICONS = resolveIcons(ASCII_ONLY)
const FRAMES = ASCII_ONLY ? ["-", "\\", "|", "/"] : STATUS_SPINNER_FRAMES
const INTERVAL_MS = 150
const COLOR = BRAND_COLORS.duneOrange
const COLOR_MODE = resolveColorMode()
if (COLOR_MODE === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}

export const useSpinner = (active: boolean, sharedTick?: number): string => {
  const internalTick = useAnimationClock(active && sharedTick == null, INTERVAL_MS)
  const tick = sharedTick ?? internalTick
  const frame = active ? FRAMES[tick % FRAMES.length] : ICONS.bullet
  return chalk.hex(COLOR)(frame)
}
