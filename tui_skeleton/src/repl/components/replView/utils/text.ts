import { CHALK, COLORS, GLYPHS } from "../theme.js"

export const clearToEnd = (value: string): string => `${value}\u001b[K`

export const stripCommandQuotes = (value: string | undefined): string | undefined => {
  if (!value) return undefined
  const trimmed = value.trim()
  if (trimmed.length >= 2) {
    const first = trimmed[0]
    const last = trimmed[trimmed.length - 1]
    if ((first === "\"" && last === "\"") || (first === "'" && last === "'")) {
      return trimmed.slice(1, -1)
    }
  }
  return trimmed
}

export const normalizeNewlines = (value: string): string => value.replace(/\r\n?/g, "\n")

export const measureBytes = (value: string): number => Buffer.byteLength(value, "utf8")

export const makeSnippet = (content: string, headLines: number, tailLines: number): string => {
  const lines = content.split("\n")
  if (lines.length <= headLines + tailLines) {
    return content
  }
  const head = lines.slice(0, headLines)
  const tail = lines.slice(-tailLines)
  const hiddenCount = Math.max(0, lines.length - head.length - tail.length)
  if (hiddenCount <= 0) {
    return content
  }
  return [...head, "", `${GLYPHS.ellipsis} (truncated) ${GLYPHS.ellipsis}`, "", ...tail].join("\n")
}

export const guessFenceLang = (filePath: string): string => {
  const match = /\.([a-zA-Z0-9]+)$/.exec(filePath)
  return match ? match[1].toLowerCase() : "text"
}

export const normalizeSessionPath = (value: string): string => {
  if (!value) return "."
  const withSlashes = value.replace(/\\/g, "/")
  const trimmedLeading = withSlashes.replace(/^\.\/+/, "")
  const collapsed = trimmedLeading.replace(/\/+/g, "/")
  const strippedTrailing = collapsed.replace(/\/$/, "")
  return strippedTrailing === "" ? "." : strippedTrailing
}

export const parseAtMentionQuery = (query: string): { cwd: string; needle: string } => {
  const normalized = query.replace(/^\.\/+/, "")
  const lastSlash = normalized.lastIndexOf("/")
  if (lastSlash < 0) {
    return { cwd: ".", needle: normalized }
  }
  const cwd = normalized.slice(0, lastSlash).replace(/\/+$/, "") || "."
  const needle = normalized.slice(lastSlash + 1)
  return { cwd, needle }
}

export const longestCommonPrefix = (values: ReadonlyArray<string>): string => {
  if (values.length === 0) return ""
  let prefix = values[0] ?? ""
  for (let index = 1; index < values.length; index += 1) {
    const value = values[index] ?? ""
    let length = 0
    const max = Math.min(prefix.length, value.length)
    while (length < max && prefix[length] === value[length]) {
      length += 1
    }
    prefix = prefix.slice(0, length)
    if (!prefix) break
  }
  return prefix
}

export const hashString = (value: string): number => {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i)
    hash |= 0
  }
  return Math.abs(hash)
}

export const findFuzzyMatchIndices = (text: string, query: string): number[] | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  const haystack = text.toLowerCase()
  const indices: number[] = []
  let lastIndex = -1
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    const idx = haystack.indexOf(ch, lastIndex + 1)
    if (idx === -1) return null
    indices.push(idx)
    lastIndex = idx
  }
  return indices
}

export const highlightFuzzyLabel = (label: string, command: string, query: string): string => {
  if (!query.trim()) return label
  const matches = findFuzzyMatchIndices(command, query)
  if (!matches || matches.length === 0) return label
  const matchSet = new Set(matches)
  let out = ""
  for (let i = 0; i < label.length; i += 1) {
    const ch = label[i]
    if (i < command.length && matchSet.has(i)) {
      out += CHALK.bold.hex(COLORS.info)(ch)
    } else {
      out += ch
    }
  }
  return out
}
