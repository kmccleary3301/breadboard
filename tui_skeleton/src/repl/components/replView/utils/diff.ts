import { CHALK, COLORS } from "../theme.js"

export interface DiffSection {
  readonly file: string
  readonly lines: ReadonlyArray<string>
}

const DIFF_SECTION_PATTERN = /^diff --git a\/(.+?) b\/(.+)$/
const DIFF_FALLBACK_NEW = "+++ b/"
const DIFF_FALLBACK_OLD = "--- a/"

export const splitUnifiedDiff = (diffText: string): DiffSection[] => {
  const normalized = diffText.replace(/\r\n?/g, "\n").trimEnd()
  if (!normalized) return []
  const lines = normalized.split("\n")
  const sections: DiffSection[] = []
  let current: { file: string; lines: string[] } | null = null

  const pushCurrent = () => {
    if (!current) return
    if (current.lines.some((line) => line.trim().length > 0)) {
      sections.push({ file: current.file, lines: [...current.lines] })
    }
    current = null
  }

  for (const line of lines) {
    const match = line.match(DIFF_SECTION_PATTERN)
    if (match) {
      pushCurrent()
      const file = match[2] ?? match[1] ?? "diff"
      current = { file, lines: [line] }
      continue
    }
    if (!current) {
      current = { file: "diff", lines: [] }
    }
    current.lines.push(line)
  }
  pushCurrent()

  if (sections.length === 1 && sections[0]?.file === "diff") {
    const fallback =
      lines.find((line) => line.startsWith(DIFF_FALLBACK_NEW))?.slice(DIFF_FALLBACK_NEW.length).trim()
      ?? lines.find((line) => line.startsWith(DIFF_FALLBACK_OLD))?.slice(DIFF_FALLBACK_OLD.length).trim()
      ?? "diff"
    sections[0] = { file: fallback || "diff", lines: sections[0].lines }
  }

  return sections.length > 0 ? sections : [{ file: "diff", lines }]
}

export const formatIsoTimestamp = (ms: number): string => {
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return "unknown time"
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z")
}

export const colorizeDiffHeaderLine = (line: string): string => {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return CHALK.hex(COLORS.info)(line)
  if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
  if (line.startsWith("+")) return CHALK.bgHex(COLORS.panel).hex(COLORS.success)(line)
  if (line.startsWith("-")) return CHALK.bgHex(COLORS.panel).hex(COLORS.error)(line)
  return line
}

export const colorizeDiffLine = (line: string): string => {
  if (!line) return line
  if (line.startsWith("+")) return CHALK.hex(COLORS.success)(line)
  if (line.startsWith("-")) return CHALK.hex(COLORS.error)(line)
  if (line.startsWith("@@")) return CHALK.hex(COLORS.info)(line)
  return CHALK.hex(COLORS.text)(line)
}
