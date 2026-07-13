import { loadUserConfigSync, writeUserConfig } from "./userConfig.js"
import { deleteKeychainAuthToken, getKeychainAuthToken } from "./keychain.js"
import { normalizeBaseUrl } from "./baseUrl.js"

const unique = (values: readonly string[]): string[] => [...new Set(values)]

const isSameNormalizedBaseUrl = (left?: string, right?: string): boolean => {
  if (!left || !right) return false
  try {
    return normalizeBaseUrl(left) === normalizeBaseUrl(right)
  } catch {
    return false
  }
}

const hasExplicitScheme = (value: string): boolean => /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(value)

const normalizeIfExplicitUrl = (value: string): string | null => {
  if (!hasExplicitScheme(value)) return null
  try {
    return normalizeBaseUrl(value)
  } catch {
    return null
  }
}

const resolveImplicitKeychainAccounts = (baseUrl: string): string[] => {
  const normalized = normalizeBaseUrl(baseUrl)
  const parsed = new URL(normalized)
  parsed.pathname = "/"
  parsed.search = ""
  parsed.hash = ""
  const origin = parsed.toString().replace(/\/$/, "")
  return unique([normalized, origin, baseUrl.trim()])
}


export const resolveKeychainAccounts = (baseUrl: string, ref?: string): string[] => {
  const rawBaseUrl = baseUrl.trim()
  const implicitAccounts = resolveImplicitKeychainAccounts(rawBaseUrl)
  const trimmed = ref?.trim()
  if (!trimmed) return []
  if (trimmed === "keychain") return implicitAccounts
  if (trimmed.startsWith("keychain:")) {
    const account = trimmed.slice("keychain:".length).trim()
    if (!account) return implicitAccounts
    const normalizedAccount = normalizeIfExplicitUrl(account)
    if (!normalizedAccount) return [account]
    return isSameNormalizedBaseUrl(account, rawBaseUrl) ? unique([normalizedAccount, account]) : []
  }
  return []
}

export const resolveKeychainDeleteAccounts = (baseUrl: string, ref?: string): string[] => {
  const rawBaseUrl = baseUrl.trim()
  const trimmed = ref?.trim()
  if (trimmed?.startsWith("keychain:")) return resolveKeychainAccounts(rawBaseUrl, trimmed)
  return resolveKeychainAccounts(rawBaseUrl, "keychain")
}

export const clearKeychainAuthForBaseUrl = async (baseUrl: string): Promise<boolean> => {
  const existing = loadUserConfigSync()
  const sameBaseUrl = isSameNormalizedBaseUrl(existing.baseUrl, baseUrl)
  const keychainAccounts = sameBaseUrl
    ? resolveKeychainDeleteAccounts(baseUrl, existing.authTokenRef)
    : resolveKeychainAccounts(baseUrl, "keychain")
  const removeResults = await Promise.all(keychainAccounts.map((account) => deleteKeychainAuthToken(account)))
  const next = { ...existing }
  if (sameBaseUrl && resolveKeychainAccounts(baseUrl, next.authTokenRef).length > 0) {
    delete (next as { authTokenRef?: string }).authTokenRef
  }
  await writeUserConfig(next)
  return removeResults.some(Boolean)
}

export const resolveAuthToken = async (baseUrl: string): Promise<string | undefined> => {
  const envToken = process.env.BREADBOARD_API_TOKEN?.trim()
  if (envToken) return envToken

  const userConfig = loadUserConfigSync()
  const fileToken = userConfig.authToken?.trim()
  if (fileToken) return fileToken

  const rawBaseUrl = baseUrl.trim()
  const explicitAccounts = resolveKeychainAccounts(rawBaseUrl, userConfig.authTokenRef)
  const keychainEnabled = process.env.BREADBOARD_KEYCHAIN === "1"
  const accounts = explicitAccounts.length > 0 ? explicitAccounts : keychainEnabled ? resolveKeychainAccounts(rawBaseUrl, "keychain") : []

  for (const account of accounts) {
    const token = await getKeychainAuthToken(account)
    if (token) return token
  }
  return undefined
}

