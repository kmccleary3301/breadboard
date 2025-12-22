export type FileMentionMode = "reference" | "inline" | "auto"

export interface FileMentionConfig {
  readonly mode: FileMentionMode
  readonly insertPath: boolean
  readonly maxInlineBytesPerFile: number
  readonly maxInlineBytesTotal: number
  readonly snippetMaxBytes: number
  readonly snippetHeadLines: number
  readonly snippetTailLines: number
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

const parseModeEnv = (value: string | undefined): FileMentionMode => {
  const normalized = value?.trim().toLowerCase()
  if (normalized === "reference" || normalized === "ref") return "reference"
  if (normalized === "inline" || normalized === "full") return "inline"
  return "auto"
}

export const loadFileMentionConfig = (): FileMentionConfig => ({
  mode: parseModeEnv(process.env.BREADBOARD_TUI_FILE_MENTION_MODE),
  insertPath: parseBooleanEnv(process.env.BREADBOARD_TUI_FILE_MENTION_INSERT_PATH, true),
  maxInlineBytesPerFile: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_MENTION_MAX_INLINE_BYTES_PER_FILE, 200_000),
  maxInlineBytesTotal: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_MENTION_MAX_INLINE_BYTES_TOTAL, 600_000),
  snippetMaxBytes: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_MENTION_SNIPPET_MAX_BYTES, 80_000),
  snippetHeadLines: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_MENTION_SNIPPET_HEAD_LINES, 200),
  snippetTailLines: parseNumberEnv(process.env.BREADBOARD_TUI_FILE_MENTION_SNIPPET_TAIL_LINES, 80),
})

