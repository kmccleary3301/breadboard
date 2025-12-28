export type KeymapMode = "claude" | "codex"

const normalizeKeymap = (value: string | undefined): KeymapMode | null => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "claude") return "claude"
  if (normalized === "codex") return "codex"
  return null
}

export const loadKeymapConfig = (): KeymapMode => {
  const explicit =
    normalizeKeymap(process.env.BREADBOARD_TUI_KEYMAP) ??
    normalizeKeymap(process.env.BREADBOARD_KEYMAP)
  return explicit ?? "claude"
}

