import type { SessionFileInfo } from "../api/types.js"

export const scoreFuzzyMatch = (candidate: string, query: string): number | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return 0
  const haystack = candidate.toLowerCase()
  let score = 0
  let lastIndex = -1
  let consecutive = 0
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    if (!ch) continue
    const index = haystack.indexOf(ch, lastIndex + 1)
    if (index === -1) return null
    score += 10
    const prevChar = index > 0 ? haystack[index - 1] : ""
    if (index === 0 || "/_-.".includes(prevChar)) {
      score += 8
    }
    if (index === lastIndex + 1) {
      consecutive += 1
      score += 12 + consecutive
    } else {
      consecutive = 0
      score -= Math.max(0, index - lastIndex - 1)
    }
    lastIndex = index
  }
  score += Math.max(0, 30 - haystack.length)
  return score
}

export const rankFuzzyFileItems = (
  items: ReadonlyArray<SessionFileInfo>,
  query: string,
  limit: number,
  display: (item: SessionFileInfo) => string,
): ReadonlyArray<SessionFileInfo> => {
  const needle = query.trim()
  if (!needle) return items.slice(0, Math.max(0, limit))
  const needleLower = needle.toLowerCase()
  const scored: Array<{ item: SessionFileInfo; score: number }> = []
  for (const item of items) {
    const label = display(item)
    const score = scoreFuzzyMatch(label, needle)
    if (score == null) continue
    const base = label.split("/").pop() ?? label
    const baseScore = scoreFuzzyMatch(base, needle)
    const labelLower = label.toLowerCase()
    const baseLower = base.toLowerCase()
    let boosted = score + (baseScore != null ? baseScore * 1.2 + 20 : 0)
    if (labelLower.startsWith(needleLower)) boosted += 24
    if (baseLower.startsWith(needleLower)) boosted += 36
    const depthPenalty = Math.max(0, label.split("/").length - 1) * 1.5
    boosted -= depthPenalty
    if (item.type === "directory") boosted -= 18
    scored.push({ item, score: boosted })
  }
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    if (a.item.type !== b.item.type) return a.item.type === "file" ? -1 : 1
    const aLen = a.item.path.length
    const bLen = b.item.path.length
    if (aLen !== bLen) return aLen - bLen
    return a.item.path.localeCompare(b.item.path)
  })
  return scored.slice(0, Math.max(0, limit)).map((entry) => entry.item)
}
