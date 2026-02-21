const UNSAFE_PATTERNS: RegExp[] = [
  /<\s*script\b/i,
  /<\s*iframe\b/i,
  /<\s*object\b/i,
  /<\s*embed\b/i,
  /<[^>]+\son\w+\s*=/i,
  /\[[^\]]*]\(\s*(javascript:|vbscript:)/i,
  /\[[^\]]*]\(\s*data:text\/html/i,
  /(href|src)\s*=\s*["']?\s*(javascript:|vbscript:|data:text\/html)/i,
]

export const hasUnsafeMarkdownContent = (text: string): boolean => {
  for (const pattern of UNSAFE_PATTERNS) {
    if (pattern.test(text)) return true
  }
  return false
}
