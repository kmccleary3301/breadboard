import type { ChromeMode, KeymapMode } from "./modes.js"
import { loadProfileConfig } from "./profile.js"

const normalizeChrome = (value: string | undefined): ChromeMode | null => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "claude") return "claude"
  if (normalized === "codex") return "codex"
  return null
}

export const loadChromeMode = (fallback: KeymapMode): ChromeMode => {
  const explicit = normalizeChrome(process.env.BREADBOARD_TUI_CHROME)
  if (explicit) return explicit
  return loadProfileConfig().chrome ?? fallback
}
