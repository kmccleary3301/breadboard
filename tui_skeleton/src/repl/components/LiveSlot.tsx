import React from "react"
import { Text } from "ink"
import chalk from "chalk"
import type { LiveSlotEntry } from "../types.js"
import {
  NEUTRAL_COLORS,
  SEMANTIC_COLORS,
  STATUS_SPINNER_FRAMES,
  resolveAsciiOnly,
  resolveIcons,
  resolveColorMode,
} from "../designSystem.js"

const spinnerFrames = STATUS_SPINNER_FRAMES
const asciiOnly = resolveAsciiOnly()
const icons = resolveIcons(asciiOnly)
const colorMode = resolveColorMode()
if (colorMode === "none") {
  ;(chalk as typeof chalk & { level: number }).level = 0
}

interface LiveSlotProps {
  readonly slot: LiveSlotEntry
  readonly index: number
  readonly tick: number
}

export const LiveSlot: React.FC<LiveSlotProps> = ({ slot, index, tick }) => {
  const frame = spinnerFrames[(index + tick) % spinnerFrames.length]
  let glyph: string
  if (slot.status === "success") {
    glyph = chalk.hex(SEMANTIC_COLORS.success)(icons.bullet)
  } else if (slot.status === "error") {
    glyph = chalk.hex(SEMANTIC_COLORS.error)(icons.bullet)
  } else {
    const spinnerColor = slot.color ? chalk.hex(slot.color) : chalk.hex(NEUTRAL_COLORS.midGray)
    glyph = spinnerColor(frame)
  }
  const colorize = slot.color ? chalk.hex(slot.color) : chalk.hex(NEUTRAL_COLORS.offWhite)
  return (
    <>
      <Text>
        {glyph}{" "}
        {colorize(slot.text)}
      </Text>
      {slot.summary ? (
        <Text color="gray">
          {chalk.dim(" ".repeat(2))}
          {slot.summary}
        </Text>
      ) : null}
    </>
  )
}
