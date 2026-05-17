import {
  getVisibleSlashCommandEntries,
  resolveSlashCommandAvailability,
  SLASH_COMMAND_REGISTRY,
  type SlashCommandRegistryEntry,
} from "./slashCommandRegistry.js"

export interface SlashCommandInfo {
  readonly name: string
  readonly summary: string
  readonly usage?: string
  readonly shortcut?: string
  readonly category?: SlashCommandRegistryEntry["category"]
  readonly availability?: SlashCommandRegistryEntry["availability"]
  readonly disabledReason?: string
  readonly visibility?: SlashCommandRegistryEntry["visibility"]
  readonly dispatchKind?: SlashCommandRegistryEntry["dispatchKind"]
}

export interface SlashSuggestion {
  readonly command: string
  readonly usage?: string
  readonly summary: string
  readonly shortcut?: string
  readonly availability?: SlashCommandRegistryEntry["availability"]
  readonly disabledReason?: string
}

export { SLASH_COMMAND_REGISTRY }

export const SLASH_COMMANDS: ReadonlyArray<SlashCommandInfo> = getVisibleSlashCommandEntries().map((entry) => ({
  name: entry.name,
  summary: entry.summary,
  usage: entry.usage,
  shortcut: entry.shortcut,
  category: entry.category,
  availability: entry.availability,
  disabledReason: entry.disabledReason,
  visibility: entry.visibility,
  dispatchKind: entry.dispatchKind,
}))

export const SLASH_COMMAND_HINT = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`).join(", ")

const DEFAULT_SUGGESTION_ORDER = ["resume", "transcript", "attach", "models", "shortcuts"] as const

const scoreFuzzy = (candidate: string, query: string): number | null => {
  const needle = query.trim().toLowerCase()
  if (!needle) return 0
  const haystack = candidate.toLowerCase()
  let score = 0
  let lastIndex = -1
  let consecutive = 0
  for (let i = 0; i < needle.length; i += 1) {
    const ch = needle[i]
    if (!ch) continue
    const index = haystack.indexOf(ch, lastIndex + 1)
    if (index === -1) return null
    score += 10
    if (index === lastIndex + 1) {
      consecutive += 1
      score += 8 + consecutive
    } else {
      consecutive = 0
      score -= Math.max(0, index - lastIndex - 1)
    }
    lastIndex = index
  }
  score += Math.max(0, 24 - haystack.length)
  return score
}

export interface BuildSuggestionsOptions {
  readonly pendingResponse?: boolean
}

const applyRuntimeAvailability = (
  entry: SlashCommandInfo,
  options: BuildSuggestionsOptions,
): Pick<SlashSuggestion, "availability" | "disabledReason"> => {
  const registryEntry = SLASH_COMMAND_REGISTRY.find((command) => command.name === entry.name)
  if (!registryEntry) return { availability: entry.availability, disabledReason: entry.disabledReason }
  return resolveSlashCommandAvailability(registryEntry, options)
}

const suggestionFromEntry = (entry: SlashCommandInfo, options: BuildSuggestionsOptions): SlashSuggestion => ({
  command: `/${entry.name}`,
  usage: entry.usage,
  summary: entry.summary,
  shortcut: entry.shortcut,
  ...applyRuntimeAvailability(entry, options),
})

export const buildSuggestions = (input: string, limit = 5, options: BuildSuggestionsOptions = {}): SlashSuggestion[] => {
  if (!input.startsWith("/")) return []
  const [lookup] = input.slice(1).split(/\s+/)
  const needle = lookup.toLowerCase()
  const exact = SLASH_COMMANDS.some((entry) => entry.name === needle)
  if (exact && input.trim() === `/${needle}`) return []
  if (!needle) {
    const defaultEntries = DEFAULT_SUGGESTION_ORDER.map((name) => SLASH_COMMANDS.find((entry) => entry.name === name)).filter(
      (entry): entry is SlashCommandInfo => entry != null,
    )
    return defaultEntries.slice(0, limit).map((entry) => suggestionFromEntry(entry, options))
  }
  const scored = SLASH_COMMANDS.map((entry) => {
    const score = scoreFuzzy(entry.name, needle)
    return score == null ? null : { entry, score }
  }).filter((item): item is { entry: SlashCommandInfo; score: number } => item != null)
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score
    if (a.entry.name.length !== b.entry.name.length) return a.entry.name.length - b.entry.name.length
    return a.entry.name.localeCompare(b.entry.name)
  })
  return scored.slice(0, limit).map(({ entry }) => suggestionFromEntry(entry, options))
}
