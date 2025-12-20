import chalk from "chalk"
import chroma from "chroma-js"

export const Gradients = {
  // Crush-like duo: purple → cyan
  crush: ["#B36BFF", "#7CF2FF"] as const,
  // Alternates
  candy: ["#FF6EC7", "#7C83FD"] as const,
  limeOcean: ["#00D38D", "#2BD9FE"] as const,
} as const

const seg =
  typeof (Intl as any).Segmenter !== "undefined" ? new (Intl as any).Segmenter(undefined, { granularity: "grapheme" }) : null

export function splitGraphemes(input: string): string[] {
  if (!input) return []
  if (seg && typeof seg.segment === "function") {
    const out: string[] = []
    for (const s of seg.segment(input)) out.push(s.segment)
    return out
  }
  return Array.from(input)
}

export function applyForegroundGradient(input: string, stops: readonly string[], bold = false): string {
  if (!input) return ""
  const clusters = splitGraphemes(input)
  const scale = chroma.scale(stops as string[]).mode("lch")
  const n = clusters.length
  return clusters
    .map((g, i) => {
      const t = n === 1 ? 0 : i / (n - 1)
      const hex = scale(t).hex()
      return bold ? chalk.bold.hex(hex)(g) : chalk.hex(hex)(g)
    })
    .join("")
}

export function gradientLines(lines: string[], stops: readonly string[], bold = false): string[] {
  return lines.map((l) => applyForegroundGradient(l, stops, bold))
}

