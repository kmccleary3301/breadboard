export interface SlashCommandInfo {
  readonly name: string
  readonly summary: string
  readonly usage?: string
  readonly shortcut?: string
}

export interface SlashSuggestion {
  readonly command: string
  readonly usage?: string
  readonly summary: string
  readonly shortcut?: string
}

export const SLASH_COMMANDS: ReadonlyArray<SlashCommandInfo> = [
  { name: "help", summary: "Show available slash commands." },
  { name: "quit", summary: "Exit the session.", shortcut: "Ctrl+C ×2" },
  { name: "stop", summary: "Interrupt the current run (if any).", shortcut: "Esc" },
  { name: "clear", summary: "Clear view (history preserved).", shortcut: "Ctrl+Shift+C" },
  { name: "status", summary: "Refresh session status." },
  { name: "remote", usage: "on|off", summary: "Toggle remote streaming preference." },
  { name: "retry", summary: "Restart the current stream." },
  { name: "plan", summary: "Request plan-focused mode." },
  { name: "mode", usage: "<plan|build|auto>", summary: "Switch the agent mode." },
  { name: "model", usage: "<id>", summary: "Switch to a specific model." },
  { name: "test", usage: "[suite]", summary: "Trigger run_tests command." },
  { name: "files", usage: "[path]", summary: "List files for the current session." },
  { name: "models", summary: "Open interactive model picker.", shortcut: "Ctrl+K" },
  { name: "rewind", summary: "Open checkpoint rewind picker." },
  { name: "todos", summary: "Open the todos panel.", shortcut: "Ctrl+T (Claude keymap)" },
  { name: "usage", summary: "Open the usage summary panel." },
  { name: "tasks", summary: "Open the background tasks panel.", shortcut: "Ctrl+B" },
  { name: "transcript", summary: "Open the transcript viewer.", shortcut: "Ctrl+T / Ctrl+Shift+T" },
  { name: "view", usage: "<collapse|scroll|markdown> …", summary: "Adjust transcript collapse, scroll, or markdown modes." },
]

export const SLASH_COMMAND_HINT = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`).join(", ")

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

export const buildSuggestions = (input: string, limit = 5): SlashSuggestion[] => {
  if (!input.startsWith("/")) return []
  const [lookup] = input.slice(1).split(/\s+/)
  const needle = lookup.toLowerCase()
  if (!needle) {
  return SLASH_COMMANDS.slice(0, limit).map((entry) => ({
    command: `/${entry.name}`,
    usage: entry.usage,
    summary: entry.summary,
    shortcut: entry.shortcut,
  }))
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
  return scored.slice(0, limit).map(({ entry }) => ({
    command: `/${entry.name}`,
    usage: entry.usage,
    summary: entry.summary,
    shortcut: entry.shortcut,
  }))
}
