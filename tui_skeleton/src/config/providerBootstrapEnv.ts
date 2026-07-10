import fs from "node:fs"
import { homedir } from "node:os"
import path from "node:path"

const CODEX_AUTH_PATH = path.join(homedir(), ".codex", "auth.json")

const findCodexToken = (value: unknown, seen = new Set<unknown>()): string | null => {
  if (!value || typeof value !== "object") return null
  if (seen.has(value)) return null
  seen.add(value)
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findCodexToken(item, seen)
      if (found) return found
    }
    return null
  }
  const record = value as Record<string, unknown>
  for (const key of ["OPENAI_API_KEY", "codex_access_token", "access_token", "id_token", "token", "auth_token"]) {
    const candidate = record[key]
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim()
    }
  }
  for (const nested of Object.values(record)) {
    const found = findCodexToken(nested, seen)
    if (found) return found
  }
  return null
}

export const bootstrapProviderEnvironment = (): void => {
  if (process.env.OPENAI_API_KEY?.trim()) {
    return
  }
  try {
    if (!fs.existsSync(CODEX_AUTH_PATH)) return
    const payload = JSON.parse(fs.readFileSync(CODEX_AUTH_PATH, "utf8")) as unknown
    const token = findCodexToken(payload)
    if (token) {
      process.env.OPENAI_API_KEY = token
    }
  } catch {
    // Best-effort bootstrap only.
  }
}
