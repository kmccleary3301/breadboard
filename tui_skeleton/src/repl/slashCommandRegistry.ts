export type CommandVisibility = "visible" | "hidden" | "debug" | "experimental"
export type CommandAvailability = "available" | "disabled" | "feature-gated" | "context-gated"
export type SlashCommandDispatchKind = "ui-local" | "controller" | "debug-local" | "deferred"
export type SlashCommandArgumentKind = "string" | "enum" | "literal"

export interface SlashCommandArgumentSchema {
  readonly name: string
  readonly kind: SlashCommandArgumentKind
  readonly required?: boolean
  readonly variadic?: boolean
  readonly values?: readonly string[]
  readonly description?: string
}

export interface SlashCommandRuntimeContext {
  readonly pendingResponse?: boolean
  readonly disconnected?: boolean
}

export interface ResolvedSlashCommandAvailability {
  readonly availability: CommandAvailability
  readonly disabledReason?: string
}

export interface SlashCommandRegistryEntry {
  readonly id: string
  readonly name: string
  readonly aliases?: readonly string[]
  readonly summary: string
  readonly usage?: string
  readonly shortcut?: string
  readonly category: "session" | "navigation" | "context" | "model" | "workflow" | "debug" | "view"
  readonly order: number
  readonly visibility: CommandVisibility
  readonly availability: CommandAvailability
  readonly disabledReason?: string
  readonly dispatchKind: SlashCommandDispatchKind
  readonly historyBehavior: "record" | "skip"
  readonly argumentSchema?: readonly SlashCommandArgumentSchema[]
}

const entry = (value: Omit<SlashCommandRegistryEntry, "id" | "visibility" | "availability" | "historyBehavior" | "dispatchKind"> & Partial<Pick<SlashCommandRegistryEntry, "visibility" | "availability" | "historyBehavior" | "dispatchKind">>): SlashCommandRegistryEntry => ({
  id: value.name,
  visibility: "visible",
  availability: "available",
  dispatchKind: "controller",
  historyBehavior: "record",
  ...value,
})

