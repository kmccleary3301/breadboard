import { existsSync, readFileSync } from "node:fs"
import { resolveBreadboardPath } from "../utils/paths.js"

export type FilePickerMode = "tree" | "fuzzy" | "hybrid"

export interface FilePickerResource {
  readonly label: string
  readonly detail?: string
}

export interface FilePickerConfig {
  readonly mode: FilePickerMode
  readonly maxResults: number
  readonly maxIndexFiles: number
  readonly indexNodeModules: boolean
  readonly indexHiddenDirs: boolean
  readonly indexConcurrency: number
}

const parseBooleanEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const parseNumberEnv = (value: string | undefined, fallback: number): number => {
  if (value == null) return fallback
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.floor(parsed)
}

const parseModeEnv = (value: string | undefined): FilePickerMode => {
  const normalized = value?.trim().toLowerCase()
  if (normalized === "tree" || normalized === "dir" || normalized === "directory") return "tree"
  if (normalized === "hybrid") return "hybrid"
  return "fuzzy"
}

export const loadFilePickerConfig = (): FilePickerConfig => ({
  mode: parseModeEnv(process.env.BREADBOARD_TUI_FILE_PICKER_MODE),
  maxResults: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_RESULTS, 12),
  maxIndexFiles: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_PICKER_MAX_INDEX_FILES, 50_000),
  indexNodeModules: parseBooleanEnv(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_NODE_MODULES, false),
  indexHiddenDirs: parseBooleanEnv(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_HIDDEN_DIRS, false),
  indexConcurrency: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_PICKER_INDEX_CONCURRENCY, 4),
})

const parseResourcesJson = (raw: string): FilePickerResource[] => {
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const resources: FilePickerResource[] = []
    for (const entry of parsed) {
      if (!entry || typeof entry !== "object") continue
      const record = entry as Record<string, unknown>
      const label = typeof record.label === "string" ? record.label.trim() : ""
      if (!label) continue
      const detail = typeof record.detail === "string" ? record.detail.trim() : undefined
      resources.push({ label, detail })
    }
    return resources
  } catch {
    return []
  }
}

export const loadFilePickerResources = (): FilePickerResource[] => {
  const inline = process.env.BREADBOARD_TUI_FILE_PICKER_RESOURCES_JSON
  if (inline && inline.trim().length > 0) {
    return parseResourcesJson(inline)
  }
  const pathValue = process.env.BREADBOARD_TUI_FILE_PICKER_RESOURCES_PATH
  if (!pathValue || !pathValue.trim()) return []
  const resolved = resolveBreadboardPath(pathValue.trim())
  if (!existsSync(resolved)) return []
  try {
    const contents = readFileSync(resolved, "utf8")
    return parseResourcesJson(contents)
  } catch {
    return []
  }
}
