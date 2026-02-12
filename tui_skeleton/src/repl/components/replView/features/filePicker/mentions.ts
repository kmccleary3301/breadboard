import type { ActiveAtMention } from "./types.js"

export const findActiveAtMention = (value: string, cursor: number): ActiveAtMention | null => {
  const safeCursor = Math.max(0, Math.min(cursor, value.length))
  if (value.length === 0) return null
  for (let start = safeCursor - 1; start >= 0; start -= 1) {
    if (value[start] !== "@") continue
    if (start > 0 && !/\s/.test(value[start - 1] ?? "")) continue
    const quoted = value[start + 1] === "\""
    if (quoted) {
      const quoteStart = start + 2
      const closingQuote = value.indexOf("\"", quoteStart)
      const end = closingQuote >= 0 ? closingQuote + 1 : value.length
      if (safeCursor > end) continue
      const query = value.slice(quoteStart, Math.max(quoteStart, safeCursor))
      return { start, end, query, quoted: true }
    }
    const nextWhitespace = value.slice(start + 1).search(/\s/)
    const end = nextWhitespace >= 0 ? start + 1 + nextWhitespace : value.length
    if (safeCursor > end) continue
    const query = value.slice(start + 1, Math.min(end, safeCursor))
    return { start, end, query, quoted: false }
  }
  return null
}
