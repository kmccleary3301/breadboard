import { homedir } from "node:os"
import path from "node:path"
import fs from "node:fs"
import { promises as fsp } from "node:fs"

export interface UserConfigFile {
  readonly baseUrl?: string
  readonly authToken?: string
  readonly engineVersion?: string
  readonly enginePath?: string
}

const resolveConfigPath = (): string => {
  const explicit = process.env.BREADBOARD_USER_CONFIG?.trim()
  if (explicit) {
    return path.resolve(explicit)
  }
  return path.join(homedir(), ".breadboard", "config.json")
}

export const getUserConfigPath = (): string => resolveConfigPath()

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

export const loadUserConfigSync = (): UserConfigFile => {
  const configPath = resolveConfigPath()
  try {
    if (!fs.existsSync(configPath)) return {}
    const raw = fs.readFileSync(configPath, "utf8")
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed)) return {}
    const baseUrl = typeof parsed.baseUrl === "string" ? parsed.baseUrl : undefined
    const authToken = typeof parsed.authToken === "string" ? parsed.authToken : undefined
    const engineVersion = typeof parsed.engineVersion === "string" ? parsed.engineVersion : undefined
    const enginePath = typeof parsed.enginePath === "string" ? parsed.enginePath : undefined
    return { baseUrl, authToken, engineVersion, enginePath }
  } catch {
    return {}
  }
}

export const writeUserConfig = async (next: UserConfigFile): Promise<void> => {
  const configPath = resolveConfigPath()
  await fsp.mkdir(path.dirname(configPath), { recursive: true })
  const payload = {
    ...(next.baseUrl ? { baseUrl: next.baseUrl } : {}),
    ...(next.authToken ? { authToken: next.authToken } : {}),
    ...(next.engineVersion ? { engineVersion: next.engineVersion } : {}),
    ...(next.enginePath ? { enginePath: next.enginePath } : {}),
  }
  await fsp.writeFile(configPath, JSON.stringify(payload, null, 2), "utf8")
}
