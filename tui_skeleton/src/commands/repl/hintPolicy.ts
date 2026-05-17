const normalizeHint = (hint: string): string => hint.trim()

export const isLifecycleNoiseHint = (hint: string): boolean => {
  const text = normalizeHint(String(hint ?? ""))
  if (!text) return false
  return /^(?:Log link available\.?|Run finished(?:[ (.]|$)|✻ (?:Cooked for|Completed|Halted)\b)/.test(text)
}

export const filterReadableHints = (hints: readonly string[]): string[] =>
  hints.filter((hint) => !isLifecycleNoiseHint(hint))
