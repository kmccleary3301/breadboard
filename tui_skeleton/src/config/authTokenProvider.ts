import { loadUserConfigSync } from "./userConfig.js"
import { getKeychainAuthToken } from "./keychain.js"

const resolveKeychainAccount = (baseUrl: string, ref?: string): string | null => {
  const trimmed = ref?.trim()
  if (!trimmed) return null
  if (trimmed === "keychain") return baseUrl
  if (trimmed.startsWith("keychain:")) {
    const account = trimmed.slice("keychain:".length).trim()
    return account ? account : baseUrl
  }
  return null
}

export const resolveAuthToken = async (baseUrl: string): Promise<string | undefined> => {
  const envToken = process.env.BREADBOARD_API_TOKEN?.trim()
  if (envToken) return envToken

  const userConfig = loadUserConfigSync()
  const fileToken = userConfig.authToken?.trim()
  if (fileToken) return fileToken

  const explicitAccount = resolveKeychainAccount(baseUrl, userConfig.authTokenRef)
  const keychainEnabled = process.env.BREADBOARD_KEYCHAIN === "1"
  const account = explicitAccount ?? (keychainEnabled ? baseUrl : null)
  if (!account) return undefined

  return await getKeychainAuthToken(account)
}

