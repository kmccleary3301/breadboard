const TRUE_TOKENS: Record<string, true> = { "1": true, true: true, yes: true, on: true }
const FALSE_TOKENS: Record<string, true> = { "0": true, false: true, no: true, off: true }

const hasToken = (tokens: Record<string, true>, value: string): boolean =>
  Object.prototype.hasOwnProperty.call(tokens, value)

export const parseBooleanLikeValue = (value: unknown): boolean | undefined => {
  if (typeof value === "boolean") return value
  if (typeof value !== "string") return undefined
  const normalized = value.trim().toLowerCase()
  if (hasToken(TRUE_TOKENS, normalized)) return true
  if (hasToken(FALSE_TOKENS, normalized)) return false
  return undefined
}

export const parseBooleanEnv = (value: string | undefined, fallback: boolean): boolean =>
  parseBooleanLikeValue(value) ?? fallback

export const parseOptionalBooleanEnv = (value: string | undefined): boolean | null =>
  parseBooleanLikeValue(value) ?? null
