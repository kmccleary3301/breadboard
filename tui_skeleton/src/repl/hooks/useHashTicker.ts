import { useEffect, useMemo, useState } from "react"
import chalk from "chalk"
import chroma from "chroma-js"

const HASH_LENGTH = 14
const INTERVAL_MS = 1200
const GRADIENT = chroma.scale(["#818cf8", "#22d3ee", "#34d399", "#fbbf24", "#f472b6"]).mode("lab")

const randomHash = (): string => {
  const base = Date.now().toString(16)
  const random = Math.random().toString(36).slice(2)
  return (base + random).slice(0, HASH_LENGTH).padEnd(HASH_LENGTH, "0")
}

const applyGradient = (value: string): string =>
  value
    .split("")
    .map((char, index, array) => {
      const ratio = array.length <= 1 ? 0 : index / (array.length - 1)
      return chalk.hex(GRADIENT(ratio).hex())(char)
    })
    .join("")

export const useHashTicker = (): string => {
  const [hash, setHash] = useState<string>(() => randomHash())

  useEffect(() => {
    const id = setInterval(() => setHash(randomHash()), INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  return useMemo(() => applyGradient(hash), [hash])
}