export const SLASH_COMMAND_REGISTRY: readonly SlashCommandRegistryEntry[] = [
  entry({ name: "help", summary: "Show available slash commands.", category: "navigation", order: 10, dispatchKind: "ui-local", historyBehavior: "skip" }),
  entry({ name: "quit", summary: "Exit the session.", shortcut: "Ctrl+C ×2", category: "session", order: 20 }),
  entry({ name: "stop", summary: "Interrupt the current run (if any).", shortcut: "Esc", category: "session", order: 30 }),
  entry({ name: "clear", summary: "Clear view (history preserved).", shortcut: "Ctrl+Shift+C", category: "session", order: 40, dispatchKind: "controller" }),
  entry({ name: "status", summary: "Refresh session status.", category: "session", order: 50 }),
  entry({ name: "resume", summary: "Resume or recover the current session after interruption.", category: "session", order: 60, dispatchKind: "controller" }),
  entry({ name: "sessions", summary: "Open recent-session re-entry overlay.", category: "session", order: 61, dispatchKind: "ui-local" }),
  entry({ name: "fork", summary: "Fork the current session from a checkpoint.", category: "session", order: 62, visibility: "experimental", availability: "feature-gated", disabledReason: "/fork is deferred until backend session graph, checkpoint, and recovery semantics are first-class.", dispatchKind: "deferred" }),
  entry({ name: "new", summary: "Start a new clean session.", category: "session", order: 65, dispatchKind: "controller" }),
  entry({ name: "transcript", summary: "Open the transcript viewer.", shortcut: "Ctrl+T (Codex) / Ctrl+O (Claude)", category: "navigation", order: 70, dispatchKind: "ui-local" }),
  entry({ name: "raw", summary: "Open the raw transcript/event viewer.", category: "navigation", order: 72, dispatchKind: "ui-local" }),
  entry({ name: "copy", usage: "[transcript]", summary: "Export transcript content for copy/review.", category: "navigation", order: 74, dispatchKind: "ui-local", argumentSchema: [{ name: "target", kind: "enum", values: ["transcript"], description: "Optional export target." }] }),
  entry({
    name: "diff",
    usage: "[file|hunk|export|copy] [target]",
    summary: "Show, target, export, or copy working-tree diff; falls back to latest transcript diff.",
    category: "view",
    order: 76,
    visibility: "experimental",
    dispatchKind: "ui-local",
    argumentSchema: [
      { name: "action", kind: "enum", values: ["file", "hunk", "export", "copy"], description: "Optional diff action." },
      { name: "target", kind: "string", variadic: true, description: "Optional file index/path, hunk index, or export path." },
    ],
  }),
  entry({ name: "attach", aliases: ["mention"], summary: "Start the @ attach/file picker.", category: "context", order: 80, dispatchKind: "ui-local" }),
  entry({ name: "mention", aliases: ["attach"], summary: "Start the @ attach/file picker.", category: "context", order: 81, dispatchKind: "ui-local" }),
  entry({ name: "files", usage: "[path]", summary: "List files for the current session.", category: "context", order: 90, dispatchKind: "ui-local", argumentSchema: [{ name: "path", kind: "string", variadic: true, description: "Optional directory or file path." }] }),
  entry({ name: "models", aliases: ["model"], summary: "Open interactive model picker.", shortcut: "Ctrl+K", category: "model", order: 100, dispatchKind: "ui-local" }),
  entry({ name: "model", usage: "<id>", summary: "Switch to a specific model.", category: "model", order: 101, dispatchKind: "controller", argumentSchema: [{ name: "id", kind: "string", required: true, variadic: true, description: "Model id or alias." }] }),
  entry({ name: "permissions", summary: "Show read-only permission and approval status.", category: "session", order: 105, visibility: "experimental", dispatchKind: "ui-local" }),
  entry({ name: "skills", summary: "Open the skills picker.", shortcut: "Ctrl+G", category: "context", order: 110, dispatchKind: "ui-local" }),
  entry({ name: "todos", summary: "Open the todos panel.", shortcut: "Ctrl+T (Claude keymap)", category: "workflow", order: 120, dispatchKind: "ui-local" }),
  entry({ name: "tasks", summary: "Open the background tasks panel.", shortcut: "Ctrl+B", category: "workflow", order: 130, dispatchKind: "ui-local" }),
  entry({ name: "agents", summary: "Open the multiagent task dashboard.", category: "workflow", order: 135, visibility: "experimental", dispatchKind: "ui-local" }),
  entry({ name: "plan", summary: "Request plan-focused mode.", category: "workflow", order: 140 }),
  entry({ name: "goal", usage: "<objective>", summary: "Run toward an explicit long-lived goal.", category: "workflow", order: 145, visibility: "experimental", availability: "feature-gated", disabledReason: "/goal is deferred until BreadBoard defines durable goal persistence, stop conditions, and completion-audit semantics.", dispatchKind: "deferred", argumentSchema: [{ name: "objective", kind: "string", required: true, variadic: true, description: "Goal objective and stop condition." }] }),
  entry({ name: "mode", usage: "<plan|build|auto>", summary: "Switch the agent mode.", category: "workflow", order: 150, argumentSchema: [{ name: "mode", kind: "enum", required: true, values: ["plan", "build", "auto"], description: "Agent work mode." }] }),
  entry({ name: "test", usage: "[suite]", summary: "Trigger run_tests command.", category: "workflow", order: 160, argumentSchema: [{ name: "suite", kind: "string", variadic: true, description: "Optional test suite or selector." }] }),
  entry({ name: "rewind", summary: "Open checkpoint rewind picker.", category: "workflow", order: 170 }),
  entry({ name: "follow", usage: "[status|pause|resume|task status|task pause|task resume]", summary: "Control active-run or Task Focus follow behavior.", category: "view", order: 180, argumentSchema: [{ name: "target_or_action", kind: "enum", values: ["status", "pause", "resume", "task"], description: "Follow target or main transcript action." }, { name: "task_action", kind: "enum", values: ["status", "pause", "resume"], description: "Task Focus follow action when target is task." }] }),
  entry({ name: "todo-scope", usage: "[status|list|next|prev|set <key>]", summary: "Switch which todo scope is displayed.", shortcut: "Ctrl+U", category: "workflow", order: 190, argumentSchema: [{ name: "action", kind: "enum", values: ["status", "list", "next", "prev", "set"], description: "Todo scope action." }, { name: "key", kind: "string", description: "Scope key when action is set." }] }),
  entry({ name: "view", usage: "<collapse|scroll|markdown|raw|tools|reasoning> …", summary: "Adjust transcript, raw stream, or tool display modes.", category: "view", order: 200, argumentSchema: [{ name: "surface", kind: "enum", required: true, values: ["collapse", "scroll", "markdown", "raw", "tools", "reasoning"], description: "View surface to update." }, { name: "args", kind: "string", variadic: true, description: "Surface-specific arguments." }] }),
  entry({ name: "usage", summary: "Open the usage summary panel.", category: "debug", order: 210 }),
  entry({ name: "shortcuts", summary: "Open the shortcuts sheet.", shortcut: "?", category: "navigation", order: 220, historyBehavior: "skip" }),
  entry({ name: "ctree", usage: "[status|refresh|stage|previews|source] …", summary: "Refresh or configure the C-Tree view.", category: "context", order: 230, argumentSchema: [{ name: "action", kind: "enum", values: ["status", "refresh", "stage", "previews", "source"], description: "C-Tree action." }, { name: "args", kind: "string", variadic: true, description: "Action-specific arguments." }] }),
  entry({ name: "thinking", usage: "[summary|raw]", summary: "Show the retained thinking artifact for the active turn.", category: "debug", order: 240, argumentSchema: [{ name: "view", kind: "enum", values: ["summary", "raw"], description: "Thinking view." }] }),
  entry({ name: "runtime", usage: "[telemetry]", summary: "Show runtime telemetry and recent activity transitions.", category: "debug", order: 250, argumentSchema: [{ name: "view", kind: "enum", values: ["telemetry"], description: "Runtime report view." }] }),
  entry({ name: "engine", usage: "[status|logs|restart]", summary: "Show or control engine lifecycle, health, pid, logs, and recovery state.", category: "debug", order: 255, argumentSchema: [{ name: "view", kind: "enum", values: ["status", "logs", "restart"], description: "Engine report or control action." }] }),
  entry({ name: "doctor", summary: "Run lifecycle diagnostics and show recovery recommendations.", category: "debug", order: 256 }),
  entry({ name: "tool-display", usage: "list", summary: "List resolved tool display rules.", category: "debug", order: 260, argumentSchema: [{ name: "action", kind: "literal", required: true, values: ["list"], description: "List resolved rules." }] }),
  entry({ name: "inspect", usage: "[task|refresh|close]", summary: "Open the inspector panel or Task Focus inspector.", shortcut: "Ctrl+I", category: "debug", order: 270, argumentSchema: [{ name: "target", kind: "enum", values: ["task", "refresh", "close"], description: "Inspector target or action." }] }),
  entry({ name: "remote", usage: "on|off", summary: "Toggle remote streaming preference.", category: "debug", order: 280, argumentSchema: [{ name: "state", kind: "enum", required: true, values: ["on", "off"], description: "Remote streaming preference." }] }),
  entry({ name: "retry", summary: "Restart the current stream.", category: "session", order: 290 }),
  entry({ name: "debug-config", summary: "Print local TUI command/debug configuration.", category: "debug", order: 300, visibility: "debug", dispatchKind: "debug-local", historyBehavior: "skip" }),
]

