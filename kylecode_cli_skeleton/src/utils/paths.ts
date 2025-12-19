import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const findUpward = (startDir: string, relativePath: string): string | null => {
  let current = path.resolve(startDir)
  for (;;) {
    const candidate = path.join(current, relativePath)
    if (existsSync(candidate)) return candidate
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  return null
}

export const resolveKylecodePath = (value: string): string => {
  const trimmed = value.trim()
  if (!trimmed) {
    throw new Error("Path is empty.")
  }
  if (path.isAbsolute(trimmed)) return trimmed

  const cwdCandidate = path.resolve(process.cwd(), trimmed)
  if (existsSync(cwdCandidate)) return cwdCandidate

  const fromCwd = findUpward(process.cwd(), trimmed)
  if (fromCwd) return fromCwd

  const moduleDir = path.dirname(fileURLToPath(import.meta.url))
  const fromModule = findUpward(moduleDir, trimmed)
  if (fromModule) return fromModule

  return cwdCandidate
}

export const resolveKylecodeWorkspace = (value?: string | null): string => {
  if (!value || !value.trim()) {
    return process.cwd()
  }
  return resolveKylecodePath(value)
}

