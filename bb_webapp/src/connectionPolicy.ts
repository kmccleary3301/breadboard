export type ConnectionMode = "local" | "sandbox" | "remote"
export type TokenStoragePolicy = "persisted" | "session"

export type ConnectionSettingsByMode = Record<ConnectionMode, { baseUrl: string; token: string }>

export const DEFAULT_BASE_URLS: Record<ConnectionMode, string> = {
  local: "http://127.0.0.1:9099",
  sandbox: "http://127.0.0.1:9099",
  remote: "",
}

export const shouldSendAuthorizationHeader = (mode: ConnectionMode): boolean => mode === "remote"

export const sanitizeTokenForStorage = (policy: TokenStoragePolicy, token: string): string => {
  if (policy === "session") return ""
  return token
}

export const resolveClientAuthToken = (mode: ConnectionMode, token: string): string | undefined => {
  const value = token.trim()
  if (!value) return undefined
  return shouldSendAuthorizationHeader(mode) ? value : undefined
}
