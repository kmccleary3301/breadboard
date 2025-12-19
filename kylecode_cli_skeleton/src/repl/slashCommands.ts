export interface SlashCommandInfo {
  readonly name: string
  readonly summary: string
  readonly usage?: string
}

export interface SlashSuggestion {
  readonly command: string
  readonly usage?: string
  readonly summary: string
}

export const SLASH_COMMANDS: ReadonlyArray<SlashCommandInfo> = [
  { name: "help", summary: "Show available slash commands." },
  { name: "quit", summary: "Exit the session." },
  { name: "stop", summary: "Interrupt the current run (if any)." },
  { name: "clear", summary: "Clear conversation and tool panes." },
  { name: "status", summary: "Refresh session status." },
  { name: "remote", usage: "on|off", summary: "Toggle remote streaming preference." },
  { name: "retry", summary: "Restart the current stream." },
  { name: "plan", summary: "Request plan-focused mode." },
  { name: "mode", usage: "<plan|build|auto>", summary: "Switch the agent mode." },
  { name: "model", usage: "<id>", summary: "Switch to a specific model." },
  { name: "test", usage: "[suite]", summary: "Trigger run_tests command." },
  { name: "files", usage: "[path]", summary: "List files for the current session." },
  { name: "models", summary: "Open interactive model picker." },
  { name: "rewind", summary: "Open checkpoint rewind picker." },
  { name: "view", usage: "<collapse|scroll|markdown> …", summary: "Adjust transcript collapse, scroll, or markdown modes." },
]

export const SLASH_COMMAND_HINT = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`).join(", ")

export const buildSuggestions = (input: string, limit = 5): SlashSuggestion[] => {
  if (!input.startsWith("/")) return []
  const [lookup] = input.slice(1).split(/\s+/)
  const needle = lookup.toLowerCase()
  return SLASH_COMMANDS.filter((entry) => entry.name.startsWith(needle))
    .slice(0, limit)
    .map((entry) => ({
      command: `/${entry.name}`,
      usage: entry.usage,
      summary: entry.summary,
    }))
}
