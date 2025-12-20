import React from "react"
import { Text } from "ink"
import chalk from "chalk"
import type { LiveSlotEntry } from "../types.js"

const spinnerFrames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"] as const

interface LiveSlotProps {
  readonly slot: LiveSlotEntry
  readonly index: number
  readonly tick: number
}

export const LiveSlot: React.FC<LiveSlotProps> = ({ slot, index, tick }) => {
  const frame = spinnerFrames[(index + tick) % spinnerFrames.length]
  let glyph: string
  if (slot.status === "success") {
    glyph = chalk.hex("#34D399")("●")
  } else if (slot.status === "error") {
    glyph = chalk.hex("#F87171")("●")
  } else {
    const spinnerColor = slot.color ? chalk.hex(slot.color) : chalk.hex("#94A3B8")
    glyph = spinnerColor(frame)
  }
  const colorize = slot.color ? chalk.hex(slot.color) : chalk.white
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
