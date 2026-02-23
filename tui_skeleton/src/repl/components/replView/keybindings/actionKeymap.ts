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

type NormalizedInputBinding = {
  readonly key: string
  readonly ctrl: boolean
  readonly shift: boolean
  readonly meta: boolean
}

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

const LETTER_RE = /^[a-z]$/i
const CSI_U_RE = /^\u001b\[(\d+);(\d+)u$/
const XTERM_MOD_OTHER_KEYS_RE = /^\u001b\[27;(\d+);(\d+)~$/
const XTERM_MOD_LETTER_RE = /^\u001b\[1;(\d+)([A-Za-z])$/
const ALT_LETTER_RE = /^\u001b([A-Za-z])$/

const decodeModifierFlags = (raw: number): { ctrl: boolean; shift: boolean; meta: boolean } => {
  const value = Number.isFinite(raw) ? Math.max(1, Math.trunc(raw)) : 1
  const flags = Math.max(0, value - 1)
  return {
    shift: Boolean(flags & 1),
    meta: Boolean(flags & 2),
    ctrl: Boolean(flags & 4),
  }
}

const normalizeFromCodePoint = (
  codePoint: number,
  modifiers: { ctrl: boolean; shift: boolean; meta: boolean },
): NormalizedInputBinding | null => {
  if (!Number.isFinite(codePoint) || codePoint <= 0 || codePoint > 0x10ffff) return null
  const ch = String.fromCodePoint(codePoint).toLowerCase()
  if (!LETTER_RE.test(ch)) return null
  return {
    key: ch,
    ctrl: modifiers.ctrl,
    shift: modifiers.shift,
    meta: modifiers.meta,
  }
}

const normalizeControlByte = (char: string): NormalizedInputBinding | null => {
  if (char.length !== 1) return null
  const code = char.charCodeAt(0)
  if (code >= 1 && code <= 26) {
    return {
      key: String.fromCharCode(code + 96),
      ctrl: true,
      shift: false,
      meta: false,
    }
  }
  return null
}

const normalizeExtendedEscapeSequence = (char: string): NormalizedInputBinding | null => {
  const csiU = char.match(CSI_U_RE)
  if (csiU) {
    const codePoint = Number.parseInt(csiU[1] ?? "", 10)
    const modifiers = decodeModifierFlags(Number.parseInt(csiU[2] ?? "", 10))
    return normalizeFromCodePoint(codePoint, modifiers)
  }

  const modOtherKeys = char.match(XTERM_MOD_OTHER_KEYS_RE)
  if (modOtherKeys) {
    const modifiers = decodeModifierFlags(Number.parseInt(modOtherKeys[1] ?? "", 10))
    const codePoint = Number.parseInt(modOtherKeys[2] ?? "", 10)
    return normalizeFromCodePoint(codePoint, modifiers)
  }

  const modLetter = char.match(XTERM_MOD_LETTER_RE)
  if (modLetter) {
    const modifiers = decodeModifierFlags(Number.parseInt(modLetter[1] ?? "", 10))
    const letter = (modLetter[2] ?? "").toLowerCase()
    if (!LETTER_RE.test(letter)) return null
    return {
      key: letter,
      ctrl: modifiers.ctrl,
      shift: modifiers.shift,
      meta: modifiers.meta,
    }
  }

  const altLetter = char.match(ALT_LETTER_RE)
  if (altLetter) {
    const letter = (altLetter[1] ?? "").toLowerCase()
    if (!LETTER_RE.test(letter)) return null
    return {
      key: letter,
      ctrl: false,
      shift: false,
      meta: true,
    }
  }

  return null
}

const normalizeInputBinding = (char: string | undefined, key: Key): NormalizedInputBinding | null => {
  const input = typeof char === "string" ? char : ""
  const lower = input.toLowerCase()
  const keyName =
    typeof (key as Record<string, unknown>).name === "string"
      ? String((key as Record<string, unknown>).name).toLowerCase()
      : ""

  if (key.ctrl || key.shift || key.meta) {
    if (LETTER_RE.test(lower)) {
      return {
        key: lower,
        ctrl: Boolean(key.ctrl),
        shift: Boolean(key.shift),
        meta: Boolean(key.meta),
      }
    }
    if (LETTER_RE.test(keyName)) {
      return {
        key: keyName,
        ctrl: Boolean(key.ctrl),
        shift: Boolean(key.shift),
        meta: Boolean(key.meta),
      }
    }
  }

  if (LETTER_RE.test(lower)) {
    return {
      key: lower,
      ctrl: false,
      shift: false,
      meta: false,
    }
  }

  const controlByte = normalizeControlByte(input)
  if (controlByte) return controlByte

  if (input.includes("\u001b")) {
    const escaped = normalizeExtendedEscapeSequence(input)
    if (escaped) return escaped
  }

  return null
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
  const normalized = normalizeInputBinding(char, key)
  if (!normalized) return false
  const expectedKey = binding.key.toLowerCase().trim()
  const expectedCtrl = binding.ctrl === true
  const expectedShift = binding.shift === true
  const expectedMeta = binding.meta === true
  if (normalized.ctrl !== expectedCtrl) return false
  if (normalized.shift !== expectedShift) return false
  if (normalized.meta !== expectedMeta) return false
  return normalized.key === expectedKey
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
