export interface WindowSlice<T> {
  readonly items: ReadonlyArray<T>
  readonly hiddenCount: number
  readonly usedLines: number
  readonly truncated: boolean
}

export const trimTailByLineCount = <T,>(
  entries: ReadonlyArray<T>,
  linesToTrim: number,
  measure: (entry: T) => number,
): ReadonlyArray<T> => {
  const trim = Math.max(0, Math.floor(linesToTrim))
  if (trim === 0 || entries.length === 0) return entries
  let remaining = trim
  let end = entries.length
  while (end > 0 && remaining > 0) {
    const cost = Math.max(1, measure(entries[end - 1]))
    remaining -= cost
    end -= 1
  }
  return entries.slice(0, Math.max(0, end))
}

export const sliceTailByLineBudget = <T,>(
  entries: ReadonlyArray<T>,
  budgetLines: number,
  measure: (entry: T) => number,
): WindowSlice<T> => {
  const budget = Math.max(0, Math.floor(budgetLines))
  if (entries.length === 0 || budget === 0) {
    return { items: [], hiddenCount: entries.length, usedLines: 0, truncated: false }
  }
  let used = 0
  let start = entries.length
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const cost = Math.max(1, measure(entries[index]))
    if (used + cost > budget && start < entries.length) {
      break
    }
    used += cost
    start = index
    if (used >= budget) break
  }
  let items = entries.slice(start)
  let hidden = start
  let truncated = hidden > 0
  if (truncated) {
    while (items.length > 1 && used + 1 > budget) {
      const removed = items[0]
      items = items.slice(1)
      used = Math.max(0, used - Math.max(1, measure(removed)))
      hidden += 1
    }
    truncated = hidden > 0 && items.length > 0
  }
  return { items, hiddenCount: hidden, usedLines: used + (truncated ? 1 : 0), truncated }
}
