export const normalizeBaseUrl = (value: string): string => {
  const trimmed = value.trim()
  if (!trimmed) {
    throw new Error("URL is empty.")
  }
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`
  const parsed = new URL(withScheme)
  if (!parsed.hostname) {
    throw new Error(`Invalid URL: ${value}`)
  }
  return parsed.toString().replace(/\/$/, "")
}
