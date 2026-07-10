export interface HistorySearchCursor {
  readonly query: string
  readonly nextIndex: number
  readonly lastApplied: string
  readonly lastIndex: number
  readonly direction: -1 | 1
}

export interface HistorySearchResult {
  readonly cursor: HistorySearchCursor
  readonly entry: string
  readonly index: number
}

export const findHistorySearchMatch = (
  entries: readonly string[],
  input: string,
  direction: -1 | 1,
  activeCursor: HistorySearchCursor | null,
): HistorySearchResult | null => {
  if (entries.length === 0) return null
  const active = activeCursor?.lastApplied === input ? activeCursor : null
  const query = active ? active.query : input.trim()
  const firstIndex = active
    ? active.direction === direction
      ? active.nextIndex
      : active.lastIndex + direction
    : direction === -1
      ? entries.length - 1
      : 0
  const matches = (entry: string) => query.length === 0 || entry.toLowerCase().includes(query.toLowerCase())
  let foundIndex = -1
  if (direction === -1) {
    for (let index = Math.min(firstIndex, entries.length - 1); index >= 0; index -= 1) {
      if (matches(entries[index])) {
        foundIndex = index
        break
      }
    }
  } else {
    for (let index = Math.max(firstIndex, 0); index < entries.length; index += 1) {
      if (matches(entries[index])) {
        foundIndex = index
        break
      }
    }
  }
  if (foundIndex < 0) return null
  const entry = entries[foundIndex]
  return {
    entry,
    index: foundIndex,
    cursor: {
      query,
      nextIndex: foundIndex + direction,
      lastApplied: entry,
      lastIndex: foundIndex,
      direction,
    },
  }
}
