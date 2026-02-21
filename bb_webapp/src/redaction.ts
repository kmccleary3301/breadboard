const SENSITIVE_KEY_PATTERN = /(authorization|token|secret|password|api[-_]?key|bearer|cookie|set-cookie|session)/i

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

export const redactSensitiveValue = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map((entry) => redactSensitiveValue(entry))
  }
  if (!isRecord(value)) return value
  const out: Record<string, unknown> = {}
  for (const [key, entry] of Object.entries(value)) {
    if (SENSITIVE_KEY_PATTERN.test(key)) {
      out[key] = "[REDACTED]"
      continue
    }
    out[key] = redactSensitiveValue(entry)
  }
  return out
}
