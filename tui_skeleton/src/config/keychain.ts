import { createRequire } from "node:module"

type KeytarModule = {
  getPassword(service: string, account: string): Promise<string | null>
  setPassword(service: string, account: string, password: string): Promise<void>
  deletePassword(service: string, account: string): Promise<boolean>
}

const SERVICE = "breadboard"
const require = createRequire(import.meta.url)

const loadKeytar = async (): Promise<KeytarModule | null> => {
  try {
    const mod = require("keytar") as unknown as KeytarModule
    if (typeof mod?.getPassword !== "function") return null
    return mod
  } catch {
    return null
  }
}

export const getKeychainAuthToken = async (account: string): Promise<string | undefined> => {
  const keytar = await loadKeytar()
  if (!keytar) return undefined
  const token = await keytar.getPassword(SERVICE, account)
  return token ?? undefined
}

export const setKeychainAuthToken = async (account: string, token: string): Promise<void> => {
  const keytar = await loadKeytar()
  if (!keytar) {
    throw new Error(
      "Keychain support is not available (optional dependency `keytar` missing). Install it or use BREADBOARD_API_TOKEN.",
    )
  }
  await keytar.setPassword(SERVICE, account, token)
}

export const deleteKeychainAuthToken = async (account: string): Promise<boolean> => {
  const keytar = await loadKeytar()
  if (!keytar) return false
  return await keytar.deletePassword(SERVICE, account)
}

export const isKeychainAvailable = async (): Promise<boolean> => {
  return (await loadKeytar()) !== null
}
