import { useEffect, useMemo, useState } from "react"
import chalk from "chalk"
import chroma from "chroma-js"

const HASH_LENGTH = 16
const TICK_INTERVAL_MS = 1200
const GRADIENT_COLORS = ["#10b981", "#f97316", "#ec4899"]

const randomHash = (): string => {
  const timestamp = Date.now().toString(16).slice(-6)
  const randomPart = Math.random().toString(36).replace(/[^a-z0-9]/gi, "")
  return (timestamp + randomPart).slice(0, HASH_LENGTH).padEnd(HASH_LENGTH, "0")
}

const gradientize = (value: string): string => {
  const scale = chroma.scale(GRADIENT_COLORS).mode("lab")
  return value
    .split("")
    .map((char, index, array) => {
      const ratio = array.length <= 1 ? 0 : index / (array.length - 1)
      const color = scale(ratio).hex()
      return chalk.hex(color)(char)
    })
    .join("")
}

export const useHashTicker = (): string => {
  const [hash, setHash] = useState<string>(() => randomHash())

  useEffect(() => {
    const id = setInterval(() => {
      setHash(randomHash())
    }, TICK_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  return useMemo(() => gradientize(hash), [hash])
}

