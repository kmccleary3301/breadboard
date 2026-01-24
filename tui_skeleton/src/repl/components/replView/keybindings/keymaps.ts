import type { Key } from "ink"

export type KeyMatch = (input: string, key: Key) => boolean

export interface KeymapEntry {
  readonly match: KeyMatch
  readonly action: () => boolean
}

export const runKeymap = (entries: ReadonlyArray<KeymapEntry>, input: string, key: Key): boolean => {
  for (const entry of entries) {
    if (entry.match(input, key)) {
      return entry.action()
    }
  }
  return false
}

export const matchCtrl = (char: string): KeyMatch => (input, key) =>
  key.ctrl && input?.toLowerCase() === char.toLowerCase()

export const matchChar = (char: string): KeyMatch => (input) => input === char

export const matchEscape = (): KeyMatch => (input, key) => key.escape || input === "\u001b"

export const matchReturn = (): KeyMatch => (input, key) => key.return || input === "\r" || input === "\n"

export const matchTab = (): KeyMatch => (input, key) =>
  key.tab || (typeof input === "string" && (input.includes("\t") || input.includes("\u001b[Z")))