export const getVisibleSlashCommandEntries = (): readonly SlashCommandRegistryEntry[] =>
  SLASH_COMMAND_REGISTRY.filter((command) => command.visibility === "visible").sort((a, b) => a.order - b.order)

export const getDispatchableSlashCommandEntries = (): readonly SlashCommandRegistryEntry[] =>
  SLASH_COMMAND_REGISTRY.filter((command) => command.visibility !== "hidden").sort((a, b) => a.order - b.order)

export const findSlashCommandEntry = (nameOrAlias: string): SlashCommandRegistryEntry | null => {
  const normalized = nameOrAlias.trim().replace(/^\//, "").toLowerCase()
  const entries = getDispatchableSlashCommandEntries()
  return entries.find((command) => command.name === normalized) ??
    entries.find((command) => command.aliases?.some((alias) => alias === normalized)) ??
    null
}

export const resolveSlashCommandAvailability = (
  entry: SlashCommandRegistryEntry,
  context: SlashCommandRuntimeContext = {},
): ResolvedSlashCommandAvailability => {
  if (entry.name === "stop" && !context.pendingResponse && !context.disconnected) {
    return { availability: "context-gated", disabledReason: "No running response to stop." }
  }
  return { availability: entry.availability, disabledReason: entry.disabledReason }
}

const scoreNearest = (candidate: string, query: string): number => {
  if (candidate === query) return 1_000
  if (candidate.startsWith(query) || query.startsWith(candidate)) return 500 - Math.abs(candidate.length - query.length)
  let score = 0
  let lastIndex = -1
  for (const char of query) {
    const index = candidate.indexOf(char, lastIndex + 1)
    if (index === -1) continue
    score += index === lastIndex + 1 ? 8 : 3
    lastIndex = index
  }
  return score - Math.abs(candidate.length - query.length)
}

export const findNearestSlashCommands = (nameOrAlias: string, limit = 3): readonly SlashCommandRegistryEntry[] => {
  const normalized = nameOrAlias.trim().replace(/^\//, "").toLowerCase()
  if (!normalized) return []
  return [...getVisibleSlashCommandEntries()]
    .map((command) => ({ command, score: scoreNearest(command.name, normalized) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score || a.command.name.localeCompare(b.command.name))
    .slice(0, limit)
    .map((item) => item.command)
}

export interface SlashCommandArgumentValidation {
  readonly ok: boolean
  readonly errorLines?: readonly string[]
}

const formatExpected = (arg: SlashCommandArgumentSchema): string => {
  if (arg.values && arg.values.length > 0) return arg.values.join("|")
  return arg.name
}

export const validateSlashCommandArguments = (
  entry: SlashCommandRegistryEntry,
  args: readonly string[],
): SlashCommandArgumentValidation => {
  const schema = entry.argumentSchema ?? []
  if (schema.length === 0) {
    return args.length === 0
      ? { ok: true }
      : { ok: false, errorLines: [`/${entry.name} does not take arguments.`, `Usage: /${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`] }
  }

  let argIndex = 0
  for (const spec of schema) {
    const remaining = args.slice(argIndex)
    const value = spec.variadic ? remaining.join(" ").trim() : remaining[0]
    if (spec.required && (!value || value.length === 0)) {
      return {
        ok: false,
        errorLines: [`Missing required argument <${spec.name}>.`, `Usage: /${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`],
      }
    }
    if (value && (spec.kind === "enum" || spec.kind === "literal")) {
      const firstToken = spec.variadic ? value : String(value)
      if (!spec.values?.includes(firstToken)) {
        return {
          ok: false,
          errorLines: [
            `Invalid ${spec.name}: ${firstToken}`,
            `Expected: ${formatExpected(spec)}`,
            `Usage: /${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`,
          ],
        }
      }
    }
    if (spec.variadic) {
      argIndex = args.length
      break
    }
    if (value) argIndex += 1
  }
  if (argIndex < args.length) {
    return {
      ok: false,
      errorLines: [`Too many arguments for /${entry.name}.`, `Usage: /${entry.name}${entry.usage ? ` ${entry.usage}` : ""}`],
    }
  }
  return { ok: true }
}
