import type { Key } from "ink"

export type KeyProfile = "claude" | "codex" | "breadboard"

export type ActionId =
  | "toggle_transcript_viewer"
  | "toggle_todos_panel"
  | "toggle_tasks_panel"
  | "toggle_ctree_panel"
  | "toggle_skills_panel"
  | "toggle_palette"
  | "clear_screen"
  | "quit_hard"
  | "inspect_panel"

type KeyBinding = {
  readonly key: string
  readonly ctrl?: boolean
  readonly shift?: boolean
  readonly meta?: boolean
}

type ActionBindingTable = Record<ActionId, ReadonlyArray<KeyBinding>>

const SHARED_BINDINGS: ActionBindingTable = {
  toggle_transcript_viewer: [{ ctrl: true, shift: true, key: "t" }],
  toggle_todos_panel: [{ ctrl: true, key: "t" }],
  toggle_tasks_panel: [{ ctrl: true, key: "b" }],
  toggle_ctree_panel: [{ ctrl: true, key: "y" }],
  toggle_skills_panel: [{ ctrl: true, key: "g" }],
  toggle_palette: [{ ctrl: true, key: "p" }],
  clear_screen: [{ ctrl: true, key: "l" }],
  quit_hard: [{ ctrl: true, key: "d" }],
  inspect_panel: [{ ctrl: true, key: "i" }],
}

// Today profiles intentionally share physical keybindings.
// Keeping explicit profile tables allows future profile-specific remaps without touching handlers.
export const ACTION_KEYMAP: Record<KeyProfile, ActionBindingTable> = {
  claude: SHARED_BINDINGS,
  codex: SHARED_BINDINGS,
  breadboard: SHARED_BINDINGS,
}

export const normalizeKeyProfile = (value: unknown): KeyProfile => {
  const normalized = String(value ?? "")
    .trim()
    .toLowerCase()
  if (normalized === "claude") return "claude"
  if (normalized === "codex") return "codex"
  return "breadboard"
}

const bindingMatches = (char: string | undefined, key: Key, binding: KeyBinding): boolean => {
  const lower = (char ?? "").toLowerCase()
  const expectedKey = binding.key.toLowerCase()
  const expectedCtrl = binding.ctrl === true
  const expectedShift = binding.shift === true
  const expectedMeta = binding.meta === true
  if (Boolean(key.ctrl) !== expectedCtrl) return false
  if (Boolean(key.shift) !== expectedShift) return false
  if (Boolean(key.meta) !== expectedMeta) return false
  return lower === expectedKey
}

export const matchesActionBinding = (
  profile: KeyProfile,
  actionId: ActionId,
  char: string | undefined,
  key: Key,
): boolean => {
  const bindings = ACTION_KEYMAP[profile][actionId]
  return bindings.some((binding) => bindingMatches(char, key, binding))
}

