export type FilePickerMode = "tree" | "fuzzy" | "hybrid"

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
  mode: parseModeEnv(process.env.KYLECODE_TUI_FILE_PICKER_MODE),
  maxResults: parseNumberEnv(process.env.KYLECODE_TUI_FILE_PICKER_MAX_RESULTS, 12),
  maxIndexFiles: parseNumberEnv(process.env.KYLECODE_TUI_FILE_PICKER_MAX_INDEX_FILES, 50_000),
  indexNodeModules: parseBooleanEnv(process.env.KYLECODE_TUI_FILE_PICKER_INDEX_NODE_MODULES, false),
  indexHiddenDirs: parseBooleanEnv(process.env.KYLECODE_TUI_FILE_PICKER_INDEX_HIDDEN_DIRS, false),
  indexConcurrency: parseNumberEnv(process.env.KYLECODE_TUI_FILE_PICKER_INDEX_CONCURRENCY, 4),
})

