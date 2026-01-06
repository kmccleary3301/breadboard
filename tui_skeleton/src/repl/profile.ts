import type { ChromeMode, KeymapMode } from "./modes.js"

export type ProfileName = "claude_v1" | "codex_v1" | "opencode_v1" | "breadboard_v1"

interface ProfileDefinition {
  readonly name: ProfileName
  readonly extends?: ProfileName
  readonly keymap?: KeymapMode
  readonly chrome?: ChromeMode
}

const normalizeProfile = (value: string | undefined): ProfileName | null => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "claude" || normalized === "claude_v1") return "claude_v1"
  if (normalized === "codex" || normalized === "codex_v1") return "codex_v1"
  if (normalized === "opencode" || normalized === "opencode_v1") return "opencode_v1"
  if (normalized === "breadboard" || normalized === "breadboard_v1") return "breadboard_v1"
  return null
}

const PROFILE_DEFS: Record<ProfileName, ProfileDefinition> = {
  claude_v1: { name: "claude_v1", keymap: "claude", chrome: "claude" },
  codex_v1: { name: "codex_v1", keymap: "codex", chrome: "codex" },
  opencode_v1: { name: "opencode_v1", keymap: "codex", chrome: "codex" },
  breadboard_v1: { name: "breadboard_v1", extends: "claude_v1" },
}

const resolveProfileDefinition = (name: ProfileName): { keymap: KeymapMode; chrome: ChromeMode } => {
  const visited = new Set<ProfileName>()
  let current: ProfileDefinition | undefined = PROFILE_DEFS[name]
  let keymap: KeymapMode | undefined
  let chrome: ChromeMode | undefined
  while (current && !visited.has(current.name)) {
    visited.add(current.name)
    if (!keymap && current.keymap) keymap = current.keymap
    if (!chrome && current.chrome) chrome = current.chrome
    current = current.extends ? PROFILE_DEFS[current.extends] : undefined
  }
  return {
    keymap: keymap ?? "claude",
    chrome: chrome ?? "claude",
  }
}

export interface ResolvedProfile {
  readonly name: ProfileName
  readonly keymap: KeymapMode
  readonly chrome: ChromeMode
}

export const loadProfileConfig = (): ResolvedProfile => {
  const explicit =
    normalizeProfile(process.env.BREADBOARD_TUI_PROFILE) ??
    normalizeProfile(process.env.BREADBOARD_PROFILE)
  const profile = explicit ?? "claude_v1"
  const resolved = resolveProfileDefinition(profile)
  return { name: profile, ...resolved }
}

