import type { SessionFileInfo } from "../../../../../api/types.js"
import { GLYPHS } from "../../theme.js"
import { formatFileListLines } from "../../utils/format.js"
import { stripCommandQuotes } from "../../utils/text.js"

export type AtCommandKind = "list" | "read"

const AT_COMMAND_ALIASES: Record<AtCommandKind, ReadonlyArray<string>> = {
  list: ["list", "ls", "files"],
  read: ["read", "cat"],
}

const AT_COMMAND_ALIAS_MAP = (() => {
  const map = new Map<string, AtCommandKind>()
  for (const kind of Object.keys(AT_COMMAND_ALIASES) as AtCommandKind[]) {
    for (const alias of AT_COMMAND_ALIASES[kind]) {
      map.set(alias, kind)
    }
  }
  return map
})()

export const MAX_COMMAND_LINES = 64

export const parseAtCommand = (value: string): { readonly kind: AtCommandKind; readonly argument?: string } | null => {
  const trimmedStart = value.trimStart()
  if (!trimmedStart.startsWith("@")) return null
  const afterAt = trimmedStart.slice(1)
  const match = afterAt.match(/^([a-zA-Z]+)\b/)
  if (!match) return null
  const alias = match[1].toLowerCase()
  const kind = AT_COMMAND_ALIAS_MAP.get(alias)
  if (!kind) return null
  const remainder = afterAt.slice(match[0].length)
  const argument = remainder.trim()
  const parsedArgument = argument.length > 0 ? stripCommandQuotes(argument) ?? argument : undefined
  return { kind, argument: parsedArgument }
}

export const clampCommandLines = (lines: string[], fallback?: string): string[] => {
  if (lines.length <= MAX_COMMAND_LINES) return lines
  const trimmed = lines.slice(0, Math.max(0, MAX_COMMAND_LINES - 1))
  const suffix = fallback ?? `${GLYPHS.ellipsis}Command output truncated for readability.`
  return [...trimmed, suffix]
}

export const formatListCommandLines = (files: SessionFileInfo[]): string[] => formatFileListLines(files)
