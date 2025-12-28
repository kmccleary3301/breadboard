import type { KeymapMode } from "./keymap.js"

export type ChromeMode = "claude" | "codex"

const normalizeChrome = (value: string | undefined): ChromeMode | null => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "claude") return "claude"
  if (normalized === "codex") return "codex"
  return null
}

export const loadChromeMode = (fallback: KeymapMode): ChromeMode =>
  normalizeChrome(process.env.BREADBOARD_TUI_CHROME) ?? fallback
