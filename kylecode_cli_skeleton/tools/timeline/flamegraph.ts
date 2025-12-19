import { promises as fs } from "node:fs"

interface TimelineEntry {
  readonly t: number | null
  readonly stream: string
}

const CHAR_MAP = [" ", ".", "·", "o", "O", "#"]

export const buildFlamegraph = async (
  timelinePath: string,
  outputPath: string,
  binSizeMs = 50,
) => {
  let raw: string
  try {
    raw = await fs.readFile(timelinePath, "utf8")
  } catch (error) {
    await fs.writeFile(outputPath, `Timeline not available: ${(error as Error).message}\n`, "utf8")
    return
  }
  const entries: TimelineEntry[] = raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => {
      try {
        const value = JSON.parse(line) as TimelineEntry
        return value
      } catch {
        return { t: null, stream: "unknown" }
      }
    })

  const numericEntries = entries.filter((entry) => typeof entry.t === "number" && Number.isFinite(entry.t)) as Array<{
    t: number
    stream: string
  }>
  if (numericEntries.length === 0) {
    await fs.writeFile(outputPath, "Timeline flamegraph unavailable (no numeric timestamps).\n", "utf8")
    return
  }
  const maxTime = Math.max(...numericEntries.map((entry) => entry.t))
  const binSizeSeconds = binSizeMs / 1000
  const binCount = Math.max(1, Math.ceil(maxTime / binSizeSeconds))
  const streams = Array.from(new Set(numericEntries.map((entry) => entry.stream)))
  const counts = new Map<string, number[]>(streams.map((stream) => [stream, Array(binCount).fill(0)]))
  for (const entry of numericEntries) {
    const bins = counts.get(entry.stream)
    if (!bins) continue
    const index = Math.min(binCount - 1, Math.max(0, Math.floor(entry.t / binSizeSeconds)))
    bins[index] += 1
  }

  const lines: string[] = []
  lines.push(`Timeline flamegraph (duration ${maxTime.toFixed(2)}s, bin ${binSizeMs}ms)`) 
  for (const stream of streams) {
    const bins = counts.get(stream) ?? []
    const chars = bins
      .map((value) => {
        const level = Math.min(CHAR_MAP.length - 1, value)
        return CHAR_MAP[level]
      })
      .join("")
    lines.push(`${stream.padEnd(6, " ")}|${chars}|`)
  }
  lines.push("Legend: ' ' none, '.' few, '·' moderate, 'o' busy, 'O' very busy, '#' saturated")
  await fs.writeFile(outputPath, `${lines.join("\n")}\n`, "utf8")
}
