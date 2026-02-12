export type InlineSpan = { start: number; end: number }

const TOKEN_REGEX = /(\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]+)/gu

const tokenizeForDiff = (value: string): string[] => {
  if (!value) return []
  const tokens = value.match(TOKEN_REGEX)
  return tokens && tokens.length > 0 ? tokens : [value]
}

export const mergeSpans = (spans: InlineSpan[]): InlineSpan[] => {
  if (spans.length <= 1) return spans
  const sorted = spans.slice().sort((a, b) => a.start - b.start)
  const merged: InlineSpan[] = [sorted[0]]
  for (let i = 1; i < sorted.length; i += 1) {
    const current = sorted[i]
    const last = merged[merged.length - 1]
    if (current.start <= last.end) {
      last.end = Math.max(last.end, current.end)
    } else {
      merged.push({ ...current })
    }
  }
  return merged
}

export const shiftSpans = (spans: InlineSpan[], offset: number): InlineSpan[] => {
  if (!offset) return spans
  return spans.map((span) => ({ start: span.start + offset, end: span.end + offset }))
}

const buildKeepMap = (a: string[], b: string[]): { keepA: boolean[]; keepB: boolean[] } => {
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const keepA = new Array(n).fill(false)
  const keepB = new Array(m).fill(false)
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      keepA[i] = true
      keepB[j] = true
      i += 1
      j += 1
      continue
    }
    if (dp[i + 1][j] >= dp[i][j + 1]) {
      i += 1
    } else {
      j += 1
    }
  }
  return { keepA, keepB }
}

const spansFromTokens = (tokens: string[], keep: boolean[]): InlineSpan[] => {
  const spans: InlineSpan[] = []
  let pos = 0
  for (let idx = 0; idx < tokens.length; idx += 1) {
    const token = tokens[idx]
    const next = pos + token.length
    if (!keep[idx] && token.length > 0 && !/^\s+$/.test(token)) {
      spans.push({ start: pos, end: next })
    }
    pos = next
  }
  return mergeSpans(spans)
}

export const computeInlineDiffSpans = (
  before: string,
  after: string,
): { del: InlineSpan[]; add: InlineSpan[] } => {
  if (!before && !after) return { del: [], add: [] }
  if (before === after) return { del: [], add: [] }

  const a = tokenizeForDiff(before)
  const b = tokenizeForDiff(after)

  if (a.length === 0 && b.length === 0) return { del: [], add: [] }
  if (a.length === 0) return { del: [], add: after ? [{ start: 0, end: after.length }] : [] }
  if (b.length === 0) return { del: before ? [{ start: 0, end: before.length }] : [], add: [] }

  const { keepA, keepB } = buildKeepMap(a, b)
  return {
    del: spansFromTokens(a, keepA),
    add: spansFromTokens(b, keepB),
  }
}
